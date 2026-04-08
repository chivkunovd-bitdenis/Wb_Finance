from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models.user import User


FAKE_HASH = "$2b$12$faketesthash"


@pytest.fixture
def client_auth_negative():
    """
    Клиент для негативных кейсов auth.

    БД подменяем на mock-сессию, чтобы не зависеть от Postgres.
    """
    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.hash.return_value = FAKE_HASH

        def _db():
            session = MagicMock()
            try:
                yield session
            finally:
                pass

        app.dependency_overrides[get_db] = _db
        try:
            with TestClient(app) as c:
                yield c
        finally:
            app.dependency_overrides.pop(get_db, None)


def test_register_duplicate_email_returns_400(client_auth_negative: TestClient):
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = User(
        id="u1",
        email="dup@example.com",
        password_hash=FAKE_HASH,
        wb_api_key=None,
        is_active=True,
    )

    def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    try:
        r = client_auth_negative.post(
            "/auth/register",
            json={"email": "dup@example.com", "password": "secret"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "Пользователь с таким email уже существует"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_register_invalid_email_returns_422(client_auth_negative: TestClient):
    r = client_auth_negative.post(
        "/auth/register",
        json={"email": "not-an-email", "password": "secret"},
    )
    assert r.status_code == 422


def test_login_user_not_found_returns_401(client_auth_negative: TestClient):
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None

    def _db():
        yield session

    # verify не должен быть вызван, но на всякий случай держим consistent поведение
    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.verify.return_value = False
        app.dependency_overrides[get_db] = _db
        try:
            r = client_auth_negative.post(
                "/auth/login",
                json={"email": "missing@example.com", "password": "any"},
            )
            assert r.status_code == 401
        finally:
            app.dependency_overrides.pop(get_db, None)


def test_login_inactive_user_returns_403(client_auth_negative: TestClient):
    session = MagicMock()
    user = User(
        id="u-inactive",
        email="inactive@example.com",
        password_hash=FAKE_HASH,
        wb_api_key=None,
        is_active=False,
    )
    session.query.return_value.filter.return_value.first.return_value = user

    def _db():
        yield session

    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.verify.return_value = True
        app.dependency_overrides[get_db] = _db
        try:
            r = client_auth_negative.post(
                "/auth/login",
                json={"email": "inactive@example.com", "password": "password123"},
            )
            assert r.status_code == 403
            assert r.json()["detail"] == "Аккаунт деактивирован"
        finally:
            app.dependency_overrides.pop(get_db, None)

