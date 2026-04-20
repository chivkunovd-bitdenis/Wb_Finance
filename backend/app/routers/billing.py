import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models.reminder_log import ReminderLog
from app.models.user import User
from app.schemas.billing import (
    BillingStatusResponse,
    CheckoutRequest,
    CheckoutResponse,
    WebhookResponse,
    YookassaSyncReturnResponse,
)
from app.models.promo_code import PromoCode
from app.services.billing_service import (
    ADMIN_SECRET,
    YooKassaRequestError,
    create_checkout,
    get_billing_status,
    grant_lifetime,
    process_yookassa_webhook,
    sync_latest_yookassa_payment,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/status", response_model=BillingStatusResponse)
def billing_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return BillingStatusResponse(**get_billing_status(db, current_user))


@router.post("/checkout", response_model=CheckoutResponse)
def checkout(
    body: CheckoutRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        result = create_checkout(db, current_user, Decimal(str(body.amount)), body.return_url)
    except YooKassaRequestError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc
    return CheckoutResponse(**result)


@router.post("/yookassa/sync-return", response_model=YookassaSyncReturnResponse)
def yookassa_sync_return(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Синхронизировать последний ожидающий платёж с API ЮKassa (после redirect)."""
    try:
        result = sync_latest_yookassa_payment(db, current_user)
    except YooKassaRequestError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message) from exc
    return YookassaSyncReturnResponse(**result)


@router.post("/webhook/yookassa", response_model=WebhookResponse, include_in_schema=False)
async def yookassa_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_webhook_secret: str | None = Header(default=None),
):
    payload = await request.json()
    obj = payload.get("object") if isinstance(payload, dict) else None
    event = str(payload.get("event") or "") if isinstance(payload, dict) else ""
    payment_id = str((obj or {}).get("id") or "") if isinstance(obj, dict) else ""
    md = obj.get("metadata") if isinstance(obj, dict) else None
    has_user = bool(isinstance(md, dict) and md.get("user_id"))
    logger.info(
        "yookassa webhook: event=%s payment_id=%s has_metadata_user=%s",
        event or "(empty)",
        payment_id or "(none)",
        has_user,
    )
    try:
        process_yookassa_webhook(db, payload, x_webhook_secret)
    except ValueError as exc:
        logger.warning("yookassa webhook rejected: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    logger.info("yookassa webhook: processed ok event=%s payment_id=%s", event or "(empty)", payment_id or "(none)")
    return WebhookResponse(ok=True)


@router.get("/reminders")
def reminders(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ReminderLog)
        .filter(ReminderLog.user_id == current_user.id)
        .order_by(ReminderLog.created_at.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "type": r.reminder_type,
            "channel": r.channel,
            "status": r.status,
            "due_at": r.due_at.isoformat() if r.due_at else None,
            "sent_at": r.sent_at.isoformat() if r.sent_at else None,
        }
        for r in rows
    ]


class GrantLifetimeRequest(BaseModel):
    email: EmailStr


@router.post("/admin/grant-lifetime", include_in_schema=False)
def admin_grant_lifetime(
    body: GrantLifetimeRequest,
    db: Session = Depends(get_db),
    x_admin_secret: str | None = Header(default=None),
):
    """Выдать пожизненный доступ пользователю по email. Защищено ADMIN_SECRET."""
    if not ADMIN_SECRET or x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    email = str(body.email).lower().strip()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    grant_lifetime(db, str(user.id))
    db.commit()
    return {"ok": True, "email": email, "status": "lifetime"}


@router.get("/admin/promo-codes", include_in_schema=False)
def admin_list_promo_codes(
    db: Session = Depends(get_db),
    x_admin_secret: str | None = Header(default=None),
):
    """Список всех промокодов с их статусом. Защищено ADMIN_SECRET."""
    if not ADMIN_SECRET or x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    rows = db.query(PromoCode).order_by(PromoCode.created_at).all()
    return [
        {
            "code": r.code,
            "is_used": r.is_used,
            "used_at": r.used_at.isoformat() if r.used_at else None,
        }
        for r in rows
    ]
