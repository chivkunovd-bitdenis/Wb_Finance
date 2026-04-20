from __future__ import annotations

from types import SimpleNamespace
from typing import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app


@pytest.fixture
def client_with_mock_db() -> Generator[tuple[TestClient, MagicMock], None, None]:
    session = MagicMock()

    def _mock_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _mock_get_db
    try:
        with TestClient(app) as c:
            yield c, session
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_admin_grant_lifetime_forbidden_without_secret(client_with_mock_db: tuple[TestClient, MagicMock]) -> None:
    client, _db = client_with_mock_db
    r = client.post("/billing/admin/grant-lifetime", json={"email": "user@example.com"})
    assert r.status_code == 403


def test_admin_grant_lifetime_grants_by_normalized_email(
    client_with_mock_db: tuple[TestClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = client_with_mock_db

    # Router imports ADMIN_SECRET and grant_lifetime into its module namespace.
    from app.routers import billing as billing_router
    from app.services import billing_service

    monkeypatch.setattr(billing_service, "ADMIN_SECRET", "secret", raising=True)
    monkeypatch.setattr(billing_router, "ADMIN_SECRET", "secret", raising=True)

    called: dict[str, str] = {}

    def _fake_grant_lifetime(_db, user_id: str):
        called["user_id"] = user_id
        return SimpleNamespace(user_id=user_id, status="lifetime")

    monkeypatch.setattr(billing_router, "grant_lifetime", _fake_grant_lifetime, raising=True)

    user = SimpleNamespace(id="u1", email="user@example.com")
    db.query.return_value.filter.return_value.first.return_value = user

    r = client.post(
        "/billing/admin/grant-lifetime",
        headers={"X-Admin-Secret": "secret"},
        json={"email": " User@Example.com "},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["email"] == "user@example.com"
    assert called["user_id"] == "u1"
    db.commit.assert_called_once()

