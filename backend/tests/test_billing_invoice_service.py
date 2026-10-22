"""Счета на оплату на реальной PostgreSQL (транзакция откатывается). Без DATABASE_URL тесты пропускаются."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.billing_invoice import BillingInvoice
from app.models.billing_requisites import BillingRequisites
from app.models.license import License
from app.models.subscription import Subscription
from app.models.user import User
from app.services.billing_invoice_service import (
    MOSCOW_TZ,
    InvoiceNotAllowedError,
    InvoiceStateError,
    admin_mark_invoice_paid,
    create_or_reuse_invoice,
    mark_invoice_paid,
    moscow_midnight,
    preview_invoice_period,
)

# 09:00 UTC = 12:00 по Москве, 22 октября 2026.
NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


def _make_user(session) -> User:
    user = User(
        email=f"invoice-{uuid4()}@example.com",
        password_hash="$2b$12$fake",
        wb_api_key="k",
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _save_requisites(session, user: User, inn: str = "1234567890") -> None:
    session.add(
        BillingRequisites(
            user_id=str(user.id),
            name="ООО «ТЕСТ»",
            inn=inn,
            kpp="123456789",
            address="Г МОСКВА, УЛ ТЕСТОВАЯ, Д 1",
        )
    )
    session.commit()


def _subscription(session, user: User) -> Subscription:
    return session.query(Subscription).filter(Subscription.user_id == str(user.id)).first()


def test_first_invoice_covers_one_month_from_today_and_snapshots_buyer(real_db_session) -> None:
    session = real_db_session
    user = _make_user(session)
    _save_requisites(session, user)

    invoice = create_or_reuse_invoice(session, user, ["wb"], NOW)

    assert invoice.status == "issued"
    assert invoice.period_start == date(2026, 10, 22)
    assert invoice.period_end == date(2026, 11, 22)
    assert Decimal(str(invoice.amount)) == Decimal("1490.00")
    assert invoice.number_display == f"ФП-{invoice.number}"
    assert invoice.buyer == {"name": "ООО «ТЕСТ»", "inn": "1234567890", "kpp": "123456789", "address": "Г МОСКВА, УЛ ТЕСТОВАЯ, Д 1"}


def test_same_period_and_marketplaces_return_the_same_unpaid_invoice(real_db_session) -> None:
    session = real_db_session
    user = _make_user(session)
    _save_requisites(session, user)

    first = create_or_reuse_invoice(session, user, ["wb", "ozon"], NOW)
    second = create_or_reuse_invoice(session, user, ["ozon", "wb"], NOW + timedelta(hours=2))

    assert second.id == first.id
    assert second.number == first.number
    issued = (
        session.query(BillingInvoice)
        .filter(BillingInvoice.user_id == str(user.id), BillingInvoice.status == "issued")
        .count()
    )
    assert issued == 1


def test_different_marketplaces_cancel_unpaid_invoice_and_issue_new_number(real_db_session) -> None:
    session = real_db_session
    user = _make_user(session)
    _save_requisites(session, user)

    first = create_or_reuse_invoice(session, user, ["wb"], NOW)
    second = create_or_reuse_invoice(session, user, ["wb", "ozon"], NOW)

    session.refresh(first)
    assert first.status == "cancelled"
    assert second.status == "issued"
    assert second.number > first.number
    assert Decimal(str(second.amount)) == Decimal("2980.00")


def test_changed_buyer_requisites_cancel_previous_unpaid_invoice(real_db_session) -> None:
    session = real_db_session
    user = _make_user(session)
    _save_requisites(session, user, inn="1234567890")
    first = create_or_reuse_invoice(session, user, ["wb"], NOW)

    requisites = session.query(BillingRequisites).filter(BillingRequisites.user_id == str(user.id)).first()
    requisites.inn = "0987654321"
    session.commit()
    second = create_or_reuse_invoice(session, user, ["wb"], NOW)

    session.refresh(first)
    assert first.status == "cancelled"
    assert second.buyer["inn"] == "0987654321"


def test_mark_paid_extends_access_to_end_of_period_day_in_moscow(real_db_session) -> None:
    session = real_db_session
    user = _make_user(session)
    _save_requisites(session, user)
    invoice = create_or_reuse_invoice(session, user, ["wb"], NOW)

    mark_invoice_paid(session, invoice, NOW)

    sub = _subscription(session, user)
    lic = session.query(License).filter(License.user_id == str(user.id)).first()
    expected_end = datetime(2026, 11, 22, tzinfo=MOSCOW_TZ)
    assert invoice.status == "paid"
    assert invoice.paid_at is not None
    assert sub.status == "active"
    assert sub.provider == "invoice"
    assert sub.current_period_start == datetime(2026, 10, 22, tzinfo=MOSCOW_TZ)
    assert sub.current_period_end == expected_end
    assert lic.status == "active"
    assert lic.valid_until == expected_end


def test_mark_paid_keeps_later_existing_period_end(real_db_session) -> None:
    session = real_db_session
    user = _make_user(session)
    _save_requisites(session, user)
    invoice = create_or_reuse_invoice(session, user, ["wb"], NOW)

    # Подписка оплачена дальше, чем заканчивается счёт: счёт не должен её укоротить.
    sub = _subscription(session, user)
    sub.status = "active"
    sub.current_period_end = datetime(2027, 6, 1, tzinfo=MOSCOW_TZ)
    session.commit()

    mark_invoice_paid(session, invoice, NOW)

    sub = _subscription(session, user)
    assert sub.current_period_end == datetime(2027, 6, 1, tzinfo=MOSCOW_TZ)
    assert sub.status == "active"


def test_next_invoice_after_paid_period_starts_exactly_on_its_period_end(real_db_session) -> None:
    session = real_db_session
    user = _make_user(session)
    _save_requisites(session, user)
    invoice = create_or_reuse_invoice(session, user, ["wb"], NOW)
    mark_invoice_paid(session, invoice, NOW)

    following = create_or_reuse_invoice(session, user, ["wb"], NOW + timedelta(days=1))

    assert following.period_start == invoice.period_end == date(2026, 11, 22)
    assert following.period_end == date(2026, 12, 22)
    assert following.number > invoice.number


def test_paid_periods_chain_into_each_other_in_database(real_db_session) -> None:
    session = real_db_session
    user = _make_user(session)
    _save_requisites(session, user)
    previous_end = None
    for day in range(3):
        invoice = create_or_reuse_invoice(session, user, ["wb"], NOW + timedelta(days=day))
        if previous_end is not None:
            assert invoice.period_start == previous_end
        mark_invoice_paid(session, invoice, NOW + timedelta(days=day))
        previous_end = invoice.period_end
    sub = _subscription(session, user)
    assert sub.current_period_end == moscow_midnight(previous_end)


def test_paying_twice_is_rejected(real_db_session) -> None:
    session = real_db_session
    user = _make_user(session)
    _save_requisites(session, user)
    invoice = create_or_reuse_invoice(session, user, ["wb"], NOW)
    mark_invoice_paid(session, invoice, NOW)

    with pytest.raises(InvoiceStateError):
        mark_invoice_paid(session, invoice, NOW)


def test_admin_mark_paid_by_number_returns_active_billing_status(real_db_session) -> None:
    session = real_db_session
    user = _make_user(session)
    _save_requisites(session, user)
    invoice = create_or_reuse_invoice(session, user, ["wb", "ozon"], NOW)

    status = admin_mark_invoice_paid(session, invoice.number, NOW)

    assert status["subscription_status"] == "active"
    assert status["is_access_blocked"] is False
    assert status["days_left"] > 0


def test_lifetime_account_cannot_get_invoice_in_database(real_db_session) -> None:
    session = real_db_session
    user = _make_user(session)
    _save_requisites(session, user)
    session.add(License(user_id=str(user.id), status="lifetime", valid_until=None, source="manual"))
    session.commit()

    with pytest.raises(InvoiceNotAllowedError):
        preview_invoice_period(session, user, ["wb"], NOW)
    with pytest.raises(InvoiceNotAllowedError):
        create_or_reuse_invoice(session, user, ["wb"], NOW)


def test_moscow_midnight_is_utc_plus_three() -> None:
    assert moscow_midnight(date(2026, 10, 22)).astimezone(UTC) == datetime(2026, 10, 21, 21, 0, tzinfo=UTC)
