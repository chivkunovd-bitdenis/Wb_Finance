from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import get_current_user
from app.db import SessionLocal
from app.models.user import User


class _FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Generator[TestClient, None, None]:
    # Auth bypass
    user_id = "00000000-0000-0000-0000-000000000002"
    app.dependency_overrides[get_current_user] = lambda: MagicMock(
        id="00000000-0000-0000-0000-000000000002",
        is_active=True,
        is_admin=True,
    )

    # Ensure user exists in DB (chat tables have FK to users.id)
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.id == user_id).first()
        if not existing:
            db.add(
                User(
                    id=user_id,
                    email="admin@example.com",
                    password_hash="x",
                    wb_api_key=None,
                    is_admin=True,
                    is_active=True,
                )
            )
            db.commit()
    finally:
        db.close()

    # Redirect offer storage to temp dir
    monkeypatch.setenv("OFFER_DATA_DIR", str(tmp_path / "offers"))

    # In-memory redis state
    fake_redis = _FakeRedis()
    # Важно: offer_index_state импортирует get_redis как имя (from ... import get_redis),
    # поэтому патчим оба места.
    monkeypatch.setattr("app.core.redis_client.get_redis", lambda: fake_redis)
    monkeypatch.setattr("app.services.offer_index_state.get_redis", lambda: fake_redis)

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.pop(get_current_user, None)


def test_offer_upload_rejects_unknown_extension(client: TestClient) -> None:
    r = client.post(
        "/offer/upload",
        files={"file": ("offer.docx", b"hello world " * 20, "application/octet-stream")},
    )
    assert r.status_code == 400
    assert "Поддерживаются файлы" in r.text


def test_offer_ask_requires_ready_index(client: TestClient) -> None:
    r = client.post("/offer/ask", json={"question": "что-то"})
    assert r.status_code == 409


def test_offer_upload_enqueues_indexing_task(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch celery task delay so we don't run real indexing.
    import celery_app.tasks as tasks

    called = {"ok": False}

    def _delay(_path: str, _version: str) -> None:
        called["ok"] = True

    monkeypatch.setattr(tasks.index_offer_document, "delay", _delay)

    r = client.post(
        "/offer/upload",
        files={"file": ("offer.txt", b"some offer text " * 200, "text/plain")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "indexing"
    assert isinstance(data["next_version"], str)
    assert len(data["next_version"]) >= 8
    assert called["ok"] is True


def test_offer_ask_happy_path(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Seed redis state to ready
    from app.services.offer_index_state import set_offer_index_state, OfferIndexState

    set_offer_index_state(
        OfferIndexState(
            status="ready",
            active_version="v1",
            indexed_at="2026-05-04T00:00:00+00:00",
            error_message=None,
        )
    )

    # Важно: в роутере ask_offer импортирован как имя (from ... import ask_offer),
    # поэтому патчим именно app.routers.offer_ai.ask_offer.
    monkeypatch.setattr(
        "app.routers.offer_ai.ask_offer",
        lambda *, question, active_version: (
            "ответ",
            [MagicMock(chunk_id=1, score=0.9, text="кусок")],
        ),
    )

    r = client.post("/offer/ask", json={"question": "вопрос"})
    assert r.status_code == 200
    data = r.json()
    assert data["answer"] == "ответ"
    assert data["active_version"] == "v1"
    assert isinstance(data["sources"], list)
    assert data["sources"][0]["chunk_id"] == 1


def test_offer_chat_requires_admin(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Seed redis state to ready
    from app.services.offer_index_state import set_offer_index_state, OfferIndexState

    set_offer_index_state(
        OfferIndexState(
            status="ready",
            active_version="v1",
            indexed_at="2026-05-04T00:00:00+00:00",
            error_message=None,
        )
    )

    # Override auth for this test: non-admin user
    app.dependency_overrides[get_current_user] = lambda: MagicMock(
        id="00000000-0000-0000-0000-000000000002",
        is_active=True,
        is_admin=False,
    )

    r = client.post("/offer/chat/start", json={"chat_id": "00000000-0000-0000-0000-000000000001"})
    assert r.status_code == 403


def test_offer_chat_happy_path(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.offer_index_state import set_offer_index_state, OfferIndexState

    set_offer_index_state(
        OfferIndexState(
            status="ready",
            active_version="v1",
            indexed_at="2026-05-04T00:00:00+00:00",
            error_message=None,
        )
    )

    # avoid real llm condense + real rag
    monkeypatch.setattr(
        "app.services.offer_chat_service.condense_question",
        lambda *, history, message: type("X", (), {"standalone_question": message, "need_clarification": False, "clarifying_question": None})(),
    )
    monkeypatch.setattr(
        "app.services.offer_chat_service.ask_offer",
        lambda *, question, active_version: (
            "ответ",
            [MagicMock(chunk_id=1, score=0.9, text="кусок", metadata={"chunk_id": 1})],
        ),
    )

    r0 = client.post("/offer/chat/start", json={"chat_id": "00000000-0000-0000-0000-000000000001"})
    assert r0.status_code == 200

    r = client.post("/offer/chat/ask", json={"chat_id": "00000000-0000-0000-0000-000000000001", "message": "вопрос"})
    assert r.status_code == 200
    data = r.json()
    assert data["chat_id"] == "00000000-0000-0000-0000-000000000001"
    assert data["answer"] == "ответ"
    assert data["active_version"] == "v1"
    assert data["need_clarification"] is False
    assert data["sources"][0]["chunk_id"] == 1

