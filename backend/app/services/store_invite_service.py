from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.store_access_grant import StoreAccessGrant
from app.models.store_invite import StoreInvite
from app.models.user import User
from app.services.store_access_service import (
    StoreAccessDeniedError,
    StoreAccessError,
    StoreAccessInvalidInputError,
    StoreAccessNotFoundError,
    activate_grant_no_commit,
    utc_now,
)

INVITE_TTL = timedelta(days=14)

INVITE_STATUS_PENDING = "pending"
INVITE_STATUS_ACCEPTED = "accepted"
INVITE_STATUS_REVOKED = "revoked"
# Вычисляемый статус: pending, но срок истёк. В БД не хранится.
INVITE_STATUS_EXPIRED = "expired"


class StoreInviteExpiredError(StoreAccessError):
    """Срок приглашения истёк (HTTP 410)."""


@dataclass(frozen=True, slots=True)
class InvitePublicInfo:
    owner_email: str
    email: str
    status: str
    expires_at: datetime


def normalize_email(raw: str | None) -> str:
    return (raw or "").strip().lower()


def hash_invite_token(raw_token: str) -> str:
    """sha256 от токена. Сырой токен в БД не пишем — только этот хэш."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def effective_status(invite: StoreInvite, now: datetime) -> str:
    if invite.status == INVITE_STATUS_PENDING and invite.expires_at <= now:
        return INVITE_STATUS_EXPIRED
    return str(invite.status)


def ensure_invite_acceptable(invite: StoreInvite, *, user_email: str, now: datetime) -> None:
    """
    Проверка, что приглашение можно принять этим email прямо сейчас.

    Порядок: чужой email (403) -> уже принято / отозвано (400) -> просрочено (410).
    """
    if normalize_email(user_email) != normalize_email(invite.email):
        raise StoreAccessDeniedError("Приглашение выписано на другой email")
    if invite.status == INVITE_STATUS_ACCEPTED:
        raise StoreAccessInvalidInputError("Приглашение уже принято")
    if invite.status != INVITE_STATUS_PENDING:
        raise StoreAccessInvalidInputError("Приглашение отозвано")
    if invite.expires_at <= now:
        raise StoreInviteExpiredError("Срок приглашения истёк")


def find_invite_by_token(db: Session, raw_token: str | None, *, for_update: bool = False) -> StoreInvite | None:
    token = (raw_token or "").strip()
    if not token:
        return None
    query = db.query(StoreInvite).filter(StoreInvite.token_hash == hash_invite_token(token))
    if for_update:
        query = query.with_for_update()
    invite = query.first()
    return invite if isinstance(invite, StoreInvite) else None


def _has_active_grant(db: Session, *, owner_user_id: str, viewer_user_id: str) -> bool:
    row = (
        db.query(StoreAccessGrant.id)
        .filter(
            StoreAccessGrant.store_owner_user_id == owner_user_id,
            StoreAccessGrant.viewer_user_id == viewer_user_id,
            StoreAccessGrant.status == "active",
            StoreAccessGrant.revoked_at.is_(None),
        )
        .first()
    )
    return row is not None


def get_invite_public_info(db: Session, raw_token: str | None, *, now: datetime) -> InvitePublicInfo:
    """Публичные данные приглашения (без авторизации): кабинет, email приглашённого, статус."""
    invite = find_invite_by_token(db, raw_token)
    if invite is None:
        raise StoreAccessNotFoundError("Приглашение не найдено")
    owner = db.query(User).filter(User.id == invite.store_owner_user_id).first()
    if not isinstance(owner, User):
        raise StoreAccessNotFoundError("Приглашение не найдено")
    return InvitePublicInfo(
        owner_email=str(owner.email),
        email=str(invite.email),
        status=effective_status(invite, now),
        expires_at=invite.expires_at,
    )


def create_invite(db: Session, *, owner: User, email: str) -> tuple[StoreInvite, str]:
    """
    Создать приглашение. Возвращает (invite, raw_token); raw_token виден только здесь.

    Ранее выданные pending-приглашения на тот же email (в том числе просроченные)
    переводятся в revoked, чтобы на один email было одно актуальное приглашение.
    """
    normalized = normalize_email(email)
    if not normalized:
        raise StoreAccessInvalidInputError("Email is required")
    if normalized == normalize_email(owner.email):
        raise StoreAccessInvalidInputError("Нельзя пригласить самого себя")

    invitee = db.query(User).filter(User.email == normalized).first()
    if isinstance(invitee, User) and _has_active_grant(
        db, owner_user_id=str(owner.id), viewer_user_id=str(invitee.id)
    ):
        raise StoreAccessInvalidInputError("Уже есть доступ")

    previous = (
        db.query(StoreInvite)
        .filter(
            StoreInvite.store_owner_user_id == str(owner.id),
            StoreInvite.email == normalized,
            StoreInvite.status == INVITE_STATUS_PENDING,
        )
        .all()
    )
    for old in previous:
        old.status = INVITE_STATUS_REVOKED
        db.add(old)

    now = utc_now()
    raw_token = secrets.token_urlsafe(32)
    invite = StoreInvite(
        store_owner_user_id=str(owner.id),
        email=normalized,
        token_hash=hash_invite_token(raw_token),
        status=INVITE_STATUS_PENDING,
        created_at=now,
        expires_at=now + INVITE_TTL,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite, raw_token


def list_store_team(
    db: Session, *, owner: User
) -> tuple[list[tuple[StoreAccessGrant, User]], list[StoreInvite]]:
    """Активные сотрудники (доступ + их User) и pending-приглашения владельца."""
    managers = (
        db.query(StoreAccessGrant, User)
        .join(User, User.id == StoreAccessGrant.viewer_user_id)
        .filter(
            StoreAccessGrant.store_owner_user_id == str(owner.id),
            StoreAccessGrant.status == "active",
            StoreAccessGrant.revoked_at.is_(None),
        )
        .order_by(User.email.asc())
        .all()
    )
    invites = (
        db.query(StoreInvite)
        .filter(
            StoreInvite.store_owner_user_id == str(owner.id),
            StoreInvite.status == INVITE_STATUS_PENDING,
        )
        .order_by(StoreInvite.created_at.desc())
        .all()
    )
    return list(managers), list(invites)


def revoke_invite(db: Session, *, owner: User, invite_id: str) -> StoreInvite:
    try:
        parsed_id = str(UUID(invite_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise StoreAccessNotFoundError("Приглашение не найдено") from exc

    invite = (
        db.query(StoreInvite)
        .filter(
            StoreInvite.id == parsed_id,
            StoreInvite.store_owner_user_id == str(owner.id),
        )
        .first()
    )
    if not isinstance(invite, StoreInvite):
        raise StoreAccessNotFoundError("Приглашение не найдено")
    if invite.status != INVITE_STATUS_PENDING:
        raise StoreAccessInvalidInputError("Приглашение уже неактивно")

    invite.status = INVITE_STATUS_REVOKED
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite


def consume_invite_no_commit(
    db: Session,
    *,
    invite: StoreInvite,
    viewer: User,
    now: datetime,
) -> StoreAccessGrant:
    """
    Отметить приглашение принятым и выдать доступ viewer к магазину владельца.

    Проверки статуса/email/срока делает ensure_invite_acceptable до вызова. Без commit.
    """
    grant = activate_grant_no_commit(
        db,
        owner_user_id=str(invite.store_owner_user_id),
        viewer_user_id=str(viewer.id),
        actor_user_id=str(invite.store_owner_user_id),
        now=now,
    )
    invite.status = INVITE_STATUS_ACCEPTED
    invite.accepted_at = now
    invite.accepted_user_id = str(viewer.id)
    db.add(invite)
    return grant


def accept_invite(db: Session, *, raw_token: str | None, current_user: User) -> tuple[StoreAccessGrant, User]:
    """Принять приглашение авторизованным пользователем с тем же email. Возвращает (grant, owner)."""
    invite = find_invite_by_token(db, raw_token, for_update=True)
    if invite is None:
        raise StoreAccessNotFoundError("Приглашение не найдено")

    now = utc_now()
    ensure_invite_acceptable(invite, user_email=str(current_user.email), now=now)

    owner = db.query(User).filter(User.id == invite.store_owner_user_id).first()
    if not isinstance(owner, User):
        raise StoreAccessNotFoundError("Приглашение не найдено")

    grant = consume_invite_no_commit(db, invite=invite, viewer=current_user, now=now)
    db.commit()
    db.refresh(grant)
    return grant, owner


def validate_invite_for_registration(
    db: Session,
    raw_token: str | None,
    *,
    email: str,
    now: datetime,
) -> StoreInvite:
    """
    Проверить приглашение перед созданием пользователя по нему.

    Строка приглашения блокируется (FOR UPDATE) до конца транзакции регистрации,
    чтобы одно приглашение не приняли два раза параллельно.
    """
    invite = find_invite_by_token(db, raw_token, for_update=True)
    if invite is None:
        raise StoreAccessNotFoundError("Приглашение не найдено")
    ensure_invite_acceptable(invite, user_email=email, now=now)
    return invite
