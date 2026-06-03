from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.routers import dashboard


class _FakeDb:
    def __init__(self, orch=None):
        self.orch = orch

    def query(self, *_args, **_kwargs):
        class _Q:
            def __init__(self, orch):
                self._orch = orch

            def filter(self, *_a, **_k):
                return self

            def first(self):
                return self._orch

        return _Q(self.orch)


def test_dashboard_state_kicks_funnel_tail_when_finance_complete_but_funnel_missing():
    through = date(2026, 4, 27)
    db = _FakeDb()
    user = SimpleNamespace(id="user-1", wb_api_key="key")

    with (
        patch("app.routers.dashboard.funnel_days_needing_repair", return_value=[through]),
        patch("app.routers.dashboard.wb_orchestrator_kick.delay") as mock_delay,
    ):
        out = dashboard._maybe_start_funnel_tail_repair(db, user, through)

    assert out is True
    mock_delay.assert_called_once_with("user-1", {"high": {"funnel_tail": True}})


def test_dashboard_state_wakes_idle_pending_funnel_tail_intent():
    through = date(2026, 4, 27)
    orch = SimpleNamespace(status="idle", intents={"high": {"funnel_tail": True}})
    db = _FakeDb(orch=orch)
    user = SimpleNamespace(id="user-1", wb_api_key="key")

    with (
        patch("app.routers.dashboard.funnel_days_needing_repair", return_value=[through]),
        patch("app.routers.dashboard.wb_orchestrator_kick.delay") as mock_kick,
        patch("app.routers.dashboard.wb_orchestrator_tick.delay") as mock_tick,
    ):
        out = dashboard._maybe_start_funnel_tail_repair(db, user, through)

    assert out is True
    mock_kick.assert_not_called()
    mock_tick.assert_called_once_with("user-1")


def test_dashboard_state_does_not_duplicate_running_funnel_tail_intent():
    through = date(2026, 4, 27)
    orch = SimpleNamespace(status="running", intents={"high": {"funnel_tail": True}})
    db = _FakeDb(orch=orch)
    user = SimpleNamespace(id="user-1", wb_api_key="key")

    with (
        patch("app.routers.dashboard.funnel_days_needing_repair", return_value=[through]),
        patch("app.routers.dashboard.wb_orchestrator_kick.delay") as mock_kick,
        patch("app.routers.dashboard.wb_orchestrator_tick.delay") as mock_tick,
    ):
        out = dashboard._maybe_start_funnel_tail_repair(db, user, through)

    assert out is False
    mock_kick.assert_not_called()
    mock_tick.assert_not_called()


def test_dashboard_state_clears_stale_funnel_tail_when_window_already_complete():
    through = date(2026, 4, 27)
    orch = SimpleNamespace(status="idle", intents={"high": {"funnel_tail": True}})
    db = _FakeDb(orch=orch)
    user = SimpleNamespace(id="user-1", wb_api_key="key")

    with (
        patch("app.routers.dashboard.funnel_days_needing_repair", return_value=[]),
        patch("app.routers.dashboard._clear_funnel_tail_intent") as mock_clear,
        patch("app.routers.dashboard.wb_orchestrator_tick.delay") as mock_tick,
    ):
        out = dashboard._maybe_start_funnel_tail_repair(db, user, through)

    assert out is False
    mock_clear.assert_called_once_with(db, "user-1")
    mock_tick.assert_not_called()
