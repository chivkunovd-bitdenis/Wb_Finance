from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

from app.models.wb_orchestrator_state import WbOrchestratorState
from celery_app.tasks import (
    _intents_after_consumed_step,
    _intents_merge,
    _intents_with_lane,
    wb_orchestrator_kick,
    wb_orchestrator_tick,
)


class _FakeQuery:
    def __init__(self, state: WbOrchestratorState):
        self._state = state

    def filter(self, *args: Any, **kwargs: Any) -> "_FakeQuery":
        return self

    def first(self) -> WbOrchestratorState:
        return self._state


class _FakeSession:
    def __init__(self, state: WbOrchestratorState):
        self._state = state

    def query(self, *args: Any, **kwargs: Any) -> _FakeQuery:
        return _FakeQuery(self._state)

    def add(self, obj: WbOrchestratorState) -> None:
        self._state = obj

    def commit(self) -> None:
        return None

    def refresh(self, obj: WbOrchestratorState) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_intents_merge_recursively_unions_lists_and_overwrites_scalars():
    base = {
        "high": {"funnel_tail": True, "finance_range": {"date_from": "2026-04-01", "date_to": "2026-04-07"}},
        "low": {"finance_backfill_year": 2026, "tags": ["a", "b"]},
    }
    patch = {
        "high": {"finance_range": {"date_from": "2026-04-02", "date_to": "2026-04-08"}},
        "low": {"tags": ["b", "c"]},
    }
    out = _intents_merge(base, patch)
    # recursive overwrite for nested dict scalars
    assert out["high"]["finance_range"]["date_from"] == "2026-04-02"
    assert out["high"]["finance_range"]["date_to"] == "2026-04-08"
    # keep other keys
    assert out["high"]["funnel_tail"] is True
    # list union unique stable
    assert out["low"]["tags"] == ["a", "b", "c"]


def test_intents_with_lane_replaces_or_removes_consumed_lane():
    base = {
        "high": {"funnel_tail": True, "finance_range": {"date_from": "2026-04-01", "date_to": "2026-04-07"}},
        "low": {"finance_backfill_year": 2026},
    }

    out = _intents_with_lane(base, "high", {"funnel_tail": True})

    assert out["high"] == {"funnel_tail": True}
    assert out["low"] == {"finance_backfill_year": 2026}

    out = _intents_with_lane(out, "high", {})

    assert "high" not in out
    assert out["low"] == {"finance_backfill_year": 2026}


def test_consumed_step_preserves_intents_added_by_concurrent_kick():
    """
    Регрессия первичного входа: /dashboard/state мог запустить funnel_tail, а /sync/initial
    почти одновременно добавлял finance_range. Завершение funnel step не должно стирать
    finance_range из более свежего состояния.
    """
    snapshot = {"high": {"funnel_tail": True}}
    current = {
        "high": {
            "funnel_tail": True,
            "finance_range": {"date_from": "2026-04-01", "date_to": "2026-04-30"},
        },
        "low": {"finance_backfill_year": 2026},
    }

    out = _intents_after_consumed_step(
        current,
        snapshot,
        "high",
        {},
    )

    assert out["high"] == {"finance_range": {"date_from": "2026-04-01", "date_to": "2026-04-30"}}
    assert out["low"] == {"finance_backfill_year": 2026}


def test_orchestrator_kick_wakes_expired_cooldown():
    """
    Регрессия: если celery ETA-task потерялась после рестарта, persisted cooldown может уже истечь.
    Новый kick не должен оставлять пользователя в вечном status=cooldown.
    """
    user_id = "fed8d7b9-b816-4252-bd9a-a213d73cd99d"
    state = WbOrchestratorState(
        user_id=user_id,
        status="cooldown",
        cooldown_until=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        intents={"high": {"funnel_tail": True}},
        last_step="wb_http_429",
    )
    session = _FakeSession(state)

    with (
        patch("celery_app.tasks.SessionLocal", return_value=session),
        patch.object(wb_orchestrator_tick, "delay", return_value=None) as mock_delay,
    ):
        out = wb_orchestrator_kick(
            user_id,
            {"high": {"finance_range": {"date_from": "2026-04-28", "date_to": "2026-04-29"}}},
        )

    assert out == {"ok": True, "status": "scheduled"}
    mock_delay.assert_called_once_with(user_id)
    assert state.status == "scheduled"
    assert state.intents["high"]["funnel_tail"] is True
    assert state.intents["high"]["finance_range"] == {"date_from": "2026-04-28", "date_to": "2026-04-29"}


def test_orchestrator_kick_wakes_stale_running():
    """
    Регрессия: если tick умер/потерялся, persisted status=running может зависнуть.
    Новый kick должен уметь разбудить tick без ручного сброса в БД.
    """
    user_id = "fed8d7b9-b816-4252-bd9a-a213d73cd99d"
    state = WbOrchestratorState(
        user_id=user_id,
        status="running",
        cooldown_until=None,
        intents={"high": {"finance_range": {"date_from": "2026-05-30", "date_to": "2026-06-01"}}},
        last_step="finance_missing 2026-05-30..2026-05-30",
    )
    # Mark state as stale.
    state.updated_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    session = _FakeSession(state)

    with (
        patch("celery_app.tasks.SessionLocal", return_value=session),
        patch.object(wb_orchestrator_tick, "delay", return_value=None) as mock_delay,
    ):
        out = wb_orchestrator_kick(
            user_id,
            {"high": {"funnel_tail": True}},
        )

    assert out == {"ok": True, "status": "scheduled"}
    mock_delay.assert_called_once_with(user_id)
    assert state.status == "scheduled"
    assert state.intents["high"]["funnel_tail"] is True

