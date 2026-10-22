"""Счета на оплату подписки: период, выпуск, оплата администратором, PDF.

Счёт выставляется на один или оба маркетплейса по 1 490 ₽ за каждый в месяц. Период счёта:
- подписка оплачена и действует дальше сегодня: счёт начинается в день окончания текущего периода;
- иначе (первая оплата, демо-период, истёкшая подписка): счёт начинается с сегодняшнего дня по Москве.

Последний день периода входит в него: период «с 22.10.2026 по 21.11.2026» ровно один календарный месяц.
Оплаченный счёт продлевает доступ до конца последнего дня периода по Москве.
"""

import calendar
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.billing_invoice import BillingInvoice
from app.models.billing_requisites import BillingRequisites
from app.models.user import User
from app.services.billing_service import (
    _is_lifetime,
    _upsert_license,
    get_billing_status,
    get_or_create_subscription,
)
from app.services.invoice_pdf import (
    InvoiceBuyer,
    InvoicePdfData,
    invoice_number_display,
    invoice_pdf_filename,
    invoice_total,
    render_invoice_pdf,
)

MARKETPLACE_CODES: Final[tuple[str, ...]] = ("wb", "ozon")
MARKETPLACE_UNIT_PRICE: Final[Decimal] = Decimal("1490.00")
# Europe/Moscow с 2014 года работает на UTC+3 без перехода на летнее время.
MOSCOW_TZ: Final = timezone(timedelta(hours=3), "MSK")
STATUS_ISSUED: Final = "issued"
STATUS_PAID: Final = "paid"
STATUS_CANCELLED: Final = "cancelled"
INVOICE_STATUSES: Final[tuple[str, ...]] = (STATUS_ISSUED, STATUS_PAID, STATUS_CANCELLED)
# Advisory-блокировка на транзакцию: выпуск счетов идёт по одному, поэтому номера не повторяются
# и не теряются при гонке двух запросов.
INVOICE_ISSUE_LOCK_KEY: Final = 52_410_221
_STATUS_TITLE_RU: Final = {STATUS_PAID: "оплачен", STATUS_CANCELLED: "отменён"}


class InvoiceError(Exception):
    """Ошибка со счётом. Текст сообщения можно показывать пользователю как есть."""


class InvoiceNotAllowedError(InvoiceError):
    """По этому аккаунту счёт не выставляется (безлимитный доступ)."""


class RequisitesMissingError(InvoiceError):
    """Не сохранены реквизиты покупателя."""


class InvoiceNotFoundError(InvoiceError):
    """Счёт с таким номером или идентификатором не найден."""


class InvoiceStateError(InvoiceError):
    """Действие не применимо к текущему статусу счёта."""


@dataclass(frozen=True)
class InvoicePeriodPreview:
    period_start: date
    period_end: date
    marketplaces: tuple[str, ...]
    amount: Decimal


def _as_aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def to_moscow_date(moment: datetime) -> date:
    return _as_aware(moment).astimezone(MOSCOW_TZ).date()


def moscow_midnight(day: date) -> datetime:
    """Начало указанного дня по Москве (момент в таймзоне MSK)."""
    return datetime(day.year, day.month, day.day, tzinfo=MOSCOW_TZ)


def add_calendar_months(day: date, months: int) -> date:
    """Сдвиг на календарные месяцы; если в целевом месяце нет такого числа, берётся последний день месяца."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def normalize_marketplaces(values: Iterable[str]) -> tuple[str, ...]:
    """Список кодов маркетплейсов в каноническом порядке без повторов: ('wb', 'ozon')."""
    requested = {str(value).strip().lower() for value in values if str(value).strip()}
    unknown = requested.difference(MARKETPLACE_CODES)
    if unknown:
        raise ValueError(f"Неизвестный маркетплейс: {', '.join(sorted(unknown))}")
    selected = tuple(code for code in MARKETPLACE_CODES if code in requested)
    if not selected:
        raise ValueError("Выберите хотя бы один маркетплейс")
    return selected


def invoice_amount(marketplaces: tuple[str, ...]) -> Decimal:
    return invoice_total(MARKETPLACE_UNIT_PRICE, marketplaces)


def invoice_period_for(
    *,
    subscription_status: str | None,
    current_period_end: datetime | None,
    now: datetime,
) -> tuple[date, date]:
    """Период следующего счёта: (первый день, последний день включительно)."""
    start: date | None = None
    if subscription_status == "active" and current_period_end is not None:
        paid_until = _as_aware(current_period_end)
        if paid_until > now:
            start = to_moscow_date(paid_until)
    if start is None:
        start = to_moscow_date(now)
    return start, add_calendar_months(start, 1) - timedelta(days=1)


def _marketplace_key(values: Iterable[str]) -> tuple[str, ...]:
    present = set(values)
    return tuple(code for code in MARKETPLACE_CODES if code in present)


def _buyer_snapshot(requisites: BillingRequisites) -> dict[str, Any]:
    return {
        "name": requisites.name,
        "inn": requisites.inn,
        "kpp": requisites.kpp,
        "address": requisites.address,
    }


def preview_invoice_period(
    db: Session,
    user: User,
    marketplaces: Iterable[str],
    now: datetime,
) -> InvoicePeriodPreview:
    codes = normalize_marketplaces(marketplaces)
    user_id = str(user.id)
    if _is_lifetime(db, user_id):
        raise InvoiceNotAllowedError("Безлимитный доступ: счёт на оплату не требуется")
    sub = get_or_create_subscription(db, user_id)
    period_start, period_end = invoice_period_for(
        subscription_status=sub.status,
        current_period_end=sub.current_period_end,
        now=now,
    )
    return InvoicePeriodPreview(
        period_start=period_start,
        period_end=period_end,
        marketplaces=codes,
        amount=invoice_amount(codes),
    )


def create_or_reuse_invoice(
    db: Session,
    user: User,
    marketplaces: Iterable[str],
    now: datetime,
) -> BillingInvoice:
    """Выставить счёт или вернуть уже выставленный такой же.

    Неоплаченный счёт с тем же периодом, набором маркетплейсов и реквизитами покупателя переиспользуется
    (тот же номер). Остальные неоплаченные счета пользователя отменяются, новый счёт получает следующий номер.
    """
    codes = normalize_marketplaces(marketplaces)
    user_id = str(user.id)
    if _is_lifetime(db, user_id):
        raise InvoiceNotAllowedError("Безлимитный доступ: счёт на оплату не требуется")
    requisites = db.query(BillingRequisites).filter(BillingRequisites.user_id == user_id).first()
    if requisites is None:
        raise RequisitesMissingError("Сначала заполните и сохраните реквизиты покупателя")
    buyer = _buyer_snapshot(requisites)

    sub = get_or_create_subscription(db, user_id)
    period_start, period_end = invoice_period_for(
        subscription_status=sub.status,
        current_period_end=sub.current_period_end,
        now=now,
    )
    amount = invoice_amount(codes)

    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": INVOICE_ISSUE_LOCK_KEY})
    issued = (
        db.query(BillingInvoice)
        .filter(BillingInvoice.user_id == user_id, BillingInvoice.status == STATUS_ISSUED)
        .order_by(BillingInvoice.number.desc())
        .all()
    )
    reusable: BillingInvoice | None = None
    for invoice in issued:
        same = (
            invoice.period_start == period_start
            and invoice.period_end == period_end
            and _marketplace_key(invoice.marketplaces) == codes
            and invoice.buyer == buyer
        )
        if same and reusable is None:
            reusable = invoice
        else:
            invoice.status = STATUS_CANCELLED
    if reusable is not None:
        db.commit()
        return reusable

    number = int(db.query(func.max(BillingInvoice.number)).scalar() or 0) + 1
    invoice = BillingInvoice(
        user_id=user_id,
        number=number,
        number_display=invoice_number_display(number),
        issued_at=now,
        period_start=period_start,
        period_end=period_end,
        marketplaces=list(codes),
        amount=amount,
        status=STATUS_ISSUED,
        buyer=buyer,
    )
    db.add(invoice)
    db.commit()
    return invoice


def mark_invoice_paid(db: Session, invoice: BillingInvoice, now: datetime) -> None:
    """Отметить счёт оплаченным и продлить доступ до конца периода счёта (по Москве).

    Если уже оплаченный период заканчивается позже, он сохраняется: счёт не сокращает доступ.
    """
    if invoice.status != STATUS_ISSUED:
        current = str(invoice.status)
        title = _STATUS_TITLE_RU.get(current, current)
        raise InvoiceStateError(f"Счёт {invoice.number_display} нельзя отметить оплаченным: он уже {title}")
    user_id = str(invoice.user_id)
    sub = get_or_create_subscription(db, user_id)
    access_start = moscow_midnight(invoice.period_start)
    access_end = moscow_midnight(invoice.period_end + timedelta(days=1))
    if sub.current_period_end is None or _as_aware(sub.current_period_end) < access_end:
        sub.current_period_start = access_start
        sub.current_period_end = access_end
    sub.status = "active"
    sub.provider = "invoice"
    invoice.status = STATUS_PAID
    invoice.paid_at = now
    _upsert_license(db, user_id, "active", sub.current_period_end, "invoice")
    db.commit()


def admin_mark_invoice_paid(db: Session, number: int, now: datetime) -> dict[str, Any]:
    invoice = db.query(BillingInvoice).filter(BillingInvoice.number == number).first()
    if invoice is None:
        raise InvoiceNotFoundError(f"Счёт ФП-{number} не найден")
    mark_invoice_paid(db, invoice, now)
    user = db.query(User).filter(User.id == invoice.user_id).first()
    if user is None:
        raise InvoiceNotFoundError(f"Счёт ФП-{number}: пользователь не найден")
    return get_billing_status(db, user)


def list_user_invoices(db: Session, user_id: str) -> list[BillingInvoice]:
    return (
        db.query(BillingInvoice)
        .filter(BillingInvoice.user_id == user_id)
        .order_by(BillingInvoice.number.desc())
        .all()
    )


def find_user_invoice(db: Session, user_id: str, invoice_id: str) -> BillingInvoice | None:
    try:
        parsed = str(uuid.UUID(invoice_id))
    except ValueError:
        return None
    return (
        db.query(BillingInvoice)
        .filter(BillingInvoice.id == parsed, BillingInvoice.user_id == user_id)
        .first()
    )


def list_invoices_for_admin(db: Session, status: str | None) -> list[tuple[BillingInvoice, str | None]]:
    query = db.query(BillingInvoice, User.email).join(User, User.id == BillingInvoice.user_id)
    if status is not None:
        query = query.filter(BillingInvoice.status == status)
    rows = query.order_by(BillingInvoice.number.desc()).all()
    return [(invoice, email) for invoice, email in rows]


def render_invoice_pdf_for(invoice: BillingInvoice) -> tuple[bytes, str]:
    """PDF счёта и имя файла. Берётся содержимое из сохранённого при выпуске снимка."""
    marketplaces = tuple(invoice.marketplaces)
    unit_price = (Decimal(str(invoice.amount)) / len(marketplaces)).quantize(Decimal("0.01"))
    issued_on = to_moscow_date(invoice.issued_at)
    buyer: dict[str, Any] = invoice.buyer or {}
    data = InvoicePdfData(
        invoice_id=str(invoice.id),
        number=invoice.number,
        issued_on=issued_on,
        period_start=invoice.period_start,
        period_end=invoice.period_end,
        marketplaces=marketplaces,
        unit_price=unit_price,
        buyer=InvoiceBuyer(
            name=str(buyer.get("name") or ""),
            inn=str(buyer.get("inn") or ""),
            kpp=buyer.get("kpp"),
            address=str(buyer.get("address") or ""),
        ),
    )
    return render_invoice_pdf(data), invoice_pdf_filename(invoice.number, issued_on)
