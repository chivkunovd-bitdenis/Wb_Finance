from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.store_access_audit_event import StoreAccessAuditEvent
from app.models.store_access_grant import StoreAccessGrant
from app.models.user import User


class StoreAccessError(Exception):
    """Domain-level store access error."""


class StoreAccessNotFoundError(StoreAccessError):
    pass


class StoreAccessDeniedError(StoreAccessError):
    pass


class StoreAccessInvalidInputError(StoreAccessError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class StoreContext:
    viewer: User
    store_owner: User

    @property
    def is_owner(self) -> bool:
        return str(self.viewer.id) == str(self.store_owner.id)


def parse_store_owner_id(raw: str) -> str:
    """
    Validate UUID string for store owner id (User.id).
    Returns normalized string UUID.
    """
    try:
        return str(UUID(raw))
    except ValueError as exc:
        raise StoreAccessInvalidInputError("Invalid store owner id") from exc


def get_store_context_from_header(
    db: Session,
    *,
    viewer: User,
    store_owner_id: str | None,
) -> StoreContext:
    if not store_owner_id:
        return StoreContext(viewer=viewer, store_owner=viewer)

    owner_id = parse_store_owner_id(store_owner_id)
    if owner_id == str(viewer.id):
        return StoreContext(viewer=viewer, store_owner=viewer)

    owner = db.query(User).filter(User.id == owner_id).first()
    if not isinstance(owner, User):
        raise StoreAccessNotFoundError("Store owner not found")

    allowed = (
        db.query(StoreAccessGrant.id)
        .filter(
            StoreAccessGrant.store_owner_user_id == owner_id,
            StoreAccessGrant.viewer_user_id == str(viewer.id),
            StoreAccessGrant.status == "active",
            StoreAccessGrant.revoked_at.is_(None),
        )
        .first()
        is not None
    )
    if not allowed:
        raise StoreAccessDeniedError("Access to this store is not granted")

    return StoreContext(viewer=viewer, store_owner=owner)


def list_accessible_stores(db: Session, *, viewer: User) -> list[tuple[User, str]]:
    """
    Return list of (store_owner_user, access_type) for viewer.

    access_type is 'owner' or 'granted'.
    """
    owners: list[tuple[User, str]] = [(viewer, "owner")]

    rows = (
        db.query(User)
        .join(StoreAccessGrant, StoreAccessGrant.store_owner_user_id == User.id)
        .filter(
            StoreAccessGrant.viewer_user_id == str(viewer.id),
            StoreAccessGrant.status == "active",
            StoreAccessGrant.revoked_at.is_(None),
        )
        .order_by(User.email.asc())
        .all()
    )
    for u in rows:
        if isinstance(u, User) and str(u.id) != str(viewer.id):
            owners.append((u, "granted"))
    return owners


def list_outgoing_grants(db: Session, *, owner: User) -> list[StoreAccessGrant]:
    return (
        db.query(StoreAccessGrant)
        .filter(StoreAccessGrant.store_owner_user_id == str(owner.id))
        .order_by(StoreAccessGrant.created_at.desc())
        .all()
    )


def grant_store_access(db: Session, *, owner: User, grantee_email: str) -> StoreAccessGrant:
    email = (grantee_email or "").strip().lower()
    if not email:
        raise StoreAccessInvalidInputError("Email is required")
    if email == str(owner.email).strip().lower():
        raise StoreAccessInvalidInputError("Cannot grant access to yourself")

    viewer = db.query(User).filter(User.email == email).first()
    if not isinstance(viewer, User):
        raise StoreAccessNotFoundError("User not found")

    grant = (
        db.query(StoreAccessGrant)
        .filter(
            StoreAccessGrant.store_owner_user_id == str(owner.id),
            StoreAccessGrant.viewer_user_id == str(viewer.id),
        )
        .first()
    )
    now = utc_now()
    if not isinstance(grant, StoreAccessGrant):
        grant = StoreAccessGrant(
            store_owner_user_id=str(owner.id),
            viewer_user_id=str(viewer.id),
            status="active",
            revoked_at=None,
        )
        db.add(grant)
    else:
        grant.status = "active"
        grant.revoked_at = None

    db.add(
        StoreAccessAuditEvent(
            store_owner_user_id=str(owner.id),
            viewer_user_id=str(viewer.id),
            actor_user_id=str(owner.id),
            action="grant",
            created_at=now,
        )
    )
    db.commit()
    db.refresh(grant)
    return grant


def revoke_store_access(db: Session, *, owner: User, grantee_email: str) -> StoreAccessGrant:
    email = (grantee_email or "").strip().lower()
    if not email:
        raise StoreAccessInvalidInputError("Email is required")

    viewer = db.query(User).filter(User.email == email).first()
    if not isinstance(viewer, User):
        raise StoreAccessNotFoundError("User not found")

    grant = (
        db.query(StoreAccessGrant)
        .filter(
            StoreAccessGrant.store_owner_user_id == str(owner.id),
            StoreAccessGrant.viewer_user_id == str(viewer.id),
        )
        .first()
    )
    if not isinstance(grant, StoreAccessGrant) or grant.revoked_at is not None or grant.status != "active":
        raise StoreAccessNotFoundError("Active grant not found")

    now = utc_now()
    grant.status = "revoked"
    grant.revoked_at = now
    db.add(grant)
    db.add(
        StoreAccessAuditEvent(
            store_owner_user_id=str(owner.id),
            viewer_user_id=str(viewer.id),
            actor_user_id=str(owner.id),
            action="revoke",
            created_at=now,
        )
    )
    db.commit()
    db.refresh(grant)
    return grant

