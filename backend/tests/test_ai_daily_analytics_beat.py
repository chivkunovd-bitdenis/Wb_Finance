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
        db.commit()
    finally:
        db.close()


def test_ai_daily_analytics_beat_task_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_DAILY_ANALYTICS_BEAT_ENABLED", "0")
    from celery_app.tasks import ai_daily_analytics_beat

    out = ai_daily_analytics_beat()
    assert out["ok"] is True
    assert out["enabled"] is False


def test_ai_daily_analytics_beat_cycle_skips_without_report(beat_client: TestClient) -> None:
    _ = beat_client
    _clear_reports(USER_BEAT)
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
    assert out["processed"] == 0
    assert out["skipped_no_report"] == 1


def test_ai_daily_analytics_beat_cycle_runs_for_ready_report(beat_client: TestClient) -> None:
    _clear_reports(USER_BEAT)
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
) -> None:
    _clear_reports(USER_BEAT)
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
