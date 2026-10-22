from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models.store_invite import StoreInvite
from app.models.user import User
from app.schemas.stores import (
    OutgoingGrantsResponse,
    StoreGrantRequest,
    StoreGrantResponse,
    StoreInviteAcceptRequest,
    StoreInviteAcceptResponse,
    StoreInviteCreateRequest,
    StoreInviteCreatedResponse,
    StoreInviteItem,
    StoreInviteLookupResponse,
    StoreManagerItem,
    StoreTeamResponse,
    StoresListResponse,
    StoreItem,
    OutgoingGrantItem,
)
from app.services.store_access_service import (
    StoreAccessDeniedError,
    StoreAccessInvalidInputError,
    StoreAccessNotFoundError,
    grant_store_access,
    list_accessible_stores,
    list_outgoing_grants,
    revoke_store_access,
    utc_now,
)
from app.services.store_invite_service import (
    StoreInviteExpiredError,
    accept_invite,
    create_invite,
    effective_status,
    get_invite_public_info,
    list_store_team,
    revoke_invite,
)


router = APIRouter(prefix="/stores", tags=["stores"])


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _invite_item(invite: StoreInvite, now: datetime) -> StoreInviteItem:
    return StoreInviteItem(
        id=str(invite.id),
        email=str(invite.email),
        status=effective_status(invite, now),
        expires_at=invite.expires_at.isoformat(),
        created_at=_iso(invite.created_at),
    )


@router.get("", response_model=StoresListResponse)
def get_accessible_stores(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items: list[StoreItem] = []
    for owner, access in list_accessible_stores(db, viewer=current_user):
        items.append(
            StoreItem(
                owner_user_id=str(owner.id),
                owner_email=str(owner.email),
                access=access,
            )
        )
    return StoresListResponse(stores=items)


@router.get("/grants", response_model=OutgoingGrantsResponse)
def get_outgoing_grants(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    grants = list_outgoing_grants(db, owner=current_user)
    out: list[OutgoingGrantItem] = []
    for g in grants:
        viewer = db.query(User).filter(User.id == g.viewer_user_id).first()
        viewer_email = str(viewer.email) if isinstance(viewer, User) else ""
        out.append(
            OutgoingGrantItem(
                viewer_email=viewer_email,
                viewer_user_id=str(g.viewer_user_id),
                status=str(g.status),
                revoked_at=g.revoked_at.isoformat() if g.revoked_at else None,
            )
        )
    return OutgoingGrantsResponse(grants=out)


@router.post("/grants", response_model=StoreGrantResponse)
def post_grant_store_access(
    body: StoreGrantRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        g = grant_store_access(db, owner=current_user, grantee_email=str(body.grantee_email))
    except StoreAccessInvalidInputError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except StoreAccessNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return StoreGrantResponse(
        store_owner_user_id=str(g.store_owner_user_id),
        viewer_user_id=str(g.viewer_user_id),
        status=str(g.status),
        revoked_at=g.revoked_at.isoformat() if g.revoked_at else None,
    )


@router.post("/grants/revoke", response_model=StoreGrantResponse)
def post_revoke_store_access(
    body: StoreGrantRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        g = revoke_store_access(db, owner=current_user, grantee_email=str(body.grantee_email))
    except StoreAccessInvalidInputError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except StoreAccessNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return StoreGrantResponse(
        store_owner_user_id=str(g.store_owner_user_id),
        viewer_user_id=str(g.viewer_user_id),
        status=str(g.status),
        revoked_at=g.revoked_at.isoformat() if g.revoked_at else None,
    )


@router.post("/invites", response_model=StoreInviteCreatedResponse)
def post_create_store_invite(
    body: StoreInviteCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        invite, raw_token = create_invite(db, owner=current_user, email=str(body.email))
    except StoreAccessInvalidInputError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except StoreAccessNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return StoreInviteCreatedResponse(
        id=str(invite.id),
        email=str(invite.email),
        status=str(invite.status),
        expires_at=invite.expires_at.isoformat(),
        invite_url_path=f"/invite/{raw_token}",
    )


@router.get("/team", response_model=StoreTeamResponse)
def get_store_team(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now = utc_now()
    managers, invites = list_store_team(db, owner=current_user)
    return StoreTeamResponse(
        managers=[
            StoreManagerItem(
                viewer_user_id=str(grant.viewer_user_id),
                email=str(user.email),
                granted_at=_iso(grant.created_at),
            )
            for grant, user in managers
        ],
        invites=[_invite_item(invite, now) for invite in invites],
    )


@router.get("/invites/lookup", response_model=StoreInviteLookupResponse)
def get_store_invite_lookup(
    token: str = Query(min_length=1, max_length=256),
    db: Session = Depends(get_db),
):
    """Публичная проверка приглашения по токену (без авторизации)."""
    try:
        info = get_invite_public_info(db, token, now=utc_now())
    except StoreAccessNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return StoreInviteLookupResponse(
        owner_email=info.owner_email,
        email=info.email,
        status=info.status,
        expires_at=info.expires_at.isoformat(),
    )


@router.post("/invites/accept", response_model=StoreInviteAcceptResponse)
def post_accept_store_invite(
    body: StoreInviteAcceptRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Намеренно get_current_user, а не get_store_context: принять приглашение можно,
    # даже если заголовок X-Store-Owner-Id указывает на магазин, которого ещё нет доступа.
    try:
        grant, owner = accept_invite(db, raw_token=body.token, current_user=current_user)
    except StoreInviteExpiredError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except StoreAccessDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except StoreAccessInvalidInputError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except StoreAccessNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return StoreInviteAcceptResponse(
        owner_user_id=str(grant.store_owner_user_id),
        owner_email=str(owner.email),
        status="accepted",
    )


@router.post("/invites/{invite_id}/revoke", response_model=StoreInviteItem)
def post_revoke_store_invite(
    invite_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        invite = revoke_invite(db, owner=current_user, invite_id=invite_id)
    except StoreAccessInvalidInputError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except StoreAccessNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _invite_item(invite, utc_now())
