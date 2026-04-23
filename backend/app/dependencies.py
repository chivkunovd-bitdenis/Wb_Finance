from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.user import User
from app.core.security import decode_access_token
from app.services.billing_service import require_access, start_trial_if_needed
from app.services.store_access_service import (
    StoreAccessDeniedError,
    StoreAccessNotFoundError,
    StoreAccessInvalidInputError,
    StoreContext,
    get_store_context_from_header,
)

security = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        request.state.auth_error = "missing_bearer"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        request.state.auth_error = "invalid_or_expired_token"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или истёкший токен",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = payload["sub"]
    request.state.user_id = str(user_id)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        request.state.auth_error = "user_not_found"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь не найден",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        request.state.auth_error = "user_inactive"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Аккаунт деактивирован",
        )
    # Для существующих аккаунтов (созданных до billing) запускаем trial лениво,
    # когда у пользователя уже есть WB API key.
    sub = start_trial_if_needed(db, user)
    if sub in db.new or db.is_modified(sub):
        db.commit()
    path = request.url.path or ""
    allow_without_access = (
        path.startswith("/auth")
        or path.startswith("/billing")
        or path.startswith("/health")
    )
    if not allow_without_access:
        try:
            require_access(db, user)
        except PermissionError as exc:
            request.state.auth_error = "billing_access_denied"
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=str(exc),
            ) from exc
    return user


def get_store_context(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StoreContext:
    """
    Resolve effective store context for the request.

    Viewer is the authenticated user (JWT). Store owner can be switched via header:
    `X-Store-Owner-Id: <uuid>`.
    """
    raw_owner_id = (request.headers.get("X-Store-Owner-Id") or "").strip()
    try:
        return get_store_context_from_header(db, viewer=current_user, store_owner_id=raw_owner_id or None)
    except StoreAccessInvalidInputError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except StoreAccessNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except StoreAccessDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
