from __future__ import annotations

from datetime import date

from app.models.user import User
from app.models.funnel_daily import FunnelDaily
from celery_app.tasks import _funnel_insert_only


def test_funnel_insert_heals_existing_zero_orders(real_db_session):
    """
    Регрессия из прод-симптома:
    если в funnel_daily однажды записались нули (order_count=0),
    а позже пришли корректные значения order_count>0 для того же (user_id,date,nm_id),
    витрина должна "долечиться" и обновиться.

    Этот тест падает при ON CONFLICT DO NOTHING.
    """
    user_id = "a0000000-0000-4000-8000-000000000010"
    d = date(2026, 4, 7)
    nm_id = 123

    # FK: funnel_daily.user_id -> users.id
    real_db_session.add(
        User(
            id=user_id,
            email="upsert-heal@example.com",
            password_hash="$2b$12$fake",
            wb_api_key="k",
            is_active=True,
        )
    )
    real_db_session.commit()

    # существующая "плохая" строка (нули)
    real_db_session.add(
        FunnelDaily(
            user_id=user_id,
            date=d,
            nm_id=nm_id,
            vendor_code="NM-123",
            open_count=0,
            cart_count=0,
            order_count=0,
            order_sum=None,
            buyout_percent=None,
            cr_to_cart=None,
            cr_to_order=None,
        )
    )
    real_db_session.commit()

    # повторный прогон приносит уже ненулевые метрики
    rows = [
        {
            "date": d.isoformat(),
            "nm_id": nm_id,
            "vendor_code": "NM-123",
            "open_count": 100,
            "cart_count": 10,
            "order_count": 2,
            "order_sum": 500.0,
            "buyout_percent": 50.0,
            "cr_to_cart": 0.10,
            "cr_to_order": 0.02,
        }
    ]
    _ = _funnel_insert_only(real_db_session, rows, user_id=user_id)
    real_db_session.commit()

    healed = (
        real_db_session.query(FunnelDaily)
        .filter(FunnelDaily.user_id == user_id, FunnelDaily.date == d, FunnelDaily.nm_id == nm_id)
        .first()
    )
    assert isinstance(healed, FunnelDaily)
    assert int(healed.order_count or 0) > 0
    assert int(healed.open_count or 0) > 0
    assert int(healed.cart_count or 0) > 0

