from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models.reminder_log import ReminderLog
from app.models.user import User
from app.schemas.billing import BillingStatusResponse, CheckoutRequest, CheckoutResponse, WebhookResponse
from app.models.promo_code import PromoCode
from app.services.billing_service import (
    ADMIN_SECRET,
    create_checkout,
    get_billing_status,
    grant_lifetime,
    process_yookassa_webhook,
)

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
    result = create_checkout(db, current_user, Decimal(str(body.amount)), body.return_url)
    return CheckoutResponse(**result)


@router.post("/webhook/yookassa", response_model=WebhookResponse, include_in_schema=False)
async def yookassa_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_webhook_secret: str | None = Header(default=None),
):
    payload = await request.json()
    try:
        process_yookassa_webhook(db, payload, x_webhook_secret)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
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
    user = db.query(User).filter(User.email == body.email).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    grant_lifetime(db, str(user.id))
    db.commit()
    return {"ok": True, "email": body.email, "status": "lifetime"}


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
