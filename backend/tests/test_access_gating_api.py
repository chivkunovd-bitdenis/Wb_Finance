from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db import get_db
from app.main import app
from app.models.subscription import Subscription
from app.models.user import User


@pytest.fixture
def authenticated_client(real_db_session):
    """
    Клиент с JWT и реальной БД.

    Важно: real_db_session откатывается после теста (см. conftest.py).
    """
    user = User(
        email="access-gating@example.com",
        password_hash="$2b$12$fake",
        wb_api_key="test-wb-key",
        is_active=True,
    )
    real_db_session.add(user)
    real_db_session.commit()
    real_db_session.refresh(user)
    user_id = str(user.id)
    token = create_access_token(data={"sub": user_id})

    def get_db_override():
        yield real_db_session

    app.dependency_overrides[get_db] = get_db_override
    try:
        # некоторые задачи/сервисы вызывают db.close(); в тесте не закрываем общую session
        real_db_session.close = MagicMock()
        with TestClient(app) as client:
            yield client, real_db_session, user_id, token
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_access_gating_allows_active_trial(authenticated_client):
    """
    Контракт: для защищённых эндпоинтов (не /auth|/billing|/health) доступ разрешён,
    если trial активен.
    """
    client, _session, _user_id, token = authenticated_client
    r = client.get("/dashboard/state", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_access_gating_blocks_expired_trial_returns_402(authenticated_client):
    """
    Регрессия/критичный контракт: истёкший trial должен блокировать защищённые эндпоинты кодом 402.
    """
    client, session, user_id, token = authenticated_client
    now = datetime.now(UTC)
    session.add(
        Subscription(
            user_id=user_id,
            status="trial",
            trial_started_at=now - timedelta(days=10),
            trial_ends_at=now - timedelta(days=1),
            auto_renew=True,
            provider="yookassa",
        )
    )
    session.commit()

    r = client.get("/dashboard/state", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 402
    data = r.json()
    assert isinstance(data, dict)
    assert "detail" in data

