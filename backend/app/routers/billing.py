import logging
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models.billing_invoice import BillingInvoice
from app.models.billing_requisites import BillingRequisites
from app.models.reminder_log import ReminderLog
from app.models.user import User
from app.schemas.billing import (
    AdminBillingInvoiceResponse,
    BillingInvoiceResponse,
    BillingRequisitesIn,
    BillingRequisitesResponse,
    BillingStatusResponse,
    CheckoutRequest,
    CheckoutResponse,
    CreateInvoiceRequest,
    InvoicePreviewResponse,
    WebhookResponse,
    YookassaSyncReturnResponse,
)
from app.models.promo_code import PromoCode
from app.services.billing_invoice_service import (
    INVOICE_STATUSES,
    InvoiceError,
    InvoiceNotFoundError,
    InvoiceStateError,
    admin_mark_invoice_paid,
    create_or_reuse_invoice,
    find_user_invoice,
    list_invoices_for_admin,
    list_user_invoices,
    preview_invoice_period,
    render_invoice_pdf_for,
)
from app.services.billing_service import (
    ADMIN_SECRET,
    YooKassaRequestError,
    create_checkout,
    get_billing_status,
    grant_lifetime,
    process_yookassa_webhook,
    sync_latest_yookassa_payment,
    utc_now,
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


class GrantAdminRequest(BaseModel):
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


@router.post("/admin/grant-admin", include_in_schema=False)
def admin_grant_admin(
    body: GrantAdminRequest,
    db: Session = Depends(get_db),
    x_admin_secret: str | None = Header(default=None),
):
    """Назначить is_admin пользователю по email. Защищено ADMIN_SECRET."""
    if not ADMIN_SECRET or x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    email = str(body.email).lower().strip()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    user.is_admin = True
    db.add(user)
    db.commit()
    return {"ok": True, "email": email, "is_admin": True}


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


def _requisites_response(row: BillingRequisites) -> BillingRequisitesResponse:
    return BillingRequisitesResponse(name=row.name, inn=row.inn, kpp=row.kpp, address=row.address)


def _invoice_response(invoice: BillingInvoice) -> BillingInvoiceResponse:
    buyer: dict[str, Any] = invoice.buyer or {}
    return BillingInvoiceResponse(
        id=str(invoice.id),
        number=invoice.number,
        number_display=invoice.number_display,
        issued_at=invoice.issued_at.isoformat(),
        period_start=invoice.period_start,
        period_end=invoice.period_end,
        marketplaces=list(invoice.marketplaces),
        amount=float(invoice.amount),
        status=invoice.status,
        paid_at=invoice.paid_at.isoformat() if invoice.paid_at else None,
        buyer=BillingRequisitesResponse(
            name=str(buyer.get("name") or ""),
            inn=str(buyer.get("inn") or ""),
            kpp=buyer.get("kpp"),
            address=str(buyer.get("address") or ""),
        ),
    )


def _attachment_header(filename: str, fallback: str) -> str:
    """Content-Disposition с ASCII-запасным именем и именем в кириллице по RFC 5987."""
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _check_admin_secret(x_admin_secret: str | None) -> None:
    if not ADMIN_SECRET or x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.get("/requisites", response_model=BillingRequisitesResponse | None)
def get_billing_requisites(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Реквизиты покупателя текущего пользователя или null, если ещё не сохранены."""
    row = db.query(BillingRequisites).filter(BillingRequisites.user_id == str(current_user.id)).first()
    return _requisites_response(row) if row is not None else None


@router.put("/requisites", response_model=BillingRequisitesResponse)
def put_billing_requisites(
    body: BillingRequisitesIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = str(current_user.id)
    row = db.query(BillingRequisites).filter(BillingRequisites.user_id == user_id).first()
    if row is None:
        row = BillingRequisites(user_id=user_id)
        db.add(row)
    row.name = body.name
    row.inn = body.inn
    row.kpp = body.kpp
    row.address = body.address
    row.updated_at = utc_now()
    db.commit()
    db.refresh(row)
    return _requisites_response(row)


@router.get("/invoices/preview", response_model=InvoicePreviewResponse)
def preview_billing_invoice(
    marketplaces: str = Query(..., description="Коды маркетплейсов через запятую: wb, ozon"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Период и сумма счёта без выпуска: что будет выставлено, если нажать «Сформировать счёт»."""
    try:
        preview = preview_invoice_period(db, current_user, marketplaces.split(","), utc_now())
    except (InvoiceError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return InvoicePreviewResponse(
        period_start=preview.period_start,
        period_end=preview.period_end,
        marketplaces=list(preview.marketplaces),
        amount=float(preview.amount),
    )


@router.post("/invoices", response_model=BillingInvoiceResponse)
def create_billing_invoice(
    body: CreateInvoiceRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Выставить счёт на выбранные маркетплейсы или вернуть уже выставленный такой же (тот же номер)."""
    try:
        invoice = create_or_reuse_invoice(db, current_user, body.marketplaces, utc_now())
    except (InvoiceError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _invoice_response(invoice)


@router.get("/invoices", response_model=list[BillingInvoiceResponse])
def list_billing_invoices(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return [_invoice_response(invoice) for invoice in list_user_invoices(db, str(current_user.id))]


@router.get("/invoices/{invoice_id}/pdf")
def download_billing_invoice_pdf(
    invoice_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    invoice = find_user_invoice(db, str(current_user.id), invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Счёт не найден")
    pdf, filename = render_invoice_pdf_for(invoice)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": _attachment_header(filename, f"invoice-FP-{invoice.number}.pdf")},
    )


@router.get("/admin/invoices", response_model=list[AdminBillingInvoiceResponse], include_in_schema=False)
def admin_list_invoices(
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    x_admin_secret: str | None = Header(default=None),
):
    """Счета всех пользователей (фильтр status=issued|paid|cancelled). Защищено ADMIN_SECRET."""
    _check_admin_secret(x_admin_secret)
    if status_filter is not None and status_filter not in INVOICE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неизвестный статус счёта")
    return [
        AdminBillingInvoiceResponse(**_invoice_response(invoice).model_dump(), user_email=email)
        for invoice, email in list_invoices_for_admin(db, status_filter)
    ]


@router.post("/admin/invoices/{number}/mark-paid", response_model=BillingStatusResponse, include_in_schema=False)
def admin_mark_invoice_paid_endpoint(
    number: int,
    db: Session = Depends(get_db),
    x_admin_secret: str | None = Header(default=None),
):
    """Отметить счёт оплаченным и продлить доступ. Защищено ADMIN_SECRET."""
    _check_admin_secret(x_admin_secret)
    try:
        payload = admin_mark_invoice_paid(db, number, utc_now())
    except InvoiceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvoiceStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return BillingStatusResponse(**payload)
