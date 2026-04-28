from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.routers import dashboard


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def distinct(self):
        return self

    def all(self):
        return self._result

    def first(self):
        return self._result


class _FakeDb:
    def __init__(self, *, present_dates, orch=None):
        self.present_dates = present_dates
        self.orch = orch
        self.calls = 0

    def query(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return _FakeQuery([(d,) for d in self.present_dates])
        return _FakeQuery(self.orch)


def test_dashboard_state_kicks_funnel_tail_when_finance_complete_but_funnel_missing():
    through = date(2026, 4, 27)
    present_dates = [through - timedelta(days=i) for i in range(1, 7)]
    db = _FakeDb(present_dates=present_dates)
    user = SimpleNamespace(id="user-1", wb_api_key="key")

    with patch("app.routers.dashboard.wb_orchestrator_kick.delay") as mock_delay:
        out = dashboard._maybe_start_funnel_tail_repair(db, user, through)

    assert out is True
    mock_delay.assert_called_once_with("user-1", {"high": {"funnel_tail": True}})


def test_dashboard_state_wakes_idle_pending_funnel_tail_intent():
    through = date(2026, 4, 27)
    present_dates = [through - timedelta(days=i) for i in range(1, 7)]
    orch = SimpleNamespace(status="idle", intents={"high": {"funnel_tail": True}})
    db = _FakeDb(present_dates=present_dates, orch=orch)
    user = SimpleNamespace(id="user-1", wb_api_key="key")

    with (
        patch("app.routers.dashboard.wb_orchestrator_kick.delay") as mock_kick,
        patch("app.routers.dashboard.wb_orchestrator_tick.delay") as mock_tick,
    ):
        out = dashboard._maybe_start_funnel_tail_repair(db, user, through)

    assert out is True
    mock_kick.assert_not_called()
    mock_tick.assert_called_once_with("user-1")


def test_dashboard_state_does_not_duplicate_running_funnel_tail_intent():
    through = date(2026, 4, 27)
    present_dates = [through - timedelta(days=i) for i in range(1, 7)]
    orch = SimpleNamespace(status="running", intents={"high": {"funnel_tail": True}})
    db = _FakeDb(present_dates=present_dates, orch=orch)
    user = SimpleNamespace(id="user-1", wb_api_key="key")

    with (
        patch("app.routers.dashboard.wb_orchestrator_kick.delay") as mock_kick,
        patch("app.routers.dashboard.wb_orchestrator_tick.delay") as mock_tick,
    ):
        out = dashboard._maybe_start_funnel_tail_repair(db, user, through)

    assert out is False
    mock_kick.assert_not_called()
    mock_tick.assert_not_called()


def test_dashboard_state_wakes_stale_funnel_tail_when_window_already_complete():
    through = date(2026, 4, 27)
    present_dates = [through - timedelta(days=i) for i in range(0, 7)]
    orch = SimpleNamespace(status="idle", intents={"high": {"funnel_tail": True}})
    db = _FakeDb(present_dates=present_dates, orch=orch)
    user = SimpleNamespace(id="user-1", wb_api_key="key")

    with patch("app.routers.dashboard.wb_orchestrator_tick.delay") as mock_tick:
        out = dashboard._maybe_start_funnel_tail_repair(db, user, through)

    assert out is True
    mock_tick.assert_called_once_with("user-1")
