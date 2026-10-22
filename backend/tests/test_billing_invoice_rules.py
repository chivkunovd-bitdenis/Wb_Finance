"""Правила счетов без базы данных: периоды, маркетплейсы, сумма, запрет счетов для безлимитного доступа."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.user import User
from app.services.billing_invoice_service import (
    MOSCOW_TZ,
    InvoiceNotAllowedError,
    add_calendar_months,
    create_or_reuse_invoice,
    invoice_amount,
    invoice_period_for,
    moscow_midnight,
    normalize_marketplaces,
    preview_invoice_period,
    to_moscow_date,
)


def _user() -> User:
    return User(id="11111111-1111-4111-8111-111111111111", email="rules@example.com", password_hash="x", is_active=True)


def test_first_payment_starts_today_in_moscow_and_lasts_one_calendar_month() -> None:
    # 21.10 21:30 UTC уже 22.10 00:30 по Москве: период начинается 22.10 и идёт до 22.11 (не включая).
    now = datetime(2026, 10, 21, 21, 30, tzinfo=UTC)
    start, end = invoice_period_for(subscription_status="inactive", current_period_end=None, now=now)
    assert start == date(2026, 10, 22)
    assert end == date(2026, 11, 22)


def test_trial_starts_today_and_ignores_trial_end() -> None:
    now = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    start, end = invoice_period_for(subscription_status="trial", current_period_end=None, now=now)
    assert (start, end) == (date(2026, 10, 22), date(2026, 11, 22))


def test_expired_subscription_starts_today() -> None:
    now = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    past_end = datetime(2026, 9, 1, tzinfo=UTC)
    start, end = invoice_period_for(subscription_status="expired", current_period_end=past_end, now=now)
    assert (start, end) == (date(2026, 10, 22), date(2026, 11, 22))


def test_active_subscription_with_future_end_starts_on_moscow_date_of_that_end() -> None:
    now = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    # Оплачено до 00:00 3 ноября по Москве, значит следующий период начинается 3 ноября.
    paid_until = datetime(2026, 11, 3, 0, 0, tzinfo=MOSCOW_TZ)
    start, end = invoice_period_for(subscription_status="active", current_period_end=paid_until, now=now)
    assert start == date(2026, 11, 3)
    assert end == date(2026, 12, 3)


def test_consecutive_periods_chain_exactly_without_gaps_or_overlap() -> None:
    now = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    first = invoice_period_for(subscription_status="inactive", current_period_end=None, now=now)
    second = invoice_period_for(
        subscription_status="active", current_period_end=moscow_midnight(first[1]), now=now
    )
    third = invoice_period_for(
        subscription_status="active", current_period_end=moscow_midnight(second[1]), now=now
    )
    assert first == (date(2026, 10, 22), date(2026, 11, 22))
    assert second == (date(2026, 11, 22), date(2026, 12, 22))
    assert third == (date(2026, 12, 22), date(2027, 1, 22))


def test_active_subscription_with_past_end_starts_today() -> None:
    now = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    past_end = datetime(2026, 10, 1, tzinfo=UTC)
    start, _ = invoice_period_for(subscription_status="active", current_period_end=past_end, now=now)
    assert start == date(2026, 10, 22)


def test_naive_datetimes_are_treated_as_utc() -> None:
    assert to_moscow_date(datetime(2026, 10, 21, 22, 0)) == date(2026, 10, 22)


@pytest.mark.parametrize(
    ("start", "expected_end"),
    [
        (date(2027, 1, 31), date(2027, 2, 28)),  # короткий февраль: 31.01 -> 28.02
        (date(2028, 1, 31), date(2028, 2, 29)),  # високосный февраль: 31.01 -> 29.02
        (date(2026, 8, 31), date(2026, 9, 30)),  # сентябрь короче: 31.08 -> 30.09
        (date(2026, 12, 15), date(2027, 1, 15)),  # переход через год
        (date(2026, 10, 3), date(2026, 11, 3)),  # образец владельца: с 03.10 по 03.11
    ],
)
def test_period_end_is_same_day_next_month_clamped(start: date, expected_end: date) -> None:
    now = datetime.combine(start, datetime.min.time(), tzinfo=MOSCOW_TZ)
    _, end = invoice_period_for(subscription_status="inactive", current_period_end=None, now=now)
    assert end == expected_end


def test_add_calendar_months_clamps_to_last_day() -> None:
    assert add_calendar_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_calendar_months(date(2026, 3, 31), 1) == date(2026, 4, 30)
    assert add_calendar_months(date(2026, 10, 22), 1) == date(2026, 11, 22)


def test_marketplaces_are_normalized_to_canonical_order_without_duplicates() -> None:
    assert normalize_marketplaces(["ozon", "wb", "wb"]) == ("wb", "ozon")
    assert normalize_marketplaces([" WB "]) == ("wb",)


@pytest.mark.parametrize("values", [[], [""], ["yandex"], ["wb", "market"]])
def test_marketplaces_reject_empty_and_unknown(values: list[str]) -> None:
    with pytest.raises(ValueError):
        normalize_marketplaces(values)


def test_amount_is_1490_rubles_per_marketplace() -> None:
    assert invoice_amount(("wb",)) == Decimal("1490.00")
    assert invoice_amount(("ozon",)) == Decimal("1490.00")
    assert invoice_amount(("wb", "ozon")) == Decimal("2980.00")


def test_lifetime_account_gets_no_invoice_preview_or_issue() -> None:
    db = MagicMock()
    with patch("app.services.billing_invoice_service._is_lifetime", return_value=True):
        with pytest.raises(InvoiceNotAllowedError):
            preview_invoice_period(db, _user(), ["wb"], datetime(2026, 10, 22, tzinfo=UTC))
        with pytest.raises(InvoiceNotAllowedError):
            create_or_reuse_invoice(db, _user(), ["wb"], datetime(2026, 10, 22, tzinfo=UTC))
    db.add.assert_not_called()


def test_invoice_requires_saved_requisites() -> None:
    from app.services.billing_invoice_service import RequisitesMissingError

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    with (
        patch("app.services.billing_invoice_service._is_lifetime", return_value=False),
        pytest.raises(RequisitesMissingError),
    ):
        create_or_reuse_invoice(db, _user(), ["wb"], datetime(2026, 10, 22, tzinfo=UTC))
    db.add.assert_not_called()


def test_period_preview_uses_same_rule_as_issue() -> None:
    now = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    db = MagicMock()
    sub = MagicMock()
    sub.status = "active"
    sub.current_period_end = now + timedelta(days=10)
    with (
        patch("app.services.billing_invoice_service._is_lifetime", return_value=False),
        patch("app.services.billing_invoice_service.get_or_create_subscription", return_value=sub),
    ):
        preview = preview_invoice_period(db, _user(), ["wb", "ozon"], now)
    expected_start = to_moscow_date(sub.current_period_end)
    assert preview.period_start == expected_start
    assert preview.period_end == add_calendar_months(expected_start, 1)
    assert preview.amount == Decimal("2980.00")
