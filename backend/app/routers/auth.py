from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.user import User
from app.schemas.auth import RegisterRequest, LoginRequest, TokenResponse, UserResponse, UpdateWbApiKeyRequest
from app.core.security import hash_password, verify_password, create_access_token
from app.dependencies import get_current_user
from app.services.billing_service import redeem_promo_code, start_trial_if_needed

router = APIRouter(prefix="/auth", tags=["auth"])

# Обрезка пароля по байтам здесь — чтобы сработало даже при старом образе/security.py
def _password_72(s: str) -> str:
    if not s:
        return ""
    raw = (s if isinstance(s, str) else str(s)).encode("utf-8")
    if len(raw) <= 72:
        return s
    return raw[:72].decode("utf-8", errors="ignore")


def _user_id(u):
    return str(u.id) if u else None


@router.post("/register", response_model=UserResponse)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    try:
        existing = db.query(User).filter(User.email == body.email.lower()).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Пользователь с таким email уже существует",
            )
        promo = body.promo_code.upper().strip() if body.promo_code else None

        pwd = _password_72(body.password)
        user = User(
            email=body.email.lower().strip(),
            password_hash=hash_password(pwd),
            wb_api_key=body.wb_api_key.strip() if body.wb_api_key else None,
            is_active=True,
        )
        db.add(user)
        db.flush()  # чтобы получить user.id до commit

        if promo:
            try:
                found = redeem_promo_code(db, promo, str(user.id))
                if not found:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Промокод не найден",
                    )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
        elif user.wb_api_key:
            start_trial_if_needed(db, user)

        db.commit()
        db.refresh(user)
        return UserResponse(
            id=_user_id(user),
            email=user.email,
            wb_api_key=user.wb_api_key,
            is_active=user.is_active,
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка регистрации: {str(e)}",
        )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower().strip()).first()
    pwd = _password_72(body.password)
    if not user or not verify_password(pwd, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Аккаунт деактивирован",
        )
    token = create_access_token(data={"sub": _user_id(user)})
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return UserResponse(
        id=_user_id(current_user),
        email=current_user.email,
        wb_api_key=current_user.wb_api_key,
        is_active=current_user.is_active,
    )


@router.put("/wb-key", response_model=UserResponse)
def update_wb_key(
    body: UpdateWbApiKeyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.wb_api_key = body.wb_api_key.strip()
    start_trial_if_needed(db, current_user)
    db.commit()
    db.refresh(current_user)
    return UserResponse(
        id=_user_id(current_user),
        email=current_user.email,
        wb_api_key=current_user.wb_api_key,
        is_active=current_user.is_active,
    )
