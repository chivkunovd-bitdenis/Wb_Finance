from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import settings


def verify_internal_bearer(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Доступ к /internal/*: заголовок `Authorization: Bearer <WIP_INTERNAL_HMAC_SECRET>`."""
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization scheme",
        )
    if not secrets.compare_digest(token, settings.internal_hmac_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )


InternalAuth = Annotated[None, Depends(verify_internal_bearer)]
