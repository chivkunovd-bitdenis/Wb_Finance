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
from app.models.finance_backfill_state import FinanceBackfillState
from app.models.pnl_daily import PnlDaily
from app.models.raw_sales import RawSale
from app.models.user import User
from celery_app.tasks import sync_finance_backfill_step


class _FixedDate(real_date):
    _fixed_today = real_date(2026, 4, 8)

    @classmethod
    def today(cls) -> real_date:  # type: ignore[override]
        return cls._fixed_today


@pytest.fixture
def authenticated_client_with_session(real_db_session):
    user = User(
        email="fin-ytd@example.com",
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


def test_state_autostarts_finance_backfill(authenticated_client_with_session):
    """
    При входе (/dashboard/state), если есть продажи raw_sales, но нет раннего покрытия pnl_daily,
    должен запускаться sync_finance_backfill_step.delay(user_id, 2026).
    """
    client, session, user_id, token = authenticated_client_with_session
    headers = {"Authorization": f"Bearer {token}"}

    today = _FixedDate.today()
    yesterday = today - timedelta(days=1)

    # Условие has_any_sales=True
    session.add(
        RawSale(
            user_id=user_id,
            date=yesterday,
            nm_id=123,
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
    session.commit()

    with (
        patch("app.routers.dashboard.date_type", _FixedDate),
        patch("app.routers.dashboard.sync_finance_backfill_step.delay") as mock_delay,
    ):
        r = client.get("/dashboard/state", headers=headers)
        assert r.status_code == 200
        mock_delay.assert_called_once()
        args, _kwargs = mock_delay.call_args
        assert args[0] == user_id
        assert args[1] == 2026


def test_finance_backfill_step_fills_pnl_daily_when_fetch_returns_sales(authenticated_client_with_session):
    """
    Инвариант: если backfill шаг выполнился ok=True и fetch_sales вернул строки,
    то в pnl_daily за чанк должны появиться строки (и /dashboard/pnl не пустой).
    """
    client, session, user_id, token = authenticated_client_with_session
    headers = {"Authorization": f"Bearer {token}"}

    # Фиксируем "сегодня", чтобы chunk был детерминированным: 2026-04-01..2026-04-07
    today = _FixedDate.today()
    yesterday = today - timedelta(days=1)
    df = real_date(yesterday.year, yesterday.month, 1).isoformat()
    dt = yesterday.isoformat()

    nm_id = 777
    session.add(Article(user_id=user_id, nm_id=nm_id, vendor_code="NM-777", name="SKU", cost_price=10))
    session.commit()

    # Возвращаем минимально валидные строки продаж WB (shape как в fetch_sales parser)
    wb_sales_rows = [
        {
            "date": dt,
            "nm_id": nm_id,
            "doc_type": "Продажа",
            "retail_price": 1000,
            "ppvz_for_pay": 900,
            "delivery_rub": 0,
            "penalty": 0,
            "additional_payment": 0,
            "storage_fee": 0,
            "quantity": 1,
            "subject_name": "Категория",
        }
    ]
    wb_ads_rows: list[dict] = []

    with (
        patch("celery_app.tasks.date", _FixedDate),
        patch("celery_app.tasks.SessionLocal", return_value=session),
        patch("celery_app.tasks.fetch_sales", return_value=wb_sales_rows),
        patch("celery_app.tasks.fetch_ads", return_value=wb_ads_rows),
        patch.object(sync_finance_backfill_step, "apply_async", return_value=None),
    ):
        out = sync_finance_backfill_step(user_id, 2026)
    assert out.get("ok") is True, out
    assert out.get("chunk", {}).get("date_from") == df
    assert out.get("chunk", {}).get("date_to") == dt

    # DB invariant: pnl_daily создан
    rows = (
        session.query(PnlDaily)
        .filter(PnlDaily.user_id == user_id, PnlDaily.date >= real_date.fromisoformat(df), PnlDaily.date <= real_date.fromisoformat(dt))
        .all()
    )
    assert rows, "pnl_daily empty after backfill ok=True with non-empty sales"

    # API invariant: /dashboard/pnl не пустой
    r = client.get("/dashboard/pnl", params={"date_from": df, "date_to": dt}, headers=headers)
    assert r.status_code == 200
    payload = r.json()
    assert isinstance(payload, list)
    assert payload, "/dashboard/pnl returned empty list"

    # State invariant: backfill state updated
    st = (
        session.query(FinanceBackfillState)
        .filter(FinanceBackfillState.user_id == user_id, FinanceBackfillState.calendar_year == 2026)
        .first()
    )
    assert isinstance(st, FinanceBackfillState)
    assert st.status in {"running", "complete"}
    assert st.last_completed_date is not None

