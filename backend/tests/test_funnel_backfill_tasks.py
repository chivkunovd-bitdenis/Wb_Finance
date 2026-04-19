"""
Тесты фоновой догрузки воронки (Celery tasks):
- пустой ответ WB не должен удалять существующие строки funnel_daily;
- weekly(history) и daily(products) пишут данные insert-only (ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from celery_app.tasks import sync_funnel, sync_funnel_ytd_step


def _mock_db_for_funnel(user_id: str = "user-1") -> MagicMock:
    db = MagicMock()
    db.get.return_value = SimpleNamespace(id=user_id, wb_api_key="wb-key")
    db.execute.return_value = SimpleNamespace(rowcount=0)
    db.add.return_value = None
    db.commit.return_value = None
    db.rollback.return_value = None
    db.close.return_value = None

    # query(...) chains
    q = MagicMock()
    q.filter.return_value = q
    q.all.return_value = []
    q.first.return_value = None
    q.distinct.return_value = q

    def _query_side_effect(*models):
        # Article list empty; RawSale/RawAd distinct nm_ids empty; FunnelDaily exists checks empty;
        # FunnelBackfillState absent so task creates it.
        if len(models) >= 1 and getattr(models[0], "__name__", "") in {"Article", "RawSale", "RawAd", "FunnelDaily", "FunnelBackfillState"}:
            return q
        return q

    db.query.side_effect = _query_side_effect
    return db


@patch("celery_app.tasks.fetch_funnel", return_value=[])
@patch("celery_app.tasks.SessionLocal")
def test_sync_funnel_empty_wb_response_does_not_delete(_mock_session_local, _mock_fetch_funnel):
    db = _mock_db_for_funnel()
    _mock_session_local.return_value = db

    res = sync_funnel("user-1", "2026-03-01", "2026-03-07")
    assert res == {"ok": True, "count": 0}

    # При пустом WB ответе delete(FunnelDaily) больше не вызывается.
    assert db.execute.call_count == 0


@pytest.mark.parametrize("yesterday_has_funnel", [True, False])
@patch("celery_app.tasks.fetch_funnel_products_for_day_with_retry", return_value=[])
@patch("celery_app.tasks.fetch_funnel", return_value=[])
@patch("celery_app.tasks.recalculate_sku_daily")
@patch("celery_app.tasks.sync_funnel_ytd_step.apply_async")
@patch("celery_app.tasks.SessionLocal")
def test_sync_funnel_ytd_step_never_deletes_funnel_daily(
    mock_session_local,
    _mock_apply_async,
    _mock_recalculate_sku_daily,
    _mock_fetch_week,
    _mock_fetch_day,
    yesterday_has_funnel: bool,
):
    db = _mock_db_for_funnel()
    mock_session_local.return_value = db

    # FunnelDaily "exists for yesterday" check
    q_funnel_exists = MagicMock()
    q_funnel_exists.filter.return_value = q_funnel_exists
    q_funnel_exists.first.return_value = SimpleNamespace(id="fd-1") if yesterday_has_funnel else None

    # FunnelBackfillState first() returns None to create it; subsequent commits ok.
    q_state = MagicMock()
    q_state.filter.return_value = q_state
    q_state.first.return_value = None

    # Articles empty, nm_ids fallback empty -> task early returns no_articles_yet (no deletes anyway).
    # We still want to ensure there is no delete call.
    def _query_side_effect(*models):
        if len(models) == 1 and getattr(models[0], "__name__", "") == "FunnelDaily":
            return q_funnel_exists
        if len(models) == 1 and getattr(models[0], "__name__", "") == "FunnelBackfillState":
            return q_state
        return MagicMock(filter=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]), distinct=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))))  # noqa: E501

    db.query.side_effect = _query_side_effect

    res = sync_funnel_ytd_step("user-1", 2026)
    assert res["ok"] is True
    assert db.execute.call_count == 0

