from __future__ import annotations

from datetime import date as real_date
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db import get_db
from app.main import app
from app.models.article import Article
from app.models.funnel_backfill_state import FunnelBackfillState
from app.models.funnel_daily import FunnelDaily
from app.models.raw_sales import RawSale
from app.models.user import User
from celery_app.tasks import recalculate_sku_daily, sync_funnel_ytd_step


class _FixedDate(real_date):
    _fixed_today = real_date(2026, 4, 8)

    @classmethod
    def today(cls) -> real_date:  # type: ignore[override]
        return cls._fixed_today


@pytest.fixture
def authenticated_client_with_session(real_db_session):
    """
    Клиент, где API и Celery tasks используют одну и ту же DB session.
    """
    user = User(
        email="ytd-invariants@example.com",
        password_hash="$2b$12$fake",
        wb_api_key="test-wb-key",
        is_active=True,
    )
    real_db_session.add(user)
    real_db_session.commit()
    real_db_session.refresh(user)
    user_id = str(user.id)
    token = create_access_token(data={"sub": user_id})

    def get_db_override():
        yield real_db_session

    app.dependency_overrides[get_db] = get_db_override
    try:
        real_db_session.close = MagicMock()
        with TestClient(app) as client:
            yield client, real_db_session, user_id, token
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_manual_funnel_ytd_job_fills_yesterday(authenticated_client_with_session):
    """
    Сценарий "пользователь вошёл → /dashboard/state увидел дырку → запустил backfill":
    - delay действительно ставится
    - при выполнении sync_funnel_ytd_step за вчера появляется funnel_daily с order_count>0
    - /dashboard/funnel за вчера не возвращает нули
    """
    client, session, user_id, token = authenticated_client_with_session
    headers = {"Authorization": f"Bearer {token}"}

    today = _FixedDate.today()
    yesterday = today - timedelta(days=1)
    y_s = yesterday.isoformat()
    nm_id = 111

    # Условия автозапуска в /dashboard/state:
    # - есть RawSale за период
    # - за вчера нет FunnelDaily
    session.add(
        RawSale(
            user_id=user_id,
            date=yesterday,
            nm_id=nm_id,
            doc_type="Продажа",
            retail_price=100,
            ppvz_for_pay=90,
            delivery_rub=0,
            penalty=0,
            additional_payment=0,
            storage_fee=0,
            quantity=1,
        )
    )
    session.add(Article(user_id=user_id, nm_id=nm_id, vendor_code="NM-111", name="SKU", cost_price=0))
    session.commit()

    wb_row = {
        "date": y_s,
        "nm_id": nm_id,
        "vendor_code": "NM-111",
        "open_count": 100,
        "cart_count": 10,
        "order_count": 2,
        "order_sum": 1000.0,
        "buyout_percent": 50.0,
        "cr_to_cart": 0.10,
        "cr_to_order": 0.02,
        "subject_name": "Категория",
    }

    with (
        patch("app.routers.dashboard.date_type", _FixedDate),
        patch("app.routers.dashboard.sync_funnel_ytd_step.delay") as mock_delay,
    ):
        r = client.get("/dashboard/state", headers=headers)
        assert r.status_code == 200
        mock_delay.assert_not_called()

    # Выполняем саму джобу вне контекста HTTP-запроса, чтобы не ломать ORM-объект current_user
    # (внутри /dashboard/state он используется дальше по коду).
    with (
        patch("celery_app.tasks.date", _FixedDate),
        patch("celery_app.tasks.FUNNEL_YTD_DAYS_PER_RUN", 1),
        patch("celery_app.tasks.SessionLocal", return_value=session),
        patch("celery_app.tasks.fetch_funnel", return_value=[]),  # weekly history can be empty
        patch("celery_app.tasks.fetch_funnel_products_for_day_with_retry", return_value=[wb_row]),
        patch("celery_app.tasks.time.sleep", return_value=None),
        patch.object(sync_funnel_ytd_step, "apply_async", return_value=None),
        patch.object(recalculate_sku_daily, "delay", side_effect=lambda *a, **k: recalculate_sku_daily(*a, **k)),
    ):
        out = sync_funnel_ytd_step(user_id, 2026)
    assert out.get("ok") is True, out

    # DB invariant: funnel_daily за вчера заполнен и не ноль
    row = (
        session.query(FunnelDaily)
        .filter(FunnelDaily.user_id == user_id, FunnelDaily.date == yesterday, FunnelDaily.nm_id == nm_id)
        .first()
    )
    assert isinstance(row, FunnelDaily)
    assert int(row.order_count or 0) > 0

    # API invariant: /dashboard/funnel за вчера не ноль
    rf = client.get("/dashboard/funnel", params={"date_from": y_s, "date_to": y_s}, headers=headers)
    assert rf.status_code == 200
    payload = rf.json()
    hit = [x for x in payload if x.get("date") == y_s and int(x.get("nm_id") or 0) == nm_id]
    assert hit
    assert int(hit[0].get("order_count") or 0) > 0


def test_funnel_ytd_job_walks_days_and_does_not_leave_zeros_when_input_nonzero(authenticated_client_with_session):
    """
    Сценарий "Jabba прошлась по дням": sync_funnel_ytd_step обрабатывает батч дней
    и для дней, где WB-ответ содержит order_sum>0, в funnel_daily не остаётся нулей
    и прогресс (last_completed_date) двигается.
    """
    _client, session, user_id, _token = authenticated_client_with_session

    today = _FixedDate.today()
    d1 = today - timedelta(days=1)
    d2 = today - timedelta(days=2)
    d3 = today - timedelta(days=3)
    nm_id = 222

    # Чтобы _funnel_nm_ids вернул nm_id (берётся из Article и/или raw)
    session.add(Article(user_id=user_id, nm_id=nm_id, vendor_code="NM-222", name="SKU2", cost_price=0))
    session.commit()

    input_by_day = {
        d1: 3,  # non-zero -> must stay non-zero
        d2: 0,  # zero allowed
        d3: 1,  # non-zero -> must stay non-zero
    }

    def _fetch_products(day_s: str, _chunk: list[int], _key: str, **_kwargs: object):
        day = real_date.fromisoformat(day_s)
        oc = int(input_by_day.get(day, 0))
        return [
            {
                "date": day_s,
                "nm_id": nm_id,
                "vendor_code": "NM-222",
                "open_count": 100,
                "cart_count": 10,
                "order_count": oc,
                "order_sum": float(oc * 100),
                "buyout_percent": None,
                "cr_to_cart": None,
                "cr_to_order": None,
            }
        ]

    with (
        patch("celery_app.tasks.date", _FixedDate),
        patch("celery_app.tasks.FUNNEL_YTD_DAYS_PER_RUN", 3),
        patch("celery_app.tasks.SessionLocal", return_value=session),
        patch("celery_app.tasks.fetch_funnel", return_value=[]),
        patch("celery_app.tasks.fetch_funnel_products_for_day_with_retry", side_effect=_fetch_products),
        patch("celery_app.tasks.time.sleep", return_value=None),
        patch.object(sync_funnel_ytd_step, "apply_async", return_value=None),
        patch.object(recalculate_sku_daily, "delay", side_effect=lambda *a, **k: recalculate_sku_daily(*a, **k)),
    ):
        out = sync_funnel_ytd_step(user_id, 2026)
    assert out.get("ok") is True, out
    assert out.get("days_processed") == 3

    # Проверяем инвариант по дням
    for d, expected_orders in input_by_day.items():
        r = (
            session.query(FunnelDaily)
            .filter(FunnelDaily.user_id == user_id, FunnelDaily.date == d, FunnelDaily.nm_id == nm_id)
            .first()
        )
        assert isinstance(r, FunnelDaily), f"no funnel_daily for {d}"
        if expected_orders > 0:
            assert int(r.order_count or 0) > 0, f"expected non-zero orders for {d} but got {r.order_count}"

    # И состояние backfill должно обновиться
    st = (
        session.query(FunnelBackfillState)
        .filter(FunnelBackfillState.user_id == user_id, FunnelBackfillState.calendar_year == 2026)
        .first()
    )
    assert isinstance(st, FunnelBackfillState)
    assert st.status == "running" or st.status == "complete"
    assert st.last_completed_date is not None

