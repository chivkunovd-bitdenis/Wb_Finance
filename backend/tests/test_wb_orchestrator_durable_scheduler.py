"""Real PostgreSQL concurrency and crash recovery. All WB/provider work is stubbed."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from unittest.mock import Mock

import pytest
import requests
from app.db import DATABASE_URL
from app.models.base import Base
from app.models.user import User
from app.models.wb_orchestrator_state import WbOrchestratorState
from app.services.wb_orchestrator_scheduler import (
    claim_due,
    locked_state,
)
from celery_app import tasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def store(monkeypatch):
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    user_id = str(uuid.uuid4())
    with factory() as db:
        db.add(User(id=user_id, email=f"{user_id}@test.invalid", password_hash="x", wb_api_key="stub"))
        db.commit()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks.wb_orchestrator_tick, "apply_async", Mock())
    monkeypatch.setattr(tasks, "_orch_finance_missing_step", Mock(return_value={"message": "complete"}))
    monkeypatch.setattr(tasks, "_orch_funnel_tail_step", Mock(return_value={"message": "complete"}))
    yield factory, user_id
    with factory() as db:
        db.query(User).filter(User.id == user_id).delete()
        db.commit()
    engine.dispose()


def state(factory, user_id):
    with factory() as db:
        st = db.get(WbOrchestratorState, user_id)
        db.expunge(st)
        return st


def claim(factory):
    with factory() as db:
        return claim_due(db)


def test_100_legacy_and_stale_copies_during_12h_cooldown_publish_nothing(store):
    factory, uid = store
    until = datetime.now(UTC) + timedelta(hours=12)
    with factory() as db:
        db.add(WbOrchestratorState(user_id=uid, status="cooldown", cooldown_until=until.isoformat(),
            intents={"high": {"funnel_tail": True}, "blocked": {"funnel": until.isoformat()}}))
        db.commit()
    for _ in range(100):
        assert tasks.wb_orchestrator_tick(uid)["message"] == "legacy_deferred"
        assert tasks.wb_orchestrator_tick(uid, "old-token")["message"] == "stale_dispatch"
    assert state(factory, uid).next_tick_at == until
    assert claim(factory) == []
    tasks._orch_funnel_tail_step.assert_not_called()
    tasks.wb_orchestrator_tick.apply_async.assert_not_called()


def test_concurrent_kicks_and_dispatchers_merge_without_fanout(store):
    factory, uid = store
    patches = [{"high": {"funnel_tail": True}}, {"low": {"finance_backfill_year": 2026}}] * 10
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda p: tasks.wb_orchestrator_kick(uid, p), patches))
    assert all(r["ok"] for r in results)
    assert state(factory, uid).intents == {"high": {"funnel_tail": True}, "low": {"finance_backfill_year": 2026}}
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = [item for batch in pool.map(lambda _: claim(factory), range(8)) for item in batch]
    assert len(claims) == 1
    tasks.wb_orchestrator_tick.apply_async.assert_not_called()


def test_tick_keeps_intents_added_during_step_and_deduplicates_token(store):
    factory, uid = store
    tasks.wb_orchestrator_kick(uid, {"high": {"funnel_tail": True}})
    token = claim(factory)[0][1]
    entered, release = Event(), Event()
    def step(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return {"message": "complete"}
    tasks._orch_funnel_tail_step.side_effect = step
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(tasks.wb_orchestrator_tick, uid, token)
        assert entered.wait(10)
        # PG advisory lock must remain held after the claim transaction committed.
        assert tasks.wb_orchestrator_tick(uid, token)["message"] == "already_running"
        tasks.wb_orchestrator_kick(uid, {"high": {"finance_range": {"date_from": "2026-10-01", "date_to": "2026-10-02"}}})
        release.set()
        assert future.result()["ok"]
    st = state(factory, uid)
    assert "funnel_tail" not in st.intents["high"]
    assert st.intents["high"]["finance_range"]["date_to"] == "2026-10-02"
    assert st.next_tick_at > datetime.now(UTC)
    assert tasks.wb_orchestrator_tick(uid, token)["message"] == "stale_dispatch"
    tasks.wb_orchestrator_tick.apply_async.assert_not_called()


def test_publish_failure_and_worker_death_recover(store):
    factory, uid = store
    tasks.wb_orchestrator_kick(uid, {"high": {"funnel_tail": True}})
    tasks.wb_orchestrator_tick.apply_async.side_effect = RuntimeError("broker down")
    assert tasks.wb_orchestrator_dispatch()["sent"] == 0
    assert state(factory, uid).dispatch_token is None
    first = claim(factory)[0][1]
    with factory() as db:
        st = locked_state(db, uid)
        st.dispatch_lease_until = datetime.now(UTC) - timedelta(seconds=1)
        st.next_tick_at = None  # crash after tick took ownership
        st.status = "running"
        db.commit()
    tasks.wb_orchestrator_kick(uid, {})  # updated_at must not extend dead lease
    second = claim(factory)[0][1]
    assert first != second
    assert tasks.wb_orchestrator_tick(uid, first)["message"] == "stale_dispatch"
    assert tasks.wb_orchestrator_tick(uid, second)["ok"]
    assert state(factory, uid).next_tick_at is None


def test_finance_429_then_new_funnel_wakes_and_blocked_metadata_goes_idle(store):
    factory, uid = store
    fr = {"date_from": "2026-10-01", "date_to": "2026-10-02"}
    tasks.wb_orchestrator_kick(uid, {"high": {"finance_range": fr}})
    response = requests.Response()
    response.status_code = 429
    response.headers["X-RateLimit-Reset"] = "43200"
    tasks._orch_finance_missing_step.side_effect = requests.HTTPError(response=response)
    assert tasks.wb_orchestrator_tick(uid, claim(factory)[0][1])["http_code"] == 429
    assert state(factory, uid).status == "cooldown"
    tasks.wb_orchestrator_kick(uid, {"high": {"funnel_tail": True}})
    assert tasks.wb_orchestrator_tick(uid, claim(factory)[0][1])["ok"]
    st = state(factory, uid)
    assert st.intents["high"] == {"finance_range": fr}
    assert st.status == "cooldown"
    with factory() as db:
        st = locked_state(db, uid)
        st.intents = {"blocked": st.intents["blocked"]}
        st.next_tick_at = datetime.now(UTC)
        db.commit()
    assert claim(factory) == []
    assert state(factory, uid).status == "idle"
    tasks.wb_orchestrator_tick.apply_async.assert_not_called()


def test_early_current_token_does_not_erase_pacing_deadline(store):
    factory, uid = store
    tasks.wb_orchestrator_kick(uid, {"high": {"funnel_tail": True}})
    token = claim(factory)[0][1]
    future = datetime.now(UTC) + timedelta(seconds=20)
    with factory() as db:
        st = locked_state(db, uid)
        st.next_tick_at = future
        db.commit()
    assert tasks.wb_orchestrator_tick(uid, token)["message"] == "not_due"
    assert state(factory, uid).next_tick_at == future
    assert claim(factory) == []
    tasks._orch_funnel_tail_step.assert_not_called()
    tasks.wb_orchestrator_tick.apply_async.assert_not_called()


@pytest.mark.parametrize("http_code", [400, 401, 403, 404])
def test_permanent_http_failure_preserves_work_and_requires_new_kick(store, http_code):
    factory, uid = store
    intents = {"high": {"funnel_tail": True}}
    tasks.wb_orchestrator_kick(uid, intents)
    token = claim(factory)[0][1]
    response = requests.Response()
    response.status_code = http_code
    tasks._orch_funnel_tail_step.side_effect = requests.HTTPError(response=response)
    assert tasks.wb_orchestrator_tick(uid, token)["http_code"] == http_code
    st = state(factory, uid)
    assert st.status == "error"
    assert st.last_step == f"wb_http_{http_code}"
    assert st.next_tick_at is None
    assert st.dispatch_token is None and st.dispatch_lease_until is None
    assert st.intents == intents
    assert claim(factory) == []
    # Retained broker copies cannot resurrect a permanent failure.
    for _ in range(3):
        assert tasks.wb_orchestrator_tick(uid)["message"] == "legacy_deferred"
        assert tasks.wb_orchestrator_tick(uid, token)["message"] == "stale_dispatch"
    assert claim(factory) == []
    tasks._orch_funnel_tail_step.assert_called_once()
    tasks.wb_orchestrator_tick.apply_async.assert_not_called()
    tasks.wb_orchestrator_kick(uid, {})
    replacement = claim(factory)[0][1]
    tasks._orch_funnel_tail_step.side_effect = None
    assert tasks.wb_orchestrator_tick(uid, replacement)["ok"]
    assert state(factory, uid).status == "idle"


def test_missing_key_preserves_intents_without_automatic_retries(store):
    factory, uid = store
    intents = {"high": {"funnel_tail": True}}
    with factory() as db:
        db.get(User, uid).wb_api_key = None
        db.commit()
    tasks.wb_orchestrator_kick(uid, intents)
    assert tasks.wb_orchestrator_tick(uid, claim(factory)[0][1])["error"] == "no_wb_api_key"
    st = state(factory, uid)
    assert st.status == "error" and st.next_tick_at is None
    assert st.intents == intents
    assert claim(factory) == []
    tasks._orch_funnel_tail_step.assert_not_called()
    tasks.wb_orchestrator_tick.apply_async.assert_not_called()
    tasks.wb_orchestrator_kick(uid, {})
    assert len(claim(factory)) == 1


def test_permanent_failure_does_not_drop_kick_received_during_step(store):
    factory, uid = store
    tasks.wb_orchestrator_kick(uid, {"high": {"funnel_tail": True}})
    token = claim(factory)[0][1]
    entered, release = Event(), Event()
    def step(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        response = requests.Response()
        response.status_code = 401
        raise requests.HTTPError(response=response)
    tasks._orch_funnel_tail_step.side_effect = step
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = pool.submit(tasks.wb_orchestrator_tick, uid, token)
        assert entered.wait(10)
        tasks.wb_orchestrator_kick(uid, {"low": {"finance_backfill_year": 2026}})
        release.set()
        assert result.result()["http_code"] == 401
    st = state(factory, uid)
    assert st.status == "scheduled"
    assert st.next_tick_at > datetime.now(UTC)
    assert st.intents == {"high": {"funnel_tail": True}, "low": {"finance_backfill_year": 2026}}
    tasks.wb_orchestrator_tick.apply_async.assert_not_called()


def test_permanent_failure_invalidates_replacement_lease_without_new_kick(store):
    factory, uid = store
    tasks.wb_orchestrator_kick(uid, {"high": {"funnel_tail": True}})
    token = claim(factory)[0][1]
    replacements = []
    def step(*args, **kwargs):
        with factory() as db:
            st = locked_state(db, uid)
            st.dispatch_lease_until = datetime.now(UTC) - timedelta(seconds=1)
            db.commit()
        replacements.extend(claim(factory))
        response = requests.Response()
        response.status_code = 403
        raise requests.HTTPError(response=response)
    tasks._orch_funnel_tail_step.side_effect = step
    assert tasks.wb_orchestrator_tick(uid, token)["http_code"] == 403
    assert len(replacements) == 1
    assert tasks.wb_orchestrator_tick(uid, replacements[0][1])["message"] == "stale_dispatch"
    assert claim(factory) == []
    assert state(factory, uid).status == "error"
