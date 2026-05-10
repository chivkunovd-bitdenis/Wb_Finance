from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text

from app.db import SessionLocal, engine
from app.dependencies import get_current_user
from app.main import app
from app.models.ai_hypothesis import AiHypothesis
from app.models.ai_task import AiTask
from app.models.base import Base
from app.models.user import User


@lru_cache(maxsize=1)
def _ensure_ai_module_schema() -> None:
    """
    These API tests use SessionLocal (real DB). If the local dev DB already has older tables,
    SQLAlchemy create_all() will NOT add missing columns/constraints. We run Alembic upgrade to head
    once to keep schema in sync with the models used in tests.

    Important: local DB may already have ai_* tables created earlier without Alembic stamping.
    We therefore apply minimal additive DDL with IF NOT EXISTS to avoid destructive drops.
    """
    ddl = [
        # fingerprint columns
        "ALTER TABLE ai_tasks ADD COLUMN IF NOT EXISTS fingerprint VARCHAR(80)",
        "ALTER TABLE ai_hypotheses ADD COLUMN IF NOT EXISTS fingerprint VARCHAR(80)",
        # indexes
        "CREATE INDEX IF NOT EXISTS ix_ai_tasks_fingerprint ON ai_tasks (fingerprint)",
        "CREATE INDEX IF NOT EXISTS ix_ai_hypotheses_fingerprint ON ai_hypotheses (fingerprint)",
        # unique per user (use unique index to allow IF NOT EXISTS)
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_tasks_user_fingerprint ON ai_tasks (user_id, fingerprint)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_hypotheses_user_fingerprint ON ai_hypotheses (user_id, fingerprint)",
        # daily log table
        """
        CREATE TABLE IF NOT EXISTS ai_hypothesis_daily_log (
            id UUID NOT NULL,
            hypothesis_id UUID NOT NULL REFERENCES ai_hypotheses(id) ON DELETE CASCADE,
            day DATE NOT NULL,
            happened TEXT NULL,
            changed TEXT NULL,
            unchanged TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_ai_hypothesis_daily_log PRIMARY KEY (id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_ai_hypothesis_daily_log_hypothesis_id ON ai_hypothesis_daily_log (hypothesis_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_hypothesis_daily_log_hyp_day ON ai_hypothesis_daily_log (hypothesis_id, day)",
    ]
    with engine.begin() as conn:
        for stmt in ddl:
            conn.execute(text(stmt))


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    _ensure_ai_module_schema()
    # Ensure tables exist for local runs (CI/dev).
    Base.metadata.create_all(bind=engine)

    user_id = "00000000-0000-0000-0000-000000000111"
    app.dependency_overrides[get_current_user] = lambda: MagicMock(
        id=user_id,
        is_active=True,
        is_admin=True,
    )

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.id == user_id).first()
        if not existing:
            db.add(
                User(
                    id=user_id,
                    email="ai-module@example.com",
                    password_hash="x",
                    wb_api_key=None,
                    is_admin=True,
                    is_active=True,
                )
            )
            db.commit()
    finally:
        db.close()

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.pop(get_current_user, None)


def _seed_task(user_id: str) -> str:
    db = SessionLocal()
    try:
        t = AiTask(
            user_id=user_id,
            nm_id=123,
            task_type="restock",
            title="Дозакупить товар",
            description="Остатка хватит < 14 дней",
            reason="stock_days_left < 14",
            priority=10,
            status="new",
            fingerprint=f"task:restock:123:{uuid.uuid4()}",
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        return str(t.id)
    finally:
        db.close()


def _seed_hypothesis(user_id: str) -> str:
    db = SessionLocal()
    try:
        h = AiHypothesis(
            user_id=user_id,
            nm_id=123,
            hypothesis_type="content_change",
            title="Поменять контент",
            description="Воронка ниже медианы",
            status="draft",
            fingerprint=f"hyp:content_change:123:{uuid.uuid4()}",
        )
        db.add(h)
        db.commit()
        db.refresh(h)
        return str(h.id)
    finally:
        db.close()


def test_ai_tasks_list_and_patch(client: TestClient) -> None:
    user_id = "00000000-0000-0000-0000-000000000111"
    task_id = _seed_task(user_id)

    r = client.get("/ai/tasks")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["items"], list)
    assert any(x["id"] == task_id for x in data["items"])

    r2 = client.patch(f"/ai/tasks/{task_id}", json={"status": "in_progress"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "in_progress"
    assert r2.json()["started_at"] is not None

    r3 = client.patch(f"/ai/tasks/{task_id}", json={"status": "completed"})
    assert r3.status_code == 200
    assert r3.json()["status"] == "completed"
    assert r3.json()["completed_at"] is not None


def test_ai_task_invalid_transition_returns_409(client: TestClient) -> None:
    user_id = "00000000-0000-0000-0000-000000000111"
    task_id = _seed_task(user_id)

    # new -> completed is not allowed in MVP-1
    r = client.patch(f"/ai/tasks/{task_id}", json={"status": "completed"})
    assert r.status_code == 409


def test_ai_hypothesis_start_and_finish(client: TestClient) -> None:
    user_id = "00000000-0000-0000-0000-000000000111"
    hypothesis_id = _seed_hypothesis(user_id)

    r0 = client.get("/ai/hypotheses")
    assert r0.status_code == 200
    assert any(x["id"] == hypothesis_id for x in r0.json()["items"])

    r1 = client.post(f"/ai/hypotheses/{hypothesis_id}/start")
    assert r1.status_code == 200

    r2 = client.get(f"/ai/hypotheses/{hypothesis_id}")
    assert r2.status_code == 200
    assert r2.json()["status"] == "running"
    assert r2.json()["started_at"] is not None

    r3 = client.post(f"/ai/hypotheses/{hypothesis_id}/finish", json={"result_summary": "ok"})
    assert r3.status_code == 200

    r4 = client.get(f"/ai/hypotheses/{hypothesis_id}")
    assert r4.status_code == 200
    assert r4.json()["status"] == "finished"
    assert r4.json()["ended_at"] is not None
    assert r4.json()["result_summary"] == "ok"


def test_ai_hypothesis_start_twice_returns_409(client: TestClient) -> None:
    user_id = "00000000-0000-0000-0000-000000000111"
    hypothesis_id = _seed_hypothesis(user_id)

    r1 = client.post(f"/ai/hypotheses/{hypothesis_id}/start")
    assert r1.status_code == 200

    r2 = client.post(f"/ai/hypotheses/{hypothesis_id}/start")
    assert r2.status_code == 409


def test_ai_hypothesis_daily_log_requires_running(client: TestClient) -> None:
    user_id = "00000000-0000-0000-0000-000000000111"
    hypothesis_id = _seed_hypothesis(user_id)

    # draft -> daily log not allowed
    r0 = client.post(
        f"/ai/hypotheses/{hypothesis_id}/daily-log",
        json={"day": "2026-05-10", "happened": "x", "changed": "y", "unchanged": "z"},
    )
    assert r0.status_code == 409

    r1 = client.post(f"/ai/hypotheses/{hypothesis_id}/start")
    assert r1.status_code == 200

    r2 = client.post(
        f"/ai/hypotheses/{hypothesis_id}/daily-log",
        json={"day": "2026-05-10", "happened": "h1", "changed": "c1", "unchanged": "u1"},
    )
    assert r2.status_code == 200
    items = r2.json()["items"]
    assert len(items) == 1
    assert items[0]["day"] == "2026-05-10"
    assert items[0]["happened"] == "h1"

    # Upsert same day -> update, still 1 item
    r3 = client.post(
        f"/ai/hypotheses/{hypothesis_id}/daily-log",
        json={"day": "2026-05-10", "happened": "h2", "changed": "c2", "unchanged": "u2"},
    )
    assert r3.status_code == 200
    items2 = r3.json()["items"]
    assert len(items2) == 1
    assert items2[0]["happened"] == "h2"


def test_ai_fingerprint_unique_per_user(client: TestClient) -> None:
    user_id = "00000000-0000-0000-0000-000000000111"
    db = SessionLocal()
    try:
        f = f"dup:{uuid.uuid4()}"
        t1 = AiTask(user_id=user_id, task_type="x", title="t", priority=0, status="new", fingerprint=f)
        t2 = AiTask(user_id=user_id, task_type="y", title="t2", priority=0, status="new", fingerprint=f)
        db.add(t1)
        db.commit()
        db.add(t2)
        with pytest.raises(IntegrityError):
            db.commit()

        db.rollback()
        fh = f"dup_h:{uuid.uuid4()}"
        h1 = AiHypothesis(
            user_id=user_id,
            hypothesis_type="a",
            title="h",
            status="draft",
            fingerprint=fh,
        )
        h2 = AiHypothesis(
            user_id=user_id,
            hypothesis_type="b",
            title="h2",
            status="draft",
            fingerprint=fh,
        )
        db.add(h1)
        db.commit()
        db.add(h2)
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()

