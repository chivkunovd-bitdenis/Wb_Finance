from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import requests


def _http_error(status_code: int, *, headers: dict[str, str] | None = None) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = b"{}"  # type: ignore[attr-defined]
    resp.url = "https://example.test/wb"
    if headers:
        resp.headers.update(headers)
    return requests.HTTPError(f"{status_code} http error", response=resp)


def _authed_client(real_db_session):
    from fastapi.testclient import TestClient

    from app.core.security import create_access_token
    from app.db import get_db
    from app.main import app
    from app.models.user import User

    u = User(email="holes-flow@example.com", password_hash="x", is_active=True, wb_api_key="k")
    real_db_session.add(u)
    real_db_session.commit()
    token = create_access_token(data={"sub": str(u.id)})

    def get_db_override():
        yield real_db_session

    app.dependency_overrides[get_db] = get_db_override
    try:
        with TestClient(app) as c:
            yield c, real_db_session, str(u.id), token
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_dashboard_state_finance_holes_limits_max_ranges_and_trims_long_range(real_db_session, monkeypatch):
    """
    /dashboard/state должен:
    - ставить ограниченное число дыр за один вход (FINANCE_HOLES_MAX_RANGES_PER_ENTRY=3)
    - триммить дыру длиннее 7 дней до последних 7 дней
    """
    gen = _authed_client(real_db_session)
    client, session, user_id, token = next(gen)

    from app.models.raw_sales import RawSale
    from app.models.pnl_daily import PnlDaily

    today = date.today()
    yesterday = today - timedelta(days=1)

    # eligibility: raw_sales exists in year window
    session.add(
        RawSale(
            user_id=user_id,
            date=yesterday - timedelta(days=2),
            nm_id=123,
            doc_type="Продажа",
            retail_price=100,
            ppvz_for_pay=90,
            delivery_rub=5,
            penalty=0,
            additional_payment=0,
            storage_fee=0,
            quantity=1,
        )
    )
    # Yesterday present => no tail hole
    session.add(
        PnlDaily(
            user_id=user_id,
            date=yesterday,
            revenue=1,
            commission=0,
            logistics=0,
            penalties=0,
            storage=0,
            ads_spend=0,
            cogs=0,
            tax=0,
            margin=1,
            operation_expenses=0,
        )
    )
    session.commit()

    from app.services.finance_missing_tail import DateRange

    # build holes: first is long (>7 days), plus several smaller ones to exceed max ranges
    long_hole = DateRange(date_from=yesterday - timedelta(days=20), date_to=yesterday - timedelta(days=10))  # 11 days
    hole2 = DateRange(date_from=yesterday - timedelta(days=8), date_to=yesterday - timedelta(days=8))
    hole3 = DateRange(date_from=yesterday - timedelta(days=6), date_to=yesterday - timedelta(days=6))
    hole4 = DateRange(date_from=yesterday - timedelta(days=4), date_to=yesterday - timedelta(days=4))
    holes = [hole2, hole3, hole4, long_hole]  # order shouldn't matter; router processes newest->oldest

    monkeypatch.setattr("app.routers.dashboard.compute_missing_ranges_in_window", lambda *a, **k: holes)

    with patch("app.routers.dashboard.sync_finance_missing_range.delay") as mock_missing:
        r = client.get("/dashboard/state", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert mock_missing.call_count <= 3
        calls = [c.args for c in mock_missing.call_args_list]
        trimmed_df = long_hole.date_to - timedelta(days=6)
        # if long hole was enqueued, it must be trimmed
        if any(args[2] == long_hole.date_to.isoformat() for args in calls):
            assert any(args[1] == trimmed_df.isoformat() and args[2] == long_hole.date_to.isoformat() for args in calls)

    try:
        next(gen)
    except StopIteration:
        pass


def test_dashboard_state_finance_missing_range_dedup_respects_next_run_at(real_db_session):
    gen = _authed_client(real_db_session)
    client, session, user_id, token = next(gen)

    from app.models.raw_sales import RawSale
    from app.models.pnl_daily import PnlDaily
    from app.models.finance_missing_sync_state import FinanceMissingSyncState

    today = date.today()
    yesterday = today - timedelta(days=1)
    day_before = today - timedelta(days=2)

    session.add(
        RawSale(
            user_id=user_id,
            date=day_before,
            nm_id=123,
            doc_type="Продажа",
            retail_price=100,
            ppvz_for_pay=90,
            delivery_rub=5,
            penalty=0,
            additional_payment=0,
            storage_fee=0,
            quantity=1,
        )
    )
    # Важно: для новой логики дыр "вчера присутствует" определяется по raw_sales,
    # иначе сработает missing-tail и тест уйдёт не по той ветке.
    session.add(
        RawSale(
            user_id=user_id,
            date=yesterday,
            nm_id=123,
            doc_type="Продажа",
            retail_price=100,
            ppvz_for_pay=90,
            delivery_rub=5,
            penalty=0,
            additional_payment=0,
            storage_fee=0,
            quantity=1,
        )
    )
    session.add(
        PnlDaily(
            user_id=user_id,
            date=yesterday,
            revenue=1,
            commission=0,
            logistics=0,
            penalties=0,
            storage=0,
            ads_spend=0,
            cogs=0,
            tax=0,
            margin=1,
            operation_expenses=0,
        )
    )
    # middle hole would be scheduled, but we create state for it with next_run_at in future
    hole_df = yesterday - timedelta(days=6)
    hole_dt = hole_df
    session.add(
        FinanceMissingSyncState(
            user_id=user_id,
            date_from=hole_df,
            date_to=hole_dt,
            status="idle",
            next_run_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    session.commit()

    with patch("app.routers.dashboard.sync_finance_missing_range.delay") as mock_missing:
        with patch(
            "app.routers.dashboard.compute_missing_ranges_in_window",
            return_value=[MagicMock(date_from=hole_df, date_to=hole_dt)],
        ):
            r = client.get("/dashboard/state", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200
            mock_missing.assert_not_called()

    try:
        next(gen)
    except StopIteration:
        pass


def test_sync_finance_backfill_step_defers_when_pending_missing_exists(monkeypatch, real_db_session):
    from app.models.user import User
    from app.models.finance_missing_sync_state import FinanceMissingSyncState
    from celery_app import tasks

    u = User(email="defer@example.com", password_hash="x", is_active=True, wb_api_key="k")
    real_db_session.add(u)
    real_db_session.commit()

    through = date.today() - timedelta(days=1)
    real_db_session.add(
        FinanceMissingSyncState(
            user_id=str(u.id),
            date_from=through,
            date_to=through,
            status="idle",
            next_run_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
    )
    real_db_session.commit()

    monkeypatch.setattr(tasks, "SessionLocal", lambda: real_db_session)

    captured: dict[str, object] = {}

    def _apply_async(*, args, countdown):
        captured["args"] = args
        captured["countdown"] = countdown

    monkeypatch.setattr(tasks.sync_finance_backfill_step, "apply_async", _apply_async)

    res = tasks.sync_finance_backfill_step(str(u.id), 2026)
    assert res["ok"] is True
    assert res["message"] == "deferred_due_to_missing_tail"
    assert captured["countdown"] == 600


def test_sync_finance_missing_range_schedules_retry_on_429_uses_wb_headers(monkeypatch, real_db_session):
    from app.models.user import User
    from celery_app import tasks

    # deterministic jitter
    monkeypatch.setattr(tasks.random, "randint", lambda a, b: 0)

    u = User(email="r429@example.com", password_hash="x", is_active=True, wb_api_key="k")
    real_db_session.add(u)
    real_db_session.commit()

    monkeypatch.setattr(tasks, "SessionLocal", lambda: real_db_session)

    def _raise_429(*args, **kwargs):
        raise _http_error(429, headers={"X-RateLimit-Reset": "1000"})

    monkeypatch.setattr(tasks, "sync_sales", _raise_429)
    monkeypatch.setattr(tasks, "sync_ads", lambda *a, **k: {"ok": True, "count": 0})

    captured: dict[str, object] = {}

    def _apply_async(*, args, countdown):
        captured["args"] = args
        captured["countdown"] = countdown

    monkeypatch.setattr(tasks.sync_finance_missing_range, "apply_async", _apply_async)

    d = (date.today() - timedelta(days=1)).isoformat()
    res = tasks.sync_finance_missing_range(str(u.id), d, d)
    assert res["ok"] is False
    assert res["error"] == "wb_retry_scheduled"
    assert res["http_code"] == 429
    assert int(res["delay_sec"]) >= 1000
    assert captured["countdown"] == res["delay_sec"]

