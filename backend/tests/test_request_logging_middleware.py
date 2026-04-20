from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app


@pytest.fixture
def client_with_mock_db():
    def _mock_get_db():
        session = MagicMock()
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _mock_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_request_id_header_present_on_200(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    rid = r.headers.get("X-Request-ID")
    assert rid is not None
    assert isinstance(rid, str)
    assert len(rid) >= 16


def test_request_id_header_present_on_401(client_with_mock_db: TestClient):
    r = client_with_mock_db.get("/auth/me")
    assert r.status_code == 401
    rid = r.headers.get("X-Request-ID")
    assert rid is not None
    assert isinstance(rid, str)
    assert len(rid) >= 16

