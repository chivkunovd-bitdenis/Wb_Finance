from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import get_current_user


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
    app.dependency_overrides[get_current_user] = lambda: MagicMock(id="u", is_active=True)

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

