"""
Unit-тесты приглашений сотрудников (store invites) без реальной БД.

Сессия подменена фейком (MagicMock с ответами по модели), зависимости авторизации
подменены через dependency_overrides. Полные сценарии с PostgreSQL — в
test_integration_store_invites.py.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.dependencies import get_current_user
from app.main import app
from app.models.store_access_grant import StoreAccessGrant
from app.models.store_invite import StoreInvite
from app.models.user import User
from app.services.store_access_service import (
    StoreAccessDeniedError,
    StoreAccessInvalidInputError,
)
from app.services.store_invite_service import (
    StoreInviteExpiredError,
    create_invite,
    effective_status,
    ensure_invite_acceptable,
    hash_invite_token,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def _future() -> datetime:
    return datetime.now(UTC) + timedelta(days=14)


def _past() -> datetime:
    return datetime(2020, 1, 1, tzinfo=UTC)


def _owner() -> User:
    return User(id="owner-1", email="owner@example.com", password_hash="x", is_active=True)


def _invitee() -> User:
    return User(id="invitee-1", email="invitee@example.com", password_hash="x", is_active=True)


def _invite(**overrides) -> StoreInvite:
    fields = {
        "id": "inv-1",
        "store_owner_user_id": "owner-1",
        "email": "invitee@example.com",
        "token_hash": "hash-1",
        "status": "pending",
        "created_at": NOW,
        "expires_at": NOW + timedelta(days=14),
    }
    fields.update(overrides)
    return StoreInvite(**fields)


def _fake_session(first_by_model: dict | None = None, all_by_model: dict | None = None) -> MagicMock:
    """
    Фейк сессии: query(Model).filter(...).first() / .all() отдают значения по модели.
    Для query(Model.column) ключом служит класс модели (атрибут class_).
    """
    first_by_model = first_by_model or {}
    all_by_model = all_by_model or {}
    session = MagicMock()

    def _query(target, *args, **kwargs):
        key = getattr(target, "class_", target)
        q = MagicMock(name="query")
        for chain in ("filter", "join", "order_by", "with_for_update"):
            getattr(q, chain).return_value = q
        q.first.return_value = first_by_model.get(key)
        q.all.return_value = list(all_by_model.get(key, []))
        return q

    session.query.side_effect = _query
    return session


@pytest.fixture(autouse=True)
def _reset_dependency_overrides():
    yield
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


def _client(session: MagicMock, *, current_user: User | None = None) -> TestClient:
    app.dependency_overrides[get_db] = lambda: session
    if current_user is not None:
        app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


# ── Чистые правила приглашения ─────────────────────────────────────────────


def test_hash_invite_token_is_sha256_and_never_the_raw_value():
    raw = "raw-token-value"
    digest = hash_invite_token(raw)
    assert digest == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert digest != raw
    assert len(digest) == 64


def test_effective_status_marks_pending_past_deadline_as_expired():
    assert effective_status(_invite(expires_at=NOW - timedelta(seconds=1)), NOW) == "expired"
    assert effective_status(_invite(expires_at=NOW + timedelta(days=1)), NOW) == "pending"
    # Принятое приглашение не превращается в expired, даже если срок прошёл.
    assert effective_status(_invite(status="accepted", expires_at=NOW - timedelta(days=1)), NOW) == "accepted"


def test_ensure_invite_acceptable_matches_email_case_insensitively():
    ensure_invite_acceptable(_invite(), user_email="  Invitee@Example.COM ", now=NOW)


def test_ensure_invite_acceptable_wrong_email_is_denied():
    with pytest.raises(StoreAccessDeniedError):
        ensure_invite_acceptable(_invite(), user_email="someone@example.com", now=NOW)


def test_ensure_invite_acceptable_revoked_is_rejected():
    with pytest.raises(StoreAccessInvalidInputError, match="отозвано"):
        ensure_invite_acceptable(_invite(status="revoked"), user_email="invitee@example.com", now=NOW)


def test_ensure_invite_acceptable_already_accepted_is_rejected():
    with pytest.raises(StoreAccessInvalidInputError, match="уже принято"):
        ensure_invite_acceptable(_invite(status="accepted"), user_email="invitee@example.com", now=NOW)


def test_ensure_invite_acceptable_expired_raises_expired_error():
    with pytest.raises(StoreInviteExpiredError):
        ensure_invite_acceptable(
            _invite(expires_at=NOW - timedelta(minutes=1)),
            user_email="invitee@example.com",
            now=NOW,
        )


# ── Сервис создания приглашения (фейковая сессия) ──────────────────────────


def test_create_invite_rejects_own_email():
    with pytest.raises(StoreAccessInvalidInputError, match="самого себя"):
        create_invite(_fake_session(), owner=_owner(), email="OWNER@example.com")


def test_create_invite_rejects_email_that_already_has_active_grant():
    active_grant = StoreAccessGrant(
        store_owner_user_id="owner-1",
        viewer_user_id="invitee-1",
        status="active",
        revoked_at=None,
    )
    session = _fake_session(first_by_model={User: _invitee(), StoreAccessGrant: active_grant})
    with pytest.raises(StoreAccessInvalidInputError, match="Уже есть доступ"):
        create_invite(session, owner=_owner(), email="invitee@example.com")


def test_create_invite_returns_raw_token_and_stores_only_its_hash():
    session = _fake_session(first_by_model={User: None})
    invite, raw_token = create_invite(session, owner=_owner(), email="  Invitee@Example.com ")

    assert len(raw_token) >= 40  # token_urlsafe(32) даёт 43 символа
    assert invite.token_hash == hash_invite_token(raw_token)
    assert raw_token not in invite.token_hash
    assert invite.email == "invitee@example.com"
    assert invite.status == "pending"
    assert invite.expires_at - invite.created_at == timedelta(days=14)
    session.commit.assert_called()


def test_create_invite_revokes_previous_pending_invite_for_same_email():
    previous = _invite(id="old-inv", status="pending")
    session = _fake_session(first_by_model={User: None}, all_by_model={StoreInvite: [previous]})

    invite, _raw = create_invite(session, owner=_owner(), email="invitee@example.com")

    assert previous.status == "revoked"
    assert invite.status == "pending"
    assert invite is not previous


# ── HTTP-контракт (зависимости подменены) ──────────────────────────────────


def test_post_invites_rejects_own_email_over_http():
    client = _client(_fake_session(), current_user=_owner())
    r = client.post("/stores/invites", json={"email": "OWNER@example.com"})
    assert r.status_code == 400
    assert "самого себя" in r.json()["detail"]


def test_post_invites_rejects_malformed_email_with_422():
    client = _client(_fake_session(), current_user=_owner())
    r = client.post("/stores/invites", json={"email": "not-an-email"})
    assert r.status_code == 422


def test_lookup_unknown_token_returns_404():
    client = _client(_fake_session())
    r = client.get("/stores/invites/lookup", params={"token": "does-not-exist"})
    assert r.status_code == 404


def test_lookup_expired_invite_reports_expired_status():
    owner = _owner()
    invite = _invite(expires_at=_past())
    session = _fake_session(first_by_model={StoreInvite: invite, User: owner})
    client = _client(session)
    r = client.get("/stores/invites/lookup", params={"token": "any-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "expired"
    assert body["owner_email"] == "owner@example.com"
    assert body["email"] == "invitee@example.com"
    # Публичный ответ не раскрывает внутренние идентификаторы.
    assert "id" not in body
    assert "store_owner_user_id" not in body


def test_accept_requires_authentication():
    client = _client(_fake_session())
    r = client.post("/stores/invites/accept", json={"token": "abc"})
    assert r.status_code == 401


def test_team_requires_authentication():
    client = _client(_fake_session())
    r = client.get("/stores/team")
    assert r.status_code == 401


def test_revoke_malformed_invite_id_returns_404_without_db_lookup():
    session = _fake_session()
    client = _client(session, current_user=_owner())
    r = client.post("/stores/invites/not-a-uuid/revoke")
    assert r.status_code == 404
    session.query.assert_not_called()


def test_register_with_unknown_invite_token_returns_404_and_creates_no_user():
    session = _fake_session()
    client = _client(session)
    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.hash.return_value = "$2b$12$fake"
        r = client.post(
            "/auth/register",
            json={"email": "new@example.com", "password": "secret", "invite_token": "bogus"},
        )
    assert r.status_code == 404
    assert r.json()["detail"] == "Приглашение не найдено"
    added_users = [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], User)]
    assert added_users == []
    session.commit.assert_not_called()


def test_register_with_invite_for_other_email_is_forbidden_and_creates_no_user():
    invite = _invite(email="invitee@example.com", expires_at=_future())
    session = _fake_session(first_by_model={StoreInvite: invite, User: None})
    client = _client(session)
    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.hash.return_value = "$2b$12$fake"
        r = client.post(
            "/auth/register",
            json={"email": "other@example.com", "password": "secret", "invite_token": "tok"},
        )
    assert r.status_code == 403
    assert r.json()["detail"] == "Приглашение выписано на другой email"
    added_users = [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], User)]
    assert added_users == []
    assert invite.status == "pending"


def test_register_with_expired_invite_returns_410_and_creates_no_user():
    invite = _invite(email="invitee@example.com", expires_at=_past())
    session = _fake_session(first_by_model={StoreInvite: invite, User: None})
    client = _client(session)
    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.hash.return_value = "$2b$12$fake"
        r = client.post(
            "/auth/register",
            json={"email": "invitee@example.com", "password": "secret", "invite_token": "tok"},
        )
    assert r.status_code == 410
    assert r.json()["detail"] == "Срок приглашения истёк"
    added_users = [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], User)]
    assert added_users == []
