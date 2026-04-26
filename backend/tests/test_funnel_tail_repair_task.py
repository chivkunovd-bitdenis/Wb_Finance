from __future__ import annotations

from datetime import timedelta

import pytest


@pytest.mark.usefixtures("real_db_session")
def test_sync_funnel_tail_repair_picks_latest_missing_day(monkeypatch, real_db_session):
    from app.models.user import User
    from app.models.funnel_daily import FunnelDaily
    from celery_app import tasks
    from datetime import date as _date

    u = User(email="tail@example.com", password_hash="x", is_active=True, wb_api_key="k")
    real_db_session.add(u)
    real_db_session.commit()

    # Freeze "today" inside celery_app.tasks to make the rolling window deterministic.
    fixed_today = _date(2026, 4, 26)

    class _FixedDate(_date):
        @classmethod
        def today(cls):  # type: ignore[override]
            return fixed_today

    monkeypatch.setattr(tasks, "date", _FixedDate)

    # Pretend we already have data for end-1, but missing end (yesterday).
    start_d, end_d = tasks._funnel_rolling_window_dates()
    have_day = end_d - timedelta(days=1)
    real_db_session.add(
        FunnelDaily(
            user_id=u.id,
            date=have_day,
            nm_id=1,
            open_count=1,
            cart_count=1,
            order_count=1,
            order_sum=10,
        )
    )
    real_db_session.commit()

    monkeypatch.setattr(tasks, "SessionLocal", lambda: real_db_session)

    captured: dict = {}

    def _fake_fetch(day: str, nm_ids: list[int], key: str):
        captured["day"] = day
        captured["nm_ids"] = nm_ids
        return [
            {
                "date": day,
                "nm_id": 123,
                "vendor_code": "vc",
                "open_count": 1,
                "cart_count": 0,
                "order_count": 0,
                "order_sum": 0,
                "buyout_percent": None,
                "cr_to_cart": None,
                "cr_to_order": None,
                "subject_name": None,
            }
        ]

    monkeypatch.setattr(tasks, "fetch_funnel_products_for_day", _fake_fetch)

    res = tasks.sync_funnel_tail_repair(str(u.id))
    assert res["ok"] is True
    # We must pick a day inside rolling window, and never re-fetch the day we already have.
    assert start_d.isoformat() <= captured["day"] <= end_d.isoformat()
    assert captured["day"] != have_day.isoformat()
    assert captured["nm_ids"] == []


@pytest.mark.usefixtures("real_db_session")
def test_sync_funnel_tail_repair_single_flight_returns_running(monkeypatch, real_db_session):
    from app.models.user import User
    from app.models.funnel_rolling_sync_state import FunnelRollingSyncState
    from celery_app import tasks

    u = User(email="tail2@example.com", password_hash="x", is_active=True, wb_api_key="k")
    real_db_session.add(u)
    real_db_session.commit()

    st = FunnelRollingSyncState(user_id=u.id, status="running")
    real_db_session.add(st)
    real_db_session.commit()

    monkeypatch.setattr(tasks, "SessionLocal", lambda: real_db_session)

    res = tasks.sync_funnel_tail_repair(str(u.id))
    assert res["ok"] is True
    assert res["status"] == "running"

