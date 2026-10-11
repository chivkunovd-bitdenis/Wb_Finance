from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.ozon_finance import normalize_accruals
from app.services.ozon_client import OzonContractError


DAY = "2026-10-11"
RUB = "RUB"


def money(amount: str, currency: str = RUB) -> dict[str, str]:
    return {"amount": amount, "currency": currency}


def posting_accrual(
    accrual_id: int,
    *,
    total: str,
    sale: str,
    commission: str,
    delivery: str,
    sku: str = "sku-1",
    day: str = DAY,
    currency: str = RUB,
) -> dict:
    return {
        "accrual_id": accrual_id,
        "date": day,
        "unit_number": f"posting-{accrual_id}",
        "accrued_category": "POSTING",
        "total_amount": money(total, currency),
        "posting": {
            "products": [
                {
                    "sku": sku,
                    "commission": {
                        "sale_amount": money(sale, currency),
                        "commission": money(commission, currency),
                        # These fields are diagnostic breakdown, not extra ledger lines.
                        "sale_commission": money("-17.00", currency),
                        "bonus": money("-2.00", currency),
                        "coinvestment": money("-3.00", currency),
                        "sale_price": money("120.00", currency),
                        "seller_price": money("100.00", currency),
                    },
                    "delivery": {
                        "total_accrued": money(delivery, currency),
                        "services": [{"type_id": 501, "accrued": money(delivery, currency)}],
                    },
                }
            ]
        },
    }


def daily_row(normalized: dict, day: str = DAY) -> dict:
    return next(row for row in normalized["daily"] if row["date"] == day)


def product_day(normalized: dict, sku: str = "sku-1", day: str = DAY) -> dict:
    product = next(row for row in normalized["products"] if row["sku"] == sku)
    return next(row for row in product["daily"] if row["date"] == day)


def test_normalizes_real_shaped_posting_with_product_delivery_and_positive_expense_display():
    source = posting_accrual(
        101,
        total="81.00",
        sale="100.00",
        commission="-15.00",
        delivery="-4.00",
    )

    normalized = normalize_accruals([source], [], costs={"sku-1": Decimal("20.00")})
    daily = daily_row(normalized)
    product = product_day(normalized)

    assert daily["revenue"] == Decimal("100.00")
    assert daily["commission"] == Decimal("15.00")
    assert daily["logistics"] == Decimal("4.00")
    assert daily["marketplace_net"] == Decimal("81.00")
    assert daily["reconciliation_difference"] == Decimal("0.00")
    assert daily["cogs"] is None
    assert daily["tax"] is None
    assert daily["ads_spend"] is None
    assert daily["margin"] is None
    assert product["revenue"] == Decimal("100.00")
    assert product["commission"] == Decimal("15.00")
    assert product["logistics"] == Decimal("4.00")
    assert product["cogs"] is None
    assert product["source_accrual_ids"] == ["101"]
    # sale_commission, bonus, and coinvestment are never added a second time.
    assert "дубли" not in " ".join(normalized["warnings"])


def test_returns_preserve_signed_income_and_expense_reversals_and_net_zero_still_has_unknown_cogs():
    sale = posting_accrual(
        201,
        total="81.00",
        sale="100.00",
        commission="-15.00",
        delivery="-4.00",
    )
    returned = posting_accrual(
        202,
        total="-81.00",
        sale="-100.00",
        commission="15.00",
        delivery="4.00",
    )

    normalized = normalize_accruals([sale, returned], [])
    daily = daily_row(normalized)
    product = product_day(normalized)

    assert daily["revenue"] == Decimal("0.00")
    assert daily["commission"] == Decimal("0.00")
    assert daily["logistics"] == Decimal("0.00")
    assert daily["marketplace_net"] == Decimal("0.00")
    assert daily["cogs"] is None
    assert product["cogs"] is None
    assert daily["reconciliation_difference"] == Decimal("0.00")


def test_per_accrual_reconciliation_warnings_do_not_cancel_each_other():
    first = posting_accrual(
        301,
        total="11.00",
        sale="10.00",
        commission="0.00",
        delivery="0.00",
    )
    second = posting_accrual(
        302,
        total="-11.00",
        sale="-10.00",
        commission="0.00",
        delivery="0.00",
    )

    normalized = normalize_accruals([first, second], [])
    daily = daily_row(normalized)

    # Day-level signed differences net to zero, but each source operation stays visible.
    assert daily["reconciliation_difference"] == Decimal("0.00")
    assert any("начисление 301 не сверяется" in warning for warning in normalized["warnings"])
    assert any("начисление 302 не сверяется" in warning for warning in normalized["warnings"])


def test_unknown_item_fee_is_visible_and_unverified_categories_are_null():
    source = {
        "accrual_id": 401,
        "date": DAY,
        "accrued_category": "ITEM",
        "total_amount": money("-7.00"),
        "item_fees": {
            "fees": [
                {
                    "sku": "sku-1",
                    "fees": [{"type_id": 902, "accrued": money("-7.00")}],
                }
            ]
        },
    }
    types = [{"id": 902, "name": "FEE_MACHINE_NAME", "description": "Fee category description"}]

    normalized = normalize_accruals([source], types)
    daily = daily_row(normalized)
    product = product_day(normalized)

    assert daily["other_expenses"] == Decimal("7.00")
    assert daily["unallocated_amount"] == Decimal("0")
    assert daily["penalties"] is None
    assert daily["storage"] is None
    assert daily["logistics"] is None
    assert product["other_expenses"] == Decimal("7.00")
    assert product["penalties"] is None
    warning = " ".join(normalized["warnings"])
    assert "902" in warning
    assert "FEE_MACHINE_NAME" in warning
    assert "Fee category description" in warning


def test_unknown_category_keeps_signed_total_unallocated_and_breakdown_unknown():
    source = {
        "accrual_id": 501,
        "date": DAY,
        "accrued_category": "NEW_PROVIDER_CATEGORY",
        "total_amount": money("-12.50"),
    }

    normalized = normalize_accruals([source], [])
    daily = daily_row(normalized)

    assert daily["marketplace_net"] == Decimal("-12.50")
    assert daily["unallocated_amount"] == Decimal("-12.50")
    assert daily["revenue"] is None
    assert daily["other_expenses"] is None
    assert "NEW_PROVIDER_CATEGORY" in " ".join(normalized["warnings"])


def test_unallocated_amount_is_signed_source_control_total():
    expense = {
        "accrual_id": 511,
        "date": DAY,
        "accrued_category": "NON_ITEM",
        "total_amount": money("-5.00"),
        "non_item_fee": {"type_id": 911, "accrued": money("-5.00")},
    }
    reversal = {
        "accrual_id": 512,
        "date": DAY,
        "accrued_category": "NON_ITEM",
        "total_amount": money("3.00"),
        "non_item_fee": {"type_id": 912, "accrued": money("3.00")},
    }

    normalized = normalize_accruals([expense, reversal], [])
    daily = daily_row(normalized)

    assert daily["unallocated_amount"] == Decimal("-2.00")
    assert daily["other_expenses"] == Decimal("2.00")


def test_missing_source_money_is_null_not_zero_and_invalidates_reconciliation():
    source = posting_accrual(
        601,
        total="0.00",
        sale="0.00",
        commission="0.00",
        delivery="0.00",
    )
    source["posting"]["products"][0]["commission"]["sale_amount"] = None

    normalized = normalize_accruals([source], [])
    daily = daily_row(normalized)
    product = product_day(normalized)

    assert daily["revenue"] is None
    assert daily["cogs"] is None
    assert daily["reconciliation_difference"] is None
    assert product["revenue"] is None
    assert product["cogs"] is None
    assert any("sale_amount" in warning for warning in normalized["warnings"])


def test_mixed_currencies_and_conflicting_duplicate_ids_are_rejected():
    usd = posting_accrual(
        701,
        total="1.00",
        sale="1.00",
        commission="0.00",
        delivery="0.00",
        currency="USD",
        sku="sku-usd",
    )
    rub = posting_accrual(
        702,
        total="1.00",
        sale="1.00",
        commission="0.00",
        delivery="0.00",
        currency="RUB",
        sku="sku-rub",
    )
    with pytest.raises(OzonContractError, match="разных валютах"):
        normalize_accruals([usd, rub], [])

    duplicate = posting_accrual(
        703,
        total="1.00",
        sale="1.00",
        commission="0.00",
        delivery="0.00",
    )
    conflicting = posting_accrual(
        703,
        total="2.00",
        sale="2.00",
        commission="0.00",
        delivery="0.00",
    )
    with pytest.raises(OzonContractError, match="разные записи"):
        normalize_accruals([duplicate, conflicting], [])


def test_identical_duplicate_is_counted_once_with_warning():
    source = posting_accrual(
        801,
        total="81.00",
        sale="100.00",
        commission="-15.00",
        delivery="-4.00",
    )

    normalized = normalize_accruals([source, dict(source)], [])
    daily = daily_row(normalized)

    assert daily["marketplace_net"] == Decimal("81.00")
    assert daily["revenue"] == Decimal("100.00")
    assert daily["source_accrual_ids"] == ["801"]
    assert any("учтена один раз" in warning for warning in normalized["warnings"])


def test_empty_input_returns_no_invented_day():
    assert normalize_accruals([], []) == {"daily": [], "products": [], "warnings": []}
