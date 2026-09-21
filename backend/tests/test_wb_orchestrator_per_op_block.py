"""
BUG-49: 429 от statistics-api (финансовый отчёт) не должен замораживать воронку,
которая ходит в seller-analytics-api со своими лимитами.
"""
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import requests

from app.models.user import User
from app.models.wb_orchestrator_state import WbOrchestratorState
from celery_app.tasks import (
    ORCH_OP_FINANCE,
    ORCH_OP_FUNNEL,
    ORCH_STEP_DELAY_SEC,
    _orch_is_blocked,
    wb_orchestrator_tick,
)

USER_ID = "f7a767e1-ce32-4700-8bd1-8e561a11cf6a"


class _FakeQuery:
    def __init__(self, state: WbOrchestratorState):
        self._state = state

    def filter(self, *args: Any, **kwargs: Any) -> "_FakeQuery":
        return self

    def first(self) -> WbOrchestratorState:
        return self._state


class _FakeSession:
    def __init__(self, state: WbOrchestratorState, user: User):
        self._state = state
        self._user = user

    def get(self, model: Any, pk: Any) -> Any:
        return self._user if model is User else None

    def query(self, *args: Any, **kwargs: Any) -> _FakeQuery:
        return _FakeQuery(self._state)

    def add(self, obj: Any) -> None:
        return None

    def commit(self) -> None:
        return None

    def refresh(self, obj: Any) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def _http_429(reset_sec: int) -> requests.HTTPError:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 429
    resp.headers = {"X-RateLimit-Reset": str(reset_sec)}
    return requests.HTTPError(response=resp)


def _state(intents: dict, status: str = "idle") -> WbOrchestratorState:
    st = WbOrchestratorState(user_id=USER_ID, status=status, cooldown_until=None, intents=intents)
    st.updated_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    return st


def _user() -> User:
    return User(id=USER_ID, email="vitalik@example.com", password_hash="x", wb_api_key="key")


def test_finance_429_blocks_only_finance_and_keeps_funnel_going():
    state = _state({"high": {"finance_range": {"date_from": "2026-09-18", "date_to": "2026-09-20"}, "funnel_tail": True}})
    session = _FakeSession(state, _user())

    with (
        patch("celery_app.tasks.SessionLocal", return_value=session),
        patch("celery_app.tasks._orch_finance_missing_step", side_effect=_http_429(314451)),
        patch("celery_app.tasks._orch_funnel_tail_step") as funnel_step,
        patch.object(wb_orchestrator_tick, "apply_async", return_value=None) as apply_async,
    ):
        out = wb_orchestrator_tick(USER_ID)

    assert out["error"] == "wb_retry_scheduled"
    assert out["blocked_op"] == ORCH_OP_FINANCE
    assert out["continue_with"] == [ORCH_OP_FUNNEL]
    funnel_step.assert_not_called()
    # Оркестратор не ушёл в глобальный cooldown, следующий tick — сразу с обычным шагом.
    assert state.status == "scheduled"
    assert state.cooldown_until is None
    apply_async.assert_called_once_with(args=[USER_ID], countdown=ORCH_STEP_DELAY_SEC)
    # Финансовая заявка сохранена, финансы заблокированы, воронка — нет.
    assert state.intents["high"]["finance_range"] == {"date_from": "2026-09-18", "date_to": "2026-09-20"}
    now = datetime.now(timezone.utc)
    assert _orch_is_blocked(state.intents, ORCH_OP_FINANCE, now)
    assert not _orch_is_blocked(state.intents, ORCH_OP_FUNNEL, now)


def test_tick_skips_blocked_finance_and_runs_funnel_tail():
    until = (datetime.now(timezone.utc) + timedelta(hours=11)).isoformat()
    state = _state(
        {
            "high": {"finance_range": {"date_from": "2026-09-18", "date_to": "2026-09-20"}, "funnel_tail": True},
            "blocked": {ORCH_OP_FINANCE: until},
        }
    )
    session = _FakeSession(state, _user())

    with (
        patch("celery_app.tasks.SessionLocal", return_value=session),
        patch("celery_app.tasks._orch_finance_missing_step") as finance_step,
        patch(
            "celery_app.tasks._orch_funnel_tail_step",
            return_value={"ok": True, "message": "step_ok", "day": "2026-09-18", "missing_left": 2},
        ) as funnel_step,
        patch.object(wb_orchestrator_tick, "apply_async", return_value=None),
    ):
        out = wb_orchestrator_tick(USER_ID)

    assert out["ok"] is True
    assert out["step"]["day"] == "2026-09-18"
    finance_step.assert_not_called()
    funnel_step.assert_called_once()
    # Финансовая заявка не потеряна и всё ещё заблокирована.
    assert state.intents["high"]["finance_range"]["date_to"] == "2026-09-20"
    assert state.intents["blocked"][ORCH_OP_FINANCE] == until


def test_only_blocked_work_left_waits_until_window_opens():
    until_dt = datetime.now(timezone.utc) + timedelta(hours=3)
    state = _state(
        {
            "high": {"finance_range": {"date_from": "2026-09-18", "date_to": "2026-09-20"}},
            "blocked": {ORCH_OP_FINANCE: until_dt.isoformat()},
        }
    )
    session = _FakeSession(state, _user())

    with (
        patch("celery_app.tasks.SessionLocal", return_value=session),
        patch("celery_app.tasks._orch_finance_missing_step") as finance_step,
        patch.object(wb_orchestrator_tick, "apply_async", return_value=None) as apply_async,
    ):
        out = wb_orchestrator_tick(USER_ID)

    finance_step.assert_not_called()
    assert out["message"] == "cooldown"
    assert state.status == "cooldown"
    assert state.last_step == "wb_blocked_wait"
    assert 3 * 3600 - 5 <= out["delay_sec"] <= 3 * 3600
    apply_async.assert_called_once()
    assert apply_async.call_args.kwargs["countdown"] == out["delay_sec"]


def test_funnel_429_with_no_other_work_uses_global_cooldown():
    state = _state({"high": {"funnel_tail": True}})
    session = _FakeSession(state, _user())

    with (
        patch("celery_app.tasks.SessionLocal", return_value=session),
        patch("celery_app.tasks._orch_funnel_tail_step", side_effect=_http_429(45)),
        patch.object(wb_orchestrator_tick, "apply_async", return_value=None) as apply_async,
    ):
        out = wb_orchestrator_tick(USER_ID)

    assert out["error"] == "wb_retry_scheduled"
    assert "blocked_op" not in out
    assert state.status == "cooldown"
    assert state.last_step == "wb_http_429 funnel"
    assert _orch_is_blocked(state.intents, ORCH_OP_FUNNEL, datetime.now(timezone.utc))
    apply_async.assert_called_once_with(args=[USER_ID], countdown=out["delay_sec"])
