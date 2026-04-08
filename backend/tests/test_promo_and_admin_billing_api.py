from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models.promo_code import PromoCode
from app.models.user import User


@pytest.fixture
def promo_client(real_db_session):
    """
    Реальная БД (rollback) для тестов промокодов/админки.
    """

    def get_db_override():
        yield real_db_session

    app.dependency_overrides[get_db] = get_db_override
    try:
        real_db_session.close = MagicMock()
        with TestClient(app) as client:
            yield client, real_db_session
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_register_with_promo_code_twice_second_fails(promo_client):
    """
    Промокод lifetime можно использовать один раз:
    - первый register с promo_code -> 200
    - второй register с тем же promo_code -> 400 "Промокод уже был использован"
    """
    client, session = promo_client
    session.add(PromoCode(code="FREE1", is_used=False))
    session.commit()

    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.hash.return_value = "$2b$12$faketesthash"
        r1 = client.post(
            "/auth/register",
            json={"email": "p1@example.com", "password": "secret", "promo_code": "FREE1"},
        )
        assert r1.status_code == 200

        r2 = client.post(
            "/auth/register",
            json={"email": "p2@example.com", "password": "secret", "promo_code": "FREE1"},
        )
        assert r2.status_code == 400
        assert r2.json()["detail"] == "Промокод уже был использован"


def test_admin_grant_lifetime_requires_secret(promo_client):
    client, session = promo_client
    session.add(User(email="life@example.com", password_hash="$2b$12$fake", wb_api_key="k", is_active=True))
    session.commit()

    # без секрета
    with patch.dict(os.environ, {"ADMIN_SECRET": "sec"}):
        r1 = client.post("/billing/admin/grant-lifetime", json={"email": "life@example.com"})
    assert r1.status_code == 403

    # с неправильным секретом
    with patch.dict(os.environ, {"ADMIN_SECRET": "sec"}):
        r2 = client.post(
            "/billing/admin/grant-lifetime",
            json={"email": "life@example.com"},
            headers={"x-admin-secret": "wrong"},
        )
    assert r2.status_code == 403

