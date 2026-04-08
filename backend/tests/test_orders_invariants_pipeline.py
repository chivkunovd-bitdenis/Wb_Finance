from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db import get_db
from app.main import app
from app.models.article import Article
from app.models.funnel_daily import FunnelDaily
from app.models.sku_daily import SkuDaily
from app.models.user import User
from celery_app.tasks import recalculate_sku_daily, sync_funnel


@pytest.fixture
def client_and_session(real_db_session):
    """
    Интеграционный клиент: API + tasks используют одну DB session.
    """
    user = User(
        email="orders-invariants@example.com",
        password_hash="$2b$12$fake",
        wb_api_key="test-wb-key",
        is_active=True,
    )
    real_db_session.add(user)
    real_db_session.commit()
    real_db_session.refresh(user)
    user_id = str(user.id)
    token = create_access_token(data={"sub": user_id})

    # Нужен артикул, чтобы sync_funnel нашёл nm_ids для запроса воронки
    nm_id = 123456
    real_db_session.add(
        Article(
            user_id=user_id,
            nm_id=nm_id,
            vendor_code="NM-123456",
            name="Test SKU",
            cost_price=0,
        )
    )
    real_db_session.commit()

    def get_db_override():
        yield real_db_session

    app.dependency_overrides[get_db] = get_db_override
    try:
        real_db_session.close = MagicMock()
        with TestClient(app) as client:
            yield client, real_db_session, user_id, token, nm_id
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_orders_invariant_nonzero_input_cannot_become_zero_on_dashboard(client_and_session):
    """
    Инвариант ядра продукта:
    Если воронка (как вход pipeline) содержит order_count > 0 за день,
    то после sync_funnel + recalculate_sku_daily дашборд (/dashboard/funnel и /dashboard/sku)
    не имеет права показывать order_count=0 для этого (day,nm_id).
    """
    client, session, user_id, token, nm_id = client_and_session
    headers = {"Authorization": f"Bearer {token}"}

    # Дата теста — "вчера", чтобы совпадало с привычными запросами UI.
    day = date.today() - timedelta(days=1)
    day_s = day.isoformat()

    wb_funnel_rows = [
        {
            "date": day_s,
            "nm_id": nm_id,
            "vendor_code": "NM-123456",
            "open_count": 100,
            "cart_count": 10,
            "order_count": 3,
            "order_sum": 1500.0,
            "buyout_percent": 50.0,
            "cr_to_cart": 0.10,
            "cr_to_order": 0.03,
            "subject_name": "Категория",
        }
    ]

    with patch("celery_app.tasks.fetch_funnel", return_value=wb_funnel_rows), patch(
        "celery_app.tasks.SessionLocal", return_value=session
    ):
        out_sync = sync_funnel(user_id, day_s, day_s)
        assert out_sync.get("ok") is True, f"sync_funnel failed: {out_sync}"

        # Пересчёт sku_daily должен подтянуть order_count/order_sum из funnel_daily
        out_sku = recalculate_sku_daily(user_id, day_s, day_s)
        assert out_sku.get("ok") is True, f"recalculate_sku_daily failed: {out_sku}"

    # 1) DB-level: funnel_daily не ноль
    db_f = (
        session.query(FunnelDaily)
        .filter(FunnelDaily.user_id == user_id, FunnelDaily.date == day, FunnelDaily.nm_id == nm_id)
        .first()
    )
    assert isinstance(db_f, FunnelDaily), "funnel_daily row not created"
    assert int(db_f.order_count or 0) > 0, f"funnel_daily order_count=0 but WB input was {wb_funnel_rows}"
    assert float(db_f.order_sum or 0) > 0

    # 2) DB-level: sku_daily тоже не ноль (агрегация не потеряла funnel)
    db_s = (
        session.query(SkuDaily)
        .filter(SkuDaily.user_id == user_id, SkuDaily.date == day, SkuDaily.nm_id == nm_id)
        .first()
    )
    assert isinstance(db_s, SkuDaily), "sku_daily row not created"
    assert int(db_s.order_count or 0) > 0, "sku_daily lost orders from funnel_daily"

    # 3) API-level: /dashboard/funnel содержит order_count > 0
    r_f = client.get("/dashboard/funnel", params={"date_from": day_s, "date_to": day_s}, headers=headers)
    assert r_f.status_code == 200, r_f.text
    funnel_payload = r_f.json()
    hit = [x for x in funnel_payload if x.get("date") == day_s and int(x.get("nm_id") or 0) == nm_id]
    assert hit, f"/dashboard/funnel has no row for (day={day_s}, nm_id={nm_id}); payload_len={len(funnel_payload)}"
    assert int(hit[0].get("order_count") or 0) > 0, f"/dashboard/funnel order_count=0; row={hit[0]}"

    # 4) API-level: /dashboard/sku тоже содержит order_count > 0
    r_s = client.get(
        "/dashboard/sku",
        params={"date_from": day_s, "date_to": day_s, "nm_id": nm_id},
        headers=headers,
    )
    assert r_s.status_code == 200, r_s.text
    sku_payload = r_s.json()
    assert sku_payload, "/dashboard/sku returned empty list"
    assert int(sku_payload[0].get("order_count") or 0) > 0, f"/dashboard/sku order_count=0; row={sku_payload[0]}"


def test_orders_diagnostics_if_sync_returns_ok_but_rows_missing(client_and_session):
    """
    Защитный тест против "ok=True, но витрина пустая/нули":
    если sync_funnel вернул ok=True и входные данные были ненулевые,
    а в DB нет строк — это жёсткая ошибка в нашей логике сохранения.
    """
    _client, session, user_id, _token, nm_id = client_and_session
    day = date.today() - timedelta(days=1)
    day_s = day.isoformat()

    wb_funnel_rows = [
        {
            "date": day_s,
            "nm_id": nm_id,
            "vendor_code": "NM-123456",
            "open_count": 50,
            "cart_count": 5,
            "order_count": 1,
            "order_sum": 500.0,
            "buyout_percent": None,
            "cr_to_cart": None,
            "cr_to_order": None,
        }
    ]

    with patch("celery_app.tasks.fetch_funnel", return_value=wb_funnel_rows), patch(
        "celery_app.tasks.SessionLocal", return_value=session
    ):
        out = sync_funnel(user_id, day_s, day_s)
    assert out.get("ok") is True, f"sync_funnel failed: {out}"

    # Должно быть хотя бы 1 строка funnel_daily с order_count>0
    rows = (
        session.query(FunnelDaily)
        .filter(FunnelDaily.user_id == user_id, FunnelDaily.date == day, FunnelDaily.nm_id == nm_id)
        .all()
    )
    assert rows, f"sync_funnel ok=True but funnel_daily empty; wb_rows={wb_funnel_rows}"
    assert int(rows[0].order_count or 0) > 0, f"funnel_daily order_count=0; wb_rows={wb_funnel_rows}"

