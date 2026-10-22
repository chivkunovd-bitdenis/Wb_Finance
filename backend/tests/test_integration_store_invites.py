"""
Интеграционные тесты приглашений сотрудников (нужна PostgreSQL, см. conftest.real_db_session).

Каждый тест работает в транзакции и откатывается. Без DATABASE_URL или без доступной БД тесты
пропускаются фикстурой real_db_session.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db import get_db
from app.main import app
from app.models.store_access_audit_event import StoreAccessAuditEvent
from app.models.store_invite import StoreInvite
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


def _mk_user(email: str, *, wb_api_key: str | None = "wb-key") -> User:
    return User(
        id=str(uuid4()),
        email=email,
        password_hash="hash",
        wb_api_key=wb_api_key,
        is_active=True,
    )


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'sub': str(user.id)})}"}


def _raw_token_from_path(invite_url_path: str) -> str:
    prefix = "/invite/"
    assert invite_url_path.startswith(prefix)
    return invite_url_path[len(prefix):]


def _create_invite(client: TestClient, owner: User, email: str) -> dict:
    r = client.post("/stores/invites", headers=_auth(owner), json={"email": email})
    assert r.status_code == 200, r.text
    return r.json()


def _load_invite(real_db_session, invite_id: str) -> StoreInvite:
    invite = real_db_session.query(StoreInvite).filter(StoreInvite.id == invite_id).one()
    real_db_session.refresh(invite)
    return invite


def _expire_invite(real_db_session, invite_id: str) -> None:
    invite = real_db_session.query(StoreInvite).filter(StoreInvite.id == invite_id).one()
    invite.expires_at = datetime(2020, 1, 1, tzinfo=UTC)
    real_db_session.flush()


def test_create_invite_returns_link_and_persists_only_hash(client_real_db, real_db_session):
    owner = _mk_user("inv-owner-1@example.com")
    real_db_session.add(owner)
    real_db_session.commit()

    body = _create_invite(client_real_db, owner, "Staff1@Example.com")
    raw = _raw_token_from_path(body["invite_url_path"])

    assert body["status"] == "pending"
    assert body["email"] == "staff1@example.com"

    row = _load_invite(real_db_session, body["id"])
    assert row.token_hash == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert raw not in row.token_hash
    assert raw not in (row.email, row.status)
    assert row.expires_at - row.created_at == timedelta(days=14)

    # Сырой токен не возвращается повторно ни в списке команды, ни в публичном lookup.
    team = client_real_db.get("/stores/team", headers=_auth(owner))
    assert team.status_code == 200
    assert raw not in team.text


def test_lookup_is_public_and_returns_only_public_fields(client_real_db, real_db_session):
    owner = _mk_user("inv-owner-2@example.com")
    real_db_session.add(owner)
    real_db_session.commit()
    body = _create_invite(client_real_db, owner, "staff2@example.com")
    raw = _raw_token_from_path(body["invite_url_path"])

    r = client_real_db.get("/stores/invites/lookup", params={"token": raw})  # без Authorization
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data) == {"owner_email", "email", "status", "expires_at"}
    assert data["owner_email"] == "inv-owner-2@example.com"
    assert data["email"] == "staff2@example.com"
    assert data["status"] == "pending"

    missing = client_real_db.get("/stores/invites/lookup", params={"token": "no-such-token"})
    assert missing.status_code == 404


def test_duplicate_invite_replaces_previous_pending_invite(client_real_db, real_db_session):
    owner = _mk_user("inv-owner-3@example.com")
    real_db_session.add(owner)
    real_db_session.commit()

    first = _create_invite(client_real_db, owner, "dup@example.com")
    second = _create_invite(client_real_db, owner, "dup@example.com")
    assert first["id"] != second["id"]

    assert _load_invite(real_db_session, first["id"]).status == "revoked"
    assert _load_invite(real_db_session, second["id"]).status == "pending"

    team = client_real_db.get("/stores/team", headers=_auth(owner)).json()
    assert [i["id"] for i in team["invites"]] == [second["id"]]


def test_invite_for_existing_active_manager_is_rejected(client_real_db, real_db_session):
    owner = _mk_user("inv-owner-4@example.com")
    manager = _mk_user("active-manager@example.com")
    real_db_session.add_all([owner, manager])
    real_db_session.commit()

    grant = client_real_db.post(
        "/stores/grants",
        headers=_auth(owner),
        json={"grantee_email": "active-manager@example.com"},
    )
    assert grant.status_code == 200

    r = client_real_db.post(
        "/stores/invites",
        headers=_auth(owner),
        json={"email": "active-manager@example.com"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "Уже есть доступ"


def test_accept_with_matching_email_grants_store_access(client_real_db, real_db_session):
    owner = _mk_user("inv-owner-5@example.com")
    invitee = _mk_user("invitee-accept@example.com", wb_api_key=None)
    real_db_session.add_all([owner, invitee])
    real_db_session.commit()

    body = _create_invite(client_real_db, owner, "invitee-accept@example.com")
    raw = _raw_token_from_path(body["invite_url_path"])

    r = client_real_db.post("/stores/invites/accept", headers=_auth(invitee), json={"token": raw})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "owner_user_id": str(owner.id),
        "owner_email": "inv-owner-5@example.com",
        "status": "accepted",
    }

    stores = client_real_db.get("/stores", headers=_auth(invitee)).json()["stores"]
    assert any(s["owner_user_id"] == str(owner.id) and s["access"] == "granted" for s in stores)

    row = _load_invite(real_db_session, body["id"])
    assert row.status == "accepted"
    assert str(row.accepted_user_id) == str(invitee.id)
    assert row.accepted_at is not None

    audit_count = (
        real_db_session.query(StoreAccessAuditEvent)
        .filter(
            StoreAccessAuditEvent.store_owner_user_id == str(owner.id),
            StoreAccessAuditEvent.viewer_user_id == str(invitee.id),
            StoreAccessAuditEvent.action == "grant",
        )
        .count()
    )
    assert audit_count == 1

    again = client_real_db.post("/stores/invites/accept", headers=_auth(invitee), json={"token": raw})
    assert again.status_code == 400
    assert again.json()["detail"] == "Приглашение уже принято"


def test_accept_with_wrong_email_is_forbidden_and_invite_stays_pending(client_real_db, real_db_session):
    owner = _mk_user("inv-owner-6@example.com")
    stranger = _mk_user("stranger-6@example.com")
    real_db_session.add_all([owner, stranger])
    real_db_session.commit()

    body = _create_invite(client_real_db, owner, "intended-6@example.com")
    raw = _raw_token_from_path(body["invite_url_path"])

    r = client_real_db.post("/stores/invites/accept", headers=_auth(stranger), json={"token": raw})
    assert r.status_code == 403
    assert _load_invite(real_db_session, body["id"]).status == "pending"

    stores = client_real_db.get("/stores", headers=_auth(stranger)).json()["stores"]
    assert not any(s["access"] == "granted" for s in stores)


def test_accept_expired_invite_returns_410(client_real_db, real_db_session):
    owner = _mk_user("inv-owner-7@example.com")
    invitee = _mk_user("invitee-7@example.com", wb_api_key=None)
    real_db_session.add_all([owner, invitee])
    real_db_session.commit()

    body = _create_invite(client_real_db, owner, "invitee-7@example.com")
    raw = _raw_token_from_path(body["invite_url_path"])
    _expire_invite(real_db_session, body["id"])

    r = client_real_db.post("/stores/invites/accept", headers=_auth(invitee), json={"token": raw})
    assert r.status_code == 410
    assert r.json()["detail"] == "Срок приглашения истёк"
    assert _load_invite(real_db_session, body["id"]).status == "pending"


def test_accept_revoked_invite_returns_400(client_real_db, real_db_session):
    owner = _mk_user("inv-owner-8@example.com")
    invitee = _mk_user("invitee-8@example.com", wb_api_key=None)
    real_db_session.add_all([owner, invitee])
    real_db_session.commit()

    body = _create_invite(client_real_db, owner, "invitee-8@example.com")
    raw = _raw_token_from_path(body["invite_url_path"])

    rev = client_real_db.post(f"/stores/invites/{body['id']}/revoke", headers=_auth(owner))
    assert rev.status_code == 200
    assert rev.json()["status"] == "revoked"

    r = client_real_db.post("/stores/invites/accept", headers=_auth(invitee), json={"token": raw})
    assert r.status_code == 400
    assert r.json()["detail"] == "Приглашение отозвано"

    lookup = client_real_db.get("/stores/invites/lookup", params={"token": raw})
    assert lookup.json()["status"] == "revoked"


def test_revoke_invite_is_limited_to_its_owner(client_real_db, real_db_session):
    owner = _mk_user("inv-owner-9@example.com")
    stranger = _mk_user("stranger-9@example.com")
    real_db_session.add_all([owner, stranger])
    real_db_session.commit()

    body = _create_invite(client_real_db, owner, "invitee-9@example.com")
    r = client_real_db.post(f"/stores/invites/{body['id']}/revoke", headers=_auth(stranger))
    assert r.status_code == 404
    assert _load_invite(real_db_session, body["id"]).status == "pending"


def test_team_lists_managers_and_pending_invites_then_revoke_manager(client_real_db, real_db_session):
    owner = _mk_user("inv-owner-10@example.com")
    manager = _mk_user("manager-10@example.com")
    real_db_session.add_all([owner, manager])
    real_db_session.commit()

    client_real_db.post(
        "/stores/grants",
        headers=_auth(owner),
        json={"grantee_email": "manager-10@example.com"},
    )
    _create_invite(client_real_db, owner, "pending-10@example.com")

    team = client_real_db.get("/stores/team", headers=_auth(owner)).json()
    assert [m["email"] for m in team["managers"]] == ["manager-10@example.com"]
    assert [(i["email"], i["status"]) for i in team["invites"]] == [("pending-10@example.com", "pending")]

    # Отключение сотрудника использует существующий endpoint доступа.
    off = client_real_db.post(
        "/stores/grants/revoke",
        headers=_auth(owner),
        json={"grantee_email": "manager-10@example.com"},
    )
    assert off.status_code == 200
    team_after = client_real_db.get("/stores/team", headers=_auth(owner)).json()
    assert team_after["managers"] == []


def test_expired_pending_invite_is_listed_as_expired_and_can_be_revoked(client_real_db, real_db_session):
    owner = _mk_user("inv-owner-11@example.com")
    real_db_session.add(owner)
    real_db_session.commit()

    body = _create_invite(client_real_db, owner, "late-11@example.com")
    _expire_invite(real_db_session, body["id"])

    team = client_real_db.get("/stores/team", headers=_auth(owner)).json()
    assert [(i["id"], i["status"]) for i in team["invites"]] == [(body["id"], "expired")]

    rev = client_real_db.post(f"/stores/invites/{body['id']}/revoke", headers=_auth(owner))
    assert rev.status_code == 200
    assert rev.json()["status"] == "revoked"


def test_register_with_invite_grants_access_without_wb_key(client_real_db, real_db_session):
    owner = _mk_user("inv-owner-12@example.com", wb_api_key="owner-wb-key")
    real_db_session.add(owner)
    real_db_session.commit()

    body = _create_invite(client_real_db, owner, "newmanager-12@example.com")
    raw = _raw_token_from_path(body["invite_url_path"])

    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.hash.return_value = "$2b$12$fake"
        r = client_real_db.post(
            "/auth/register",
            json={
                "email": "NewManager-12@Example.com",
                "password": "secret",
                "invite_token": raw,
            },
        )
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "newmanager-12@example.com"
    assert r.json()["wb_api_key"] is None

    new_user = real_db_session.query(User).filter(User.email == "newmanager-12@example.com").one()
    stores = client_real_db.get("/stores", headers=_auth(new_user)).json()["stores"]
    assert any(s["owner_user_id"] == str(owner.id) and s["access"] == "granted" for s in stores)

    row = _load_invite(real_db_session, body["id"])
    assert row.status == "accepted"
    assert str(row.accepted_user_id) == str(new_user.id)


def test_register_with_invite_for_other_email_is_forbidden_and_creates_no_user(client_real_db, real_db_session):
    owner = _mk_user("inv-owner-13@example.com")
    real_db_session.add(owner)
    real_db_session.commit()

    body = _create_invite(client_real_db, owner, "intended-13@example.com")
    raw = _raw_token_from_path(body["invite_url_path"])

    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.hash.return_value = "$2b$12$fake"
        r = client_real_db.post(
            "/auth/register",
            json={"email": "intruder-13@example.com", "password": "secret", "invite_token": raw},
        )
    assert r.status_code == 403
    assert real_db_session.query(User).filter(User.email == "intruder-13@example.com").count() == 0
    assert _load_invite(real_db_session, body["id"]).status == "pending"
