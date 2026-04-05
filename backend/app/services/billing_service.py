import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import requests
from sqlalchemy.orm import Session

from app.models.license import License
from app.models.payment import Payment
from app.models.promo_code import PromoCode
from app.models.reminder_log import ReminderLog
from app.models.subscription import Subscription
from app.models.user import User
from app.models.webhook_event import WebhookEvent

TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "5"))
SUBSCRIPTION_DAYS = int(os.getenv("SUBSCRIPTION_DAYS", "30"))
REMINDER_DAYS = [3, 1]
YOOKASSA_SHOP_ID = (os.getenv("YOOKASSA_SHOP_ID") or "").strip()
YOOKASSA_SECRET_KEY = (os.getenv("YOOKASSA_SECRET_KEY") or "").strip()
YOOKASSA_RETURN_URL = (os.getenv("YOOKASSA_RETURN_URL") or "").strip()
YOOKASSA_WEBHOOK_SECRET = (os.getenv("YOOKASSA_WEBHOOK_SECRET") or "").strip()
ADMIN_SECRET = (os.getenv("ADMIN_SECRET") or "").strip()


def utc_now() -> datetime:
    return datetime.now(UTC)


def _upsert_license(db: Session, user_id: str, status: str, valid_until: datetime | None, source: str | None) -> License:
    lic = db.query(License).filter(License.user_id == user_id).first()
    if not isinstance(lic, License):
        lic = License(user_id=user_id, status=status, valid_until=valid_until, source=source)
        db.add(lic)
    else:
        lic.status = status
        lic.valid_until = valid_until
        lic.source = source
        lic.updated_at = utc_now()
    return lic


def get_or_create_subscription(db: Session, user_id: str) -> Subscription:
    sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
    if isinstance(sub, Subscription):
        return sub
    sub = Subscription(user_id=user_id, status="inactive", auto_renew=True, provider="yookassa")
    db.add(sub)
    db.flush()
    return sub


def start_trial_if_needed(db: Session, user: User) -> Subscription:
    sub = get_or_create_subscription(db, str(user.id))
    if sub.trial_started_at or not user.wb_api_key or not user.wb_api_key.strip():
        return sub
    now = utc_now()
    sub.status = "trial"
    sub.trial_started_at = now
    sub.trial_ends_at = now + timedelta(days=TRIAL_DAYS)
    _upsert_license(db, str(user.id), "trial", sub.trial_ends_at, "trial")
    return sub


def _is_lifetime(db: Session, user_id: str) -> bool:
    """Проверить наличие пожизненного доступа."""
    lic = db.query(License).filter(License.user_id == user_id).first()
    if not isinstance(lic, License):
        return False
    return bool(lic.status == "lifetime")


def grant_lifetime(db: Session, user_id: str) -> License:
    """Выдать пожизненный доступ пользователю."""
    return _upsert_license(db, user_id, "lifetime", None, "manual")


def redeem_promo_code(db: Session, code: str, user_id: str) -> bool:
    """
    Активировать промокод для пользователя.
    Возвращает True если успешно, False если код не существует.
    Поднимает ValueError если код уже использован.
    """
    promo = (
        db.query(PromoCode)
        .filter(PromoCode.code == code.upper().strip())
        .first()
    )
    if not promo:
        return False
    if promo.is_used:
        raise ValueError("Промокод уже был использован")
    promo.is_used = True
    promo.used_by_user_id = user_id
    promo.used_at = utc_now()
    _upsert_license(db, user_id, "lifetime", None, "promo")
    return True


def get_billing_status(db: Session, user: User) -> dict[str, Any]:
    if _is_lifetime(db, str(user.id)):
        return {
            "subscription_status": "lifetime",
            "trial_ends_at": None,
            "current_period_ends_at": None,
            "auto_renew": False,
            "is_access_blocked": False,
            "days_left": 0,
        }
    sub = get_or_create_subscription(db, str(user.id))
    now = utc_now()
    valid_until = sub.current_period_end or sub.trial_ends_at
    active = valid_until is not None and valid_until > now and sub.status in {"trial", "active"}
    days_left = 0
    if valid_until:
        days_left = max(0, (valid_until - now).days)
    return {
        "subscription_status": sub.status,
        "trial_ends_at": sub.trial_ends_at.isoformat() if sub.trial_ends_at else None,
        "current_period_ends_at": sub.current_period_end.isoformat() if sub.current_period_end else None,
        "auto_renew": bool(sub.auto_renew),
        "is_access_blocked": not active,
        "days_left": days_left,
    }


def require_access(db: Session, user: User) -> None:
    if _is_lifetime(db, str(user.id)):
        return
    sub = get_or_create_subscription(db, str(user.id))
    now = utc_now()
    if sub.status == "inactive" and not sub.trial_ends_at and not sub.current_period_end:
        return
    valid_until = sub.current_period_end or sub.trial_ends_at
    if valid_until and valid_until > now and sub.status in {"trial", "active"}:
        return
    sub.status = "expired"
    _upsert_license(db, str(user.id), "expired", valid_until, "subscription")
    db.commit()
    raise PermissionError("Подписка или демо-период истекли. Продлите доступ на странице оплаты.")


def create_checkout(db: Session, user: User, amount: Decimal, return_url: str | None = None) -> dict[str, str]:
    sub = get_or_create_subscription(db, str(user.id))
    idem = str(uuid4())
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        payment_id = f"mock-{uuid4()}"
        pay = Payment(
            user_id=str(user.id),
            subscription_id=sub.id,
            provider="yookassa",
            provider_payment_id=payment_id,
            idempotency_key=idem,
            amount=amount,
            currency="RUB",
            status="pending",
            raw_payload={"mode": "mock"},
        )
        db.add(pay)
        db.commit()
        return {"payment_id": payment_id, "confirmation_url": (return_url or "/dashboard")}

    payload = {
        "amount": {"value": str(amount), "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url or YOOKASSA_RETURN_URL},
        "description": "WB Finance Pro monthly subscription",
        "metadata": {"user_id": str(user.id)},
    }
    response = requests.post(
        "https://api.yookassa.ru/v3/payments",
        auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY),
        headers={"Idempotence-Key": idem},
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    pay = Payment(
        user_id=str(user.id),
        subscription_id=sub.id,
        provider="yookassa",
        provider_payment_id=str(data.get("id")),
        idempotency_key=idem,
        amount=amount,
        currency="RUB",
        status=str(data.get("status") or "pending"),
        raw_payload=data,
    )
    db.add(pay)
    db.commit()
    confirmation = ((data.get("confirmation") or {}).get("confirmation_url")) or (return_url or "/dashboard")
    return {"payment_id": str(data.get("id")), "confirmation_url": str(confirmation)}


def activate_subscription_from_payment(db: Session, user_id: str, provider_payment_id: str, payload: dict[str, Any]) -> None:
    sub = get_or_create_subscription(db, user_id)
    now = utc_now()
    start = sub.current_period_end if sub.current_period_end and sub.current_period_end > now else now
    end = start + timedelta(days=SUBSCRIPTION_DAYS)
    sub.status = "active"
    sub.current_period_start = start
    sub.current_period_end = end
    sub.auto_renew = True
    _upsert_license(db, user_id, "active", end, "subscription")
    p = db.query(Payment).filter(Payment.provider_payment_id == provider_payment_id).first()
    if p:
        p.status = "succeeded"
        p.paid_at = now
        p.raw_payload = payload
    db.commit()


def process_yookassa_webhook(db: Session, payload: dict[str, Any], signature: str | None) -> None:
    if YOOKASSA_WEBHOOK_SECRET and signature != YOOKASSA_WEBHOOK_SECRET:
        raise ValueError("Invalid webhook signature")
    event = str(payload.get("event") or "")
    obj = payload.get("object") or {}
    payment_id = str(obj.get("id") or "")
    user_id = str((obj.get("metadata") or {}).get("user_id") or "")
    if not payment_id or not user_id:
        return
    event_key = f"{event}:{payment_id}"
    exists = db.query(WebhookEvent).filter(WebhookEvent.event_key == event_key).first()
    if isinstance(exists, WebhookEvent):
        return
    db.add(WebhookEvent(provider="yookassa", event_key=event_key, payload=payload))
    if event == "payment.succeeded":
        activate_subscription_from_payment(db, user_id, payment_id, payload)
    elif event in {"payment.canceled", "payment.waiting_for_capture"}:
        p = db.query(Payment).filter(Payment.provider_payment_id == payment_id).first()
        if p:
            p.status = "failed" if event == "payment.canceled" else "pending"
            p.raw_payload = payload
            db.commit()
        else:
            db.commit()


def collect_due_reminders(db: Session) -> int:
    now = utc_now()
    subs = db.query(Subscription).filter(Subscription.status.in_(["trial", "active"])).all()
    created = 0
    for sub in subs:
        valid_until = sub.current_period_end or sub.trial_ends_at
        if not valid_until:
            continue
        days_left = (valid_until - now).days
        if days_left not in REMINDER_DAYS:
            continue
        for channel in ("in_app", "email"):
            exists = (
                db.query(ReminderLog)
                .filter(
                    ReminderLog.user_id == sub.user_id,
                    ReminderLog.channel == channel,
                    ReminderLog.reminder_type == f"expires_in_{days_left}",
                )
                .first()
            )
            if exists:
                continue
            db.add(
                ReminderLog(
                    user_id=sub.user_id,
                    reminder_type=f"expires_in_{days_left}",
                    channel=channel,
                    status="sent" if channel == "in_app" else "scheduled",
                    due_at=valid_until,
                    sent_at=now if channel == "in_app" else None,
                )
            )
            created += 1
    db.commit()
    return created
