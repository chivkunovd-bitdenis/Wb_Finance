from __future__ import annotations

from collections.abc import Generator
from datetime import date
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, engine
from app.dependencies import get_current_user, get_store_context
from app.main import app
from app.models.ai_competitor_report import AiCompetitorComparisonReport
from app.models.ai_task import AiTask
from app.models.base import Base
from app.models.user import User
from app.services.ai_daily_analytics_beat_service import run_ai_daily_analytics_beat_cycle

from tests.test_ai_module_api import _ensure_ai_module_schema


USER_BEAT = "00000000-0000-0000-0000-000000000222"


@pytest.fixture
def beat_client() -> Generator[TestClient, None, None]:
    _ensure_ai_module_schema()
    Base.metadata.create_all(bind=engine)

    app.dependency_overrides[get_current_user] = lambda: MagicMock(
        id=USER_BEAT,
        is_active=True,
        is_admin=True,
    )
    app.dependency_overrides[get_store_context] = lambda: MagicMock(
        store_owner=MagicMock(id=USER_BEAT),
    )

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.id == USER_BEAT).first()
        if not existing:
            db.add(
                User(
                    id=USER_BEAT,
                    email="ai-beat@example.com",
                    password_hash="x",
                    wb_api_key=None,
                    is_admin=False,
                    is_active=True,
                )
            )
            db.commit()
    finally:
        db.close()

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_store_context, None)


def _clear_reports(user_id: str) -> None:
    db = SessionLocal()
    try:
        db.query(AiCompetitorComparisonReport).filter(AiCompetitorComparisonReport.user_id == user_id).delete()
        db.query(AiTask).filter(AiTask.user_id == user_id).delete()
        db.commit()
    finally:
        db.close()


def _grant_wb_access(monkeypatch: pytest.MonkeyPatch, tmp_path, user_id: str) -> None:  # noqa: ANN001
    monkeypatch.setenv("WB_PLAYWRIGHT_STORAGE_STATE_DIR", str(tmp_path))
    from app.services.ai_wb_access_service import clear_wb_access_reconnect_required, user_storage_state_path

    clear_wb_access_reconnect_required(user_id=user_id)
    p = user_storage_state_path(user_id=user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"cookies":[],"origins":[],"pad":"' + "x" * 80 + '"}', encoding="utf-8")


def test_ai_daily_analytics_beat_task_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_DAILY_ANALYTICS_BEAT_ENABLED", "0")
    from celery_app.tasks import ai_daily_analytics_beat

    out = ai_daily_analytics_beat()
    assert out["ok"] is True
    assert out["enabled"] is False


def test_ai_daily_analytics_beat_cycle_fetches_when_no_report(
    beat_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    _ = beat_client
    _clear_reports(USER_BEAT)
    _grant_wb_access(monkeypatch, tmp_path, USER_BEAT)
    calls: list[tuple[str, str]] = []

    def _fetch(user_id: str, period: str) -> dict:
        calls.append((user_id, period))
        return {"ok": True, "report_id": "r1"}

    db = SessionLocal()
    try:
        out = run_ai_daily_analytics_beat_cycle(
            db=db,
            today=date(2026, 5, 10),
            period="week",
            limit_user_ids=[USER_BEAT],
            fetch_report=_fetch,
        )
    finally:
        db.close()
    assert out["ok"] is True
    assert out["processed"] == 1
    assert out["fetched"] == 1
    assert calls == [(USER_BEAT, "week")]


def test_ai_daily_analytics_beat_cycle_runs_for_ready_report(
    beat_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    _clear_reports(USER_BEAT)
    _grant_wb_access(monkeypatch, tmp_path, USER_BEAT)
    body = {
        "report_date": "2026-05-10",
        "period": "week",
        "source": "manual",
        "items": [
            {"nm_id": 999001, "metric_code": "ctr", "our_value": 1.0, "competitor_median_value": 2.0},
        ],
    }
    r = beat_client.post("/ai/competitor-reports/import", json=body)
    assert r.status_code == 200
    rep_id = r.json()["id"]

    db = SessionLocal()
    try:
        out = run_ai_daily_analytics_beat_cycle(
            db=db,
            today=date(2026, 5, 10),
            period="week",
            limit_user_ids=[USER_BEAT],
        )
    finally:
        db.close()

    assert out["ok"] is True
    assert out["processed"] == 1
    assert out["skipped_no_report"] == 0
    # sanity: использовали наш отчёт
    assert rep_id


def test_ai_daily_analytics_beat_cycle_ok_false_when_subtask_raises(
    monkeypatch: pytest.MonkeyPatch,
    beat_client: TestClient,
    tmp_path,
) -> None:
    _clear_reports(USER_BEAT)
    _grant_wb_access(monkeypatch, tmp_path, USER_BEAT)
    body = {
        "report_date": "2026-05-10",
        "period": "week",
        "source": "manual",
        "items": [
            {"nm_id": 999002, "metric_code": "ctr", "our_value": 1.0, "competitor_median_value": 2.0},
        ],
    }
    r = beat_client.post("/ai/competitor-reports/import", json=body)
    assert r.status_code == 200

    def _boom(**_kw: object) -> None:
        raise RuntimeError("analytics failed")

    monkeypatch.setattr(
        "app.services.ai_daily_analytics_beat_service.run_daily_analytics",
        _boom,
    )

    db = SessionLocal()
    try:
        out = run_ai_daily_analytics_beat_cycle(
            db=db,
            today=date(2026, 5, 10),
            period="week",
            limit_user_ids=[USER_BEAT],
        )
    finally:
        db.close()

    assert out["ok"] is False
    assert len(out["failures"]) == 1
    assert out["failures"][0]["user_id"] == USER_BEAT
    assert "analytics failed" in out["failures"][0]["error"]


def test_ai_daily_analytics_beat_cycle_creates_refresh_task_for_expired_report(
    beat_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    _ = beat_client
    _clear_reports(USER_BEAT)
    _grant_wb_access(monkeypatch, tmp_path, USER_BEAT)
    db = SessionLocal()
    try:
        db.add(
            AiCompetitorComparisonReport(
                user_id=USER_BEAT,
                report_date=date(2026, 5, 10),
                period="week",
                source="playwright",
                valid_until=date(2026, 5, 13),
                status="ready",
                cost_or_limit_spent=True,
            )
        )
        db.commit()

        out = run_ai_daily_analytics_beat_cycle(
            db=db,
            today=date(2026, 5, 14),
            period="week",
            limit_user_ids=[USER_BEAT],
            fetch_report=lambda _user_id, _period: {"ok": True},
        )
        task = (
            db.query(AiTask)
            .filter(AiTask.user_id == USER_BEAT, AiTask.dedupe_key == "task:competitor_report_refresh:week")
            .first()
        )
    finally:
        db.close()

    assert out["ok"] is True
    assert out["refresh_tasks_created"] == 1
    assert out["fetched"] == 0
    assert task is not None
    assert task.status == "new"


def test_ai_daily_analytics_beat_cycle_fetches_ready_playwright_report(
    beat_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    _ = beat_client
    _clear_reports(USER_BEAT)
    _grant_wb_access(monkeypatch, tmp_path, USER_BEAT)
    calls: list[tuple[str, str]] = []
    db = SessionLocal()
    try:
        db.add(
            AiCompetitorComparisonReport(
                user_id=USER_BEAT,
                report_date=date(2026, 5, 14),
                period="week",
                source="playwright",
                valid_until=date(2026, 5, 17),
                status="ready",
                cost_or_limit_spent=True,
            )
        )
        db.commit()

        def _fetch(user_id: str, period: str) -> dict:
            calls.append((user_id, period))
            return {"ok": True}

        out = run_ai_daily_analytics_beat_cycle(
            db=db,
            today=date(2026, 5, 14),
            period="week",
            limit_user_ids=[USER_BEAT],
            fetch_report=_fetch,
        )
    finally:
        db.close()

    assert out["ok"] is True
    assert out["processed"] == 1
    assert out["fetched"] == 1
    assert calls == [(USER_BEAT, "week")]


def test_ai_daily_analytics_beat_cycle_paid_prompt_creates_refresh_task(
    beat_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    _ = beat_client
    _clear_reports(USER_BEAT)
    _grant_wb_access(monkeypatch, tmp_path, USER_BEAT)
    db = SessionLocal()
    try:
        db.add(
            AiCompetitorComparisonReport(
                user_id=USER_BEAT,
                report_date=date(2026, 5, 14),
                period="week",
                source="playwright",
                valid_until=date(2026, 5, 17),
                status="ready",
                cost_or_limit_spent=True,
            )
        )
        db.commit()
        out = run_ai_daily_analytics_beat_cycle(
            db=db,
            today=date(2026, 5, 14),
            period="week",
            limit_user_ids=[USER_BEAT],
            fetch_report=lambda _user_id, _period: {"ok": False, "error": "paid_reopen_required"},
        )
        task = (
            db.query(AiTask)
            .filter(AiTask.user_id == USER_BEAT, AiTask.dedupe_key == "task:competitor_report_refresh:week")
            .first()
        )
    finally:
        db.close()

    assert out["ok"] is True
    assert out["refresh_tasks_created"] == 1
    assert task is not None
    assert task.task_type == "competitor_report_refresh"


def test_ai_daily_analytics_beat_cycle_auth_failure_creates_access_task(
    beat_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:  # noqa: ANN001
    _ = beat_client
    _clear_reports(USER_BEAT)
    _grant_wb_access(monkeypatch, tmp_path, USER_BEAT)
    db = SessionLocal()
    try:
        db.add(
            AiCompetitorComparisonReport(
                user_id=USER_BEAT,
                report_date=date(2026, 5, 14),
                period="week",
                source="playwright",
                valid_until=date(2026, 5, 17),
                status="ready",
                cost_or_limit_spent=True,
            )
        )
        db.commit()
        out = run_ai_daily_analytics_beat_cycle(
            db=db,
            today=date(2026, 5, 14),
            period="week",
            limit_user_ids=[USER_BEAT],
            fetch_report=lambda _user_id, _period: {"ok": False, "error": "auth_failed"},
        )
        task = (
            db.query(AiTask)
            .filter(AiTask.user_id == USER_BEAT, AiTask.dedupe_key == "task:wb_access_grant")
            .first()
        )
    finally:
        db.close()

    assert out["ok"] is True
    assert out["access_tasks_created"] == 1
    assert task is not None
    assert task.task_type == "wb_access_grant"
