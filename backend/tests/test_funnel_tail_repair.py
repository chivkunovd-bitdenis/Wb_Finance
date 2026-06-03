from __future__ import annotations

from datetime import date, timedelta

from app.models.funnel_daily import FunnelDaily
from app.models.user import User
from app.services.funnel_tail_repair import funnel_days_needing_repair, funnel_rolling_window


def test_funnel_rolling_window_seven_days():
    through = date(2026, 6, 1)
    start, end = funnel_rolling_window(through=through)
    assert end == through
    assert (end - start).days == 6


def test_funnel_days_needing_repair_detects_missing_day(real_db_session):
    u = User(email="tail-miss@example.com", password_hash="x", is_active=True, wb_api_key="k")
    real_db_session.add(u)
    real_db_session.commit()

    through = date.today() - timedelta(days=1)
    start, _ = funnel_rolling_window(through=through)
    real_db_session.add(
        FunnelDaily(
            user_id=str(u.id),
            date=start,
            nm_id=100,
            order_count=1,
            order_sum=500,
        )
    )
    real_db_session.commit()

    missing = funnel_days_needing_repair(real_db_session, str(u.id), start=start, through=through)
    assert through in missing


def test_funnel_days_needing_repair_detects_hollow_order_sum(real_db_session):
    u = User(email="tail-hollow@example.com", password_hash="x", is_active=True, wb_api_key="k")
    real_db_session.add(u)
    real_db_session.commit()

    through = date.today() - timedelta(days=1)
    start, _ = funnel_rolling_window(through=through)
    real_db_session.add(
        FunnelDaily(
            user_id=str(u.id),
            date=through,
            nm_id=200,
            order_count=3,
            order_sum=0,
        )
    )
    real_db_session.commit()

    missing = funnel_days_needing_repair(real_db_session, str(u.id), start=start, through=through)
    assert through in missing


def test_funnel_days_needing_repair_complete_when_sum_present(real_db_session):
    u = User(email="tail-ok@example.com", password_hash="x", is_active=True, wb_api_key="k")
    real_db_session.add(u)
    real_db_session.commit()

    through = date.today() - timedelta(days=1)
    start, _ = funnel_rolling_window(through=through)
    for i in range(7):
        d = start + timedelta(days=i)
        real_db_session.add(
            FunnelDaily(
                user_id=str(u.id),
                date=d,
                nm_id=300 + i,
                order_count=1,
                order_sum=100,
            )
        )
    real_db_session.commit()

    missing = funnel_days_needing_repair(real_db_session, str(u.id), start=start, through=through)
    assert missing == []
