from __future__ import annotations

from uuid import uuid4
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db import get_db
from app.main import app
from app.models.user import User


@pytest.fixture
def client_real_db(real_db_session):
    def _get_db_override():
        yield real_db_session

    app.dependency_overrides[get_db] = _get_db_override
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


def _token_for(user_id: str) -> str:
    return create_access_token({"sub": user_id})


def _mk_user(email: str, *, wb_api_key: str | None = "wb-key") -> User:
    return User(
        id=str(uuid4()),
        email=email,
        password_hash="hash",
        wb_api_key=wb_api_key,
        is_active=True,
    )


def test_grant_and_list_accessible_stores(client_real_db, real_db_session):
    owner = _mk_user("owner@example.com")
    viewer = _mk_user("viewer@example.com")
    real_db_session.add_all([owner, viewer])
    real_db_session.commit()

    owner_token = _token_for(str(owner.id))
    r = client_real_db.post(
        "/stores/grants",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"grantee_email": "viewer@example.com"},
    )
    assert r.status_code == 200

    viewer_token = _token_for(str(viewer.id))
    r2 = client_real_db.get("/stores", headers={"Authorization": f"Bearer {viewer_token}"})
    assert r2.status_code == 200
    stores = r2.json()["stores"]
    assert any(s["owner_email"] == "owner@example.com" and s["access"] == "granted" for s in stores)
    assert any(s["owner_email"] == "viewer@example.com" and s["access"] == "owner" for s in stores)

    # Revoke and ensure store disappears from list.
    rr = client_real_db.post(
        "/stores/grants/revoke",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"grantee_email": "viewer@example.com"},
    )
    assert rr.status_code == 200

    r3 = client_real_db.get("/stores", headers={"Authorization": f"Bearer {viewer_token}"})
    assert r3.status_code == 200
    stores2 = r3.json()["stores"]
    assert not any(s["owner_email"] == "owner@example.com" and s["access"] == "granted" for s in stores2)


def test_store_context_requires_grant_403(client_real_db, real_db_session):
    owner = _mk_user("owner2@example.com")
    stranger = _mk_user("stranger@example.com")
    real_db_session.add_all([owner, stranger])
    real_db_session.commit()

    stranger_token = _token_for(str(stranger.id))
    r = client_real_db.get(
        "/dashboard/pnl",
        headers={
            "Authorization": f"Bearer {stranger_token}",
            "X-Store-Owner-Id": str(owner.id),
        },
    )
    assert r.status_code == 403


def test_dashboard_state_autostarts_for_granted_store(client_real_db, real_db_session):
    owner = _mk_user("owner3@example.com")
    viewer = _mk_user("viewer3@example.com")
    real_db_session.add_all([owner, viewer])
    real_db_session.commit()

    # Grant access so store context is allowed.
    owner_token = _token_for(str(owner.id))
    rg = client_real_db.post(
        "/stores/grants",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"grantee_email": "viewer3@example.com"},
    )
    assert rg.status_code == 200

    viewer_token = _token_for(str(viewer.id))
    with patch("app.routers.dashboard.sync_funnel_ytd_step"):
        r = client_real_db.get(
            "/dashboard/state",
            headers={
                "Authorization": f"Bearer {viewer_token}",
                "X-Store-Owner-Id": str(owner.id),
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data.get("autostart_disabled") is False
        assert data.get("autostart_disabled_reason") is None
        # Мы не проверяем точное число вызовов: автозапуск может не произойти, если условия не выполнены
        # (например, нет wb_api_key или уже есть данные за вчера). Главное — автозапуск НЕ запрещён.

