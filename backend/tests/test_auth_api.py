"""
Тесты эндпоинтов авторизации: register, login, /me.
БД подменена на mock-сессию, в реальный PostgreSQL не пишем.
hash_password/verify_password мокаем, чтобы не дергать bcrypt (в контейнере падает на 72 байт).
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import get_db

# Фиксированный «хэш» для тестов — verify_password в роуте будет замокан
FAKE_HASH = "$2b$12$faketesthash"


def _mock_get_db_register():
    """Сессия для регистрации: пользователя с таким email нет, после add/commit/refresh — есть id."""
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    added = []

    def fake_add(x):
        added.append(x)

    def fake_refresh(x):
        x.id = "test-user-id-123"
        if not getattr(x, "email", None) and added:
            x.email = getattr(added[0], "email", "test@example.com")

    session.add = fake_add
    session.commit = MagicMock()
    session.refresh = fake_refresh
    session.close = MagicMock()
    try:
        yield session
    finally:
        pass


def _mock_get_db_login_exists(wb_api_key: str = "wb-key"):
    """Сессия для логина: пользователь есть, пароль уже захэширован (фейк-хэш, без bcrypt)."""
    from app.models.user import User

    session = MagicMock()
    user = User(
        id="user-456",
        email="login@example.com",
        password_hash=FAKE_HASH,
        wb_api_key=wb_api_key,
        is_active=True,
    )
    session.query.return_value.filter.return_value.first.return_value = user
    session.get.return_value = user
    try:
        yield session
    finally:
        pass


def _mock_get_db_login_not_found():
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    try:
        yield session
    finally:
        pass


@pytest.fixture
def client_register():
    # Мокаем pwd_context в модуле security — тогда hash_password/verify_password не дергают реальный bcrypt
    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.hash.return_value = FAKE_HASH
        app.dependency_overrides[get_db] = _mock_get_db_register
        try:
            with TestClient(app) as c:
                yield c
        finally:
            app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client_login():
    def _fake_verify(plain: str, hashed: str) -> bool:
        return plain == "password123" and hashed == FAKE_HASH
    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.verify.side_effect = _fake_verify
        app.dependency_overrides[get_db] = _mock_get_db_login_exists
        try:
            with TestClient(app) as c:
                yield c
        finally:
            app.dependency_overrides.pop(get_db, None)


def test_register_returns_200_and_user(client_register: TestClient):
    r = client_register.post(
        "/auth/register",
        json={"email": "new@example.com", "password": "secret", "wb_api_key": "key"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["email"] == "new@example.com"
    assert "id" in data
    assert "password" not in data


def test_login_returns_200_and_token(client_login: TestClient):
    r = client_login.post(
        "/auth/login",
        json={"email": "login@example.com", "password": "password123"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    assert data.get("token_type") == "bearer"


def test_login_wrong_password_returns_401(client_login: TestClient):
    r = client_login.post(
        "/auth/login",
        json={"email": "login@example.com", "password": "wrong"},
    )
    assert r.status_code == 401


def test_me_without_token_returns_401(client_register: TestClient):
    r = client_register.get("/auth/me")
    assert r.status_code == 401
