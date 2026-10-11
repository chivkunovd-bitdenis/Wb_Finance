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
    quantity: int | str | None = 1,
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
                    "quantity": quantity,
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
    assert daily["sold_quantity"] == Decimal("1")
    assert daily["returned_quantity"] == Decimal("0")
    assert daily["net_quantity"] == Decimal("1")
    assert daily["cogs"] == Decimal("20.00")
    assert daily["tax"] is None
    assert daily["ads_spend"] == Decimal("0")
    assert daily["margin"] is None
    assert daily["accrual_classification_complete"] is True
    assert daily["sku_allocation_complete"] is True
    assert daily["profitability_complete"] is True
    assert product["revenue"] == Decimal("100.00")
    assert product["commission"] == Decimal("15.00")
    assert product["logistics"] == Decimal("4.00")
    assert product["sold_quantity"] == Decimal("1")
    assert product["returned_quantity"] == Decimal("0")
    assert product["net_quantity"] == Decimal("1")
    assert product["cogs"] == Decimal("20.00")
    assert product["marketplace_net"] == Decimal("81.00")
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
    assert daily["sold_quantity"] == Decimal("1")
    assert daily["returned_quantity"] is None
    assert daily["net_quantity"] is None
    assert daily["reconciliation_difference"] == Decimal("0.00")


@pytest.mark.parametrize("quantity", [None, 0, -1, "NaN"])
def test_missing_or_invalid_posting_quantity_never_infers_cogs(quantity):
    source = posting_accrual(
        211,
        total="81.00",
        sale="100.00",
        commission="-15.00",
        delivery="-4.00",
        quantity=quantity,
    )

    normalized = normalize_accruals([source], [], costs={"sku-1": Decimal("20.00")})
    daily = daily_row(normalized)
    product = product_day(normalized)

    assert daily["sold_quantity"] is None
    assert daily["returned_quantity"] is None
    assert daily["net_quantity"] is None
    assert daily["cogs"] is None
    assert daily["profitability_complete"] is False
    assert product["net_quantity"] is None
    assert product["cogs"] is None


def test_sale_quantity_multiplies_known_cost_and_item_fee_quantity_is_not_sold_quantity():
    posting = posting_accrual(
        212,
        total="166.00",
        sale="200.00",
        commission="-30.00",
        delivery="-4.00",
        quantity=2,
    )
    item_acquiring = {
        "accrual_id": 213,
        "date": DAY,
        "accrued_category": "ITEM",
        "total_amount": money("-7.00"),
        "item_fees": {
            "fees": [
                {
                    "sku": "sku-1",
                    "quantity": 99,
                    "fees": [{"type_id": 1, "accrued": money("-7.00")}],
                }
            ]
        },
    }
    types = [{"id": 1, "name": "Acquiring", "description": "Эквайринг"}]

    normalized = normalize_accruals(
        [posting, item_acquiring],
        types,
        costs={"sku-1": Decimal("20.00")},
    )
    daily = daily_row(normalized)
    product = product_day(normalized)

    assert daily["sold_quantity"] == Decimal("2")
    assert daily["returned_quantity"] == Decimal("0")
    assert daily["net_quantity"] == Decimal("2")
    assert daily["cogs"] == Decimal("40.00")
    assert daily["acquiring"] == Decimal("7.00")
    assert product["sold_quantity"] == Decimal("2")
    assert product["cogs"] == Decimal("40.00")
    assert product["acquiring"] == Decimal("7.00")
    assert daily["reconciliation_difference"] == Decimal("0.00")


def test_cogs_stays_unknown_when_cost_is_not_configured():
    source = posting_accrual(
        2131,
        total="81.00",
        sale="100.00",
        commission="-15.00",
        delivery="-4.00",
    )

    normalized = normalize_accruals([source], [])
    daily = daily_row(normalized)
    product = product_day(normalized)

    assert daily["sold_quantity"] == Decimal("1")
    assert daily["cogs"] is None
    assert product["cogs"] is None
    assert daily["profitability_complete"] is False


def test_type_69_is_commission_and_positive_compensation_blocks_profitability():
    source = {
        "accrual_id": 2132,
        "date": DAY,
        "accrued_category": "NON_ITEM",
        "total_amount": money("5.00"),
        "non_item_fee": {"type_id": 81, "accrued": money("10.00")},
    }
    commission_reversal = {
        "accrual_id": 2133,
        "date": DAY,
        "accrued_category": "NON_ITEM",
        "total_amount": money("-3.00"),
        "non_item_fee": {"type_id": 69, "accrued": money("-3.00")},
    }
    types = [
        {"id": 69, "name": "SaleCommission", "description": "Вознаграждение за продажу"},
        {"id": 81, "name": "VolumeObligationReward", "description": "Бонус за достижение цели продаж"},
    ]

    normalized = normalize_accruals([source, commission_reversal], types)
    daily = daily_row(normalized)

    assert daily["commission"] == Decimal("3.00")
    assert daily["compensation"] == Decimal("10.00")
    assert daily["marketplace_net"] == Decimal("2.00")
    assert daily["accrual_classification_complete"] is True
    assert daily["profitability_complete"] is False
    assert any("налоговая база для компенсаций" in warning for warning in normalized["warnings"])


def test_type_id_and_exact_name_control_fee_classification_and_ambiguous_types_stay_signed():
    acquiring = {
        "accrual_id": 214,
        "date": DAY,
        "accrued_category": "ITEM",
        "total_amount": money("-5.00"),
        "item_fees": {
            "fees": [
                {"sku": "sku-1", "fees": [{"type_id": 1, "accrued": money("-5.00")}]}
            ]
        },
    }
    ambiguous = {
        "accrual_id": 215,
        "date": DAY,
        "accrued_category": "NON_ITEM",
        "total_amount": money("3.00"),
        "non_item_fee": {"type_id": 72, "accrued": money("3.00")},
    }
    types = [
        {"id": 1, "name": "Acquiring", "description": "Эквайринг"},
        {"id": 72, "name": "SetOff", "description": "Взаимозачет требований между Договорами"},
    ]

    normalized = normalize_accruals([acquiring, ambiguous], types)
    daily = daily_row(normalized)

    assert daily["acquiring"] == Decimal("5.00")
    assert daily["unallocated_amount"] == Decimal("3.00")
    assert daily["other_expenses"] == Decimal("0")
    assert daily["marketplace_net"] == Decimal("-2.00")
    assert daily["accrual_classification_complete"] is False
    assert daily["profitability_complete"] is False
    assert any("72 (SetOff;" in warning for warning in normalized["warnings"])


def test_known_fee_id_with_mismatched_dictionary_name_is_not_fuzzily_classified():
    source = {
        "accrual_id": 216,
        "date": DAY,
        "accrued_category": "NON_ITEM",
        "total_amount": money("-5.00"),
        "non_item_fee": {"type_id": 1, "accrued": money("-5.00")},
    }

    normalized = normalize_accruals(
        [source],
        [{"id": 1, "name": "OtherName", "description": "not the known exact type"}],
    )
    daily = daily_row(normalized)

    assert daily["acquiring"] is None
    assert daily["other_expenses"] == Decimal("5.00")
    assert daily["accrual_classification_complete"] is False


def test_unscoped_unknown_fee_invalidates_affected_product_day_columns_even_when_seen_first():
    unknown = {
        "accrual_id": 217,
        "date": DAY,
        "accrued_category": "NON_ITEM",
        "total_amount": money("-3.00"),
        "non_item_fee": {"type_id": 999, "accrued": money("-3.00")},
    }
    posting = posting_accrual(
        218,
        total="88.00",
        sale="100.00",
        commission="-10.00",
        delivery="-2.00",
    )
    types = [{"id": 999, "name": "UnknownFee", "description": "unknown unscoped amount"}]

    normalized = normalize_accruals([unknown, posting], types, costs={"sku-1": Decimal("20.00")})
    daily = daily_row(normalized)
    product = product_day(normalized)

    assert daily["penalties"] is None
    assert daily["other_expenses"] == Decimal("3.00")
    assert daily["profitability_complete"] is False
    assert product["penalties"] is None
    assert product["acquiring"] is None
    assert product["commission"] is None
    assert product["other_expenses"] is None
    assert product["marketplace_net"] is None
    assert product["profitability_complete"] is False


@pytest.mark.parametrize("unassigned_first", [False, True])
def test_posting_sale_without_sku_invalidates_known_sku_quantities_for_the_day(unassigned_first):
    source = posting_accrual(
        219,
        total="88.00",
        sale="100.00",
        commission="-10.00",
        delivery="-2.00",
        sku="known-sku",
    )
    unassigned = dict(source["posting"]["products"][0])
    unassigned["sku"] = None
    unassigned["quantity"] = 1
    unassigned["commission"] = {
        **unassigned["commission"],
        "sale_amount": money("200.00"),
        "commission": money("-20.00"),
    }
    unassigned["delivery"] = {
        "total_accrued": money("-4.00"),
        "services": [{"type_id": 501, "accrued": money("-4.00")}],
    }
    source["total_amount"] = money("264.00")
    source["posting"]["products"].append(unassigned)
    if unassigned_first:
        source["posting"]["products"].reverse()

    normalized = normalize_accruals([source], [], costs={"known-sku": Decimal("20.00")})
    daily = daily_row(normalized)
    product = product_day(normalized, sku="known-sku")

    assert daily["sold_quantity"] == Decimal("2")
    assert daily["cogs"] is None
    assert daily["sku_allocation_complete"] is False
    assert product["sold_quantity"] is None
    assert product["returned_quantity"] is None
    assert product["net_quantity"] is None
    assert product["cogs"] is None
    assert product["marketplace_net"] is None
    assert product["profitability_complete"] is False


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

    # Opposing discrepancies must remain nonzero both daily and when summed over a period.
    assert daily["reconciliation_difference"] == Decimal("2.00")
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
