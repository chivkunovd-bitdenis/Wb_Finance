from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.stores import (
    OutgoingGrantsResponse,
    StoreGrantRequest,
    StoreGrantResponse,
    StoresListResponse,
    StoreItem,
    OutgoingGrantItem,
)
from app.services.store_access_service import (
    StoreAccessInvalidInputError,
    StoreAccessNotFoundError,
    grant_store_access,
    list_accessible_stores,
    list_outgoing_grants,
    revoke_store_access,
)


router = APIRouter(prefix="/stores", tags=["stores"])


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

