"""Conservative Decimal normalization for Ozon's daily accrual ledger.

The source operation total remains authoritative. Component details are used
for display, SKU attribution, and a separate reconciliation check. Ozon's raw
signed payload remains available to the persistence layer; expense metrics are
flipped to WB Finance's positive-expense convention for marketplace comparison.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from app.services.ozon_client import OzonContractError


_AMOUNT_FIELDS = (
    "revenue",
    "commission",
    "logistics",
    "acquiring",
    "penalties",
    "storage",
    "ads_spend",
    "other_expenses",
    "compensation",
    "cogs",
    "tax",
    "operation_expenses",
    "margin",
    "marketplace_net",
    "unallocated_amount",
    "reconciliation_difference",
)
_POSSIBLE_FEE_FIELDS = (
    "commission",
    "logistics",
    "acquiring",
    "penalties",
    "storage",
    "ads_spend",
    "compensation",
)
_SOURCE_EXPENSE_FIELDS = {
    "commission",
    "logistics",
    "acquiring",
    "penalties",
    "storage",
    "ads_spend",
    "other_expenses",
    "operation_expenses",
}
_ISO_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# This map is deliberately keyed by both the numeric ID and the exact official
# dictionary name. A changed or mismatched provider dictionary remains
# unclassified instead of being routed by a fuzzy translated-name match.
_TYPE_CATEGORY_BY_ID_NAME: dict[tuple[str, str], str] = {}


def _register_type_names(category: str, entries: str) -> None:
    for entry in entries.split(","):
        type_id, name = entry.strip().split(" ", 1)
        _TYPE_CATEGORY_BY_ID_NAME[(type_id, name)] = category


_register_type_names("acquiring", "1 Acquiring")
_register_type_names("commission", "63 RfbsDomesticAgentFee, 66 RfbsGlobalAgentFee, 69 SaleCommission")
_register_type_names(
    "logistics",
    "2 BackwardShipment, 6 Cancellation, 9 ClientReturn, 12 CrossDock, "
    "13 CrossDockPickUpCourierDelivery, 16 Drop-Off, 17 Drop-Off Agent, "
    "21 Fulfillment, 28 LastMile, 29 LastMileCourier, 30 LastMilePickUpPoint, "
    "32 Logistic, 38 PackageCost, 39 PackingFee, 40 PartialReturn, 42 Pick-Up, "
    "43 PickUpCourierArrangement, 44 PickUpCourierDelivery, 45 PickUpPointReturnAcceptance, "
    "53 PreparingToReturn, 56 QuantProcessingDrop, 58 Replenishment, 59 ReturnFlowLogistic, "
    "64 RfbsDomesticDelivery, 65 RfbsEasyReturn, "
    "67 RfbsGlobalDelivery, 71 SellerReturns, 73 Shipment, 77 SupplyInbound, "
    "82 VolumeWeightCharacteristicsProcessing, 84 ItemPacking, 85 ItemSealing, "
    "86 PackmanCisPacking, 88 ClickAndCollect, 97 PackageUnitProcessing, "
    "98 DeliveryToHandoverPlaceByOzon, 99 InternationalLogisticDelta, "
    "100 OzonGlobalLogisticsDelivery, 101 OversizedExtraHandling, 103 B2CDisposal, "
    "106 B2C Drop-Off, 107 B2C Drop-Off Agent, 108 B2CContainerPacking, "
    "109 B2CContainerPackage, 110 B2CCourierClientReinvoice, "
    "111 B2CDeliveryToHandoverPlaceByOzon, 112 B2CPickUpPointClientReinvoice, "
    "113 B2CPickUpPointReturnAcceptance, 114 B2CLogistics, 115 B2CBackwardLogistics, "
    "120 CourierPickUpByOzon, 121 CourierPickUpReinvoice, 122 RfbsGlobalIntermediaryService, "
    "131 MicroFulfillmentSupply, 132 MicroFulfillmentPicking, 133 CrossDockHandlingAtDropOff, "
    "134 CrossDockDeliveryToAcceptancePoint, 135 CrossDockPartnerHandling",
)
_register_type_names(
    "storage",
    "46 Placements, 60 ReturnStorageInTheWarehouse, 78 TemporaryPlacement, "
    "79 TemporaryPlacementsAgent, 102 B2CTemporaryPlacement",
)
_register_type_names(
    "penalties",
    "89 DefectFineModeration, 90 DefectFineProhibitedGoods, 91 DefectFineCounterfeitGoods, "
    "92 DefectFineComplaint, 93 DefectFineErrors, 94 DefectFineShipmentDelayRate",
)
_register_type_names(
    "ads_spend",
    "3 BrandCommission, 4 BrandPromotion, 5 BrandShelf, 19 ExternalPromotion, "
    "23 InternetSiteAdvertising, 33 Marketing, 36 OrdersBooking, 41 PayPerClick, "
    "47 PointsForReviews, 49 PremiumCashbackPromotion, 54 Promotion, 55 PushCampaign, "
    "61 ReviewsPin, 70 SaleReview, 74 StarsMembership, 75 Stencil, "
    "87 SocialMediaAdvertising, 96 AcceleratedReviewCollection, 116 FirstCustomerReview, "
    "118 LabelBrandVerified, 119 CustomerChatPoints, 130 DisplayAdvertisingPlacement",
)
_register_type_names("compensation", "10 Compensation, 25 ItemCompensation, 81 VolumeObligationReward, 104 B2CInsuranceCompensation")
_register_type_names(
    "other_expenses",
    "7 Charity, 14 DefectRate, 15 Disposal, 24 ItemCloning, 27 LabelOriginal, "
    "31 LeadGeneration, 34 Marking, 35 Moderation, 37 OzonData, 51 PremiumMembership, "
    "52 PremiumSubscription, 68 RfbsServiceFee, 76 StockInsurance, 80 VideoCover, "
    "95 CustomerReviews, 105 B2CInsuranceShipping, 117 IncreaseAssortmentLimit, "
    "123 RfbsGlobalPlatformConnectionService, 125 AnalyticsLite, 126 AnalyticsPremium, "
    "127 AnalyticsPlus, 128 AnalyticsPro, 129 AnalyticsCorrection",
)

# These exact Ozon dictionary types can arise from return handling. The fee
# itself remains a known expense, but neither the affected sales quantity nor
# the corresponding COGS reversal can be inferred from that fee alone.
_RETURN_RELATED_TYPE_ID_NAMES = {
    ("2", "BackwardShipment"),
    ("6", "Cancellation"),
    ("9", "ClientReturn"),
    ("40", "PartialReturn"),
    ("45", "PickUpPointReturnAcceptance"),
    ("53", "PreparingToReturn"),
    ("59", "ReturnFlowLogistic"),
    ("60", "ReturnStorageInTheWarehouse"),
    ("65", "RfbsEasyReturn"),
    ("71", "SellerReturns"),
    ("113", "B2CPickUpPointReturnAcceptance"),
    ("115", "B2CBackwardLogistics"),
}

# These dictionary entries are real, but their meaning is a payout adjustment,
# settlement, correction, seller bonus, or security amount rather than a proven
# P&L category. Keep their signed values in unallocated_amount.
_AMBIGUOUS_TYPE_NAMES = {
    ("8", "ClaimCommission"),
    ("11", "CorrectionCommission"),
    ("18", "EarlyPayment"),
    ("20", "FlexiblePayments"),
    ("22", "Installment"),
    ("26", "KazakhstanBuyerInstallment"),
    ("48", "PremiumCashbackIndividualPoints"),
    ("50", "PremiumMailingCommission"),
    ("57", "RealizationReportCorrection"),
    ("62", "RfbsClientDeliveryCharge"),
    ("72", "SetOff"),
    ("83", "BrandDeposit"),
    ("124", "RfbsBuyerDelivery"),
}


def normalize_accruals(
    accruals: list[dict[str, Any]],
    accrual_types: list[dict[str, Any]],
    costs: dict[str, Decimal | None] | None = None,
) -> dict[str, Any]:
    """Return daily values, SKU values grouped by day, and data-quality warnings.

    Quantity is used only when the source carries it directly on a POSTING
    product. Fee quantities and monetary ratios are never treated as sales.
    """
    if not isinstance(accruals, list):
        raise OzonContractError("Список начислений Ozon имеет неверный формат")
    if not isinstance(accrual_types, list):
        raise OzonContractError("Справочник типов начислений Ozon имеет неверный формат")

    type_names = _type_names(accrual_types)
    unique, warnings = _deduplicate(accruals)
    daily_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    products_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    seen_currencies: set[str] = set()
    # Per accrual: does its total equal the sum of its complete details? Products rely on their own accruals only.
    accrual_reconciled: dict[str, bool] = {}

    for accrual in unique:
        accrual_id = str(accrual["accrual_id"])
        day = _accrual_day(accrual.get("date"))
        authoritative_total, currency = _parse_money(accrual.get("total_amount"), "total_amount")
        seen_currencies.add(currency)
        if len(seen_currencies) > 1:
            raise OzonContractError("Начисления Ozon в разных валютах нельзя объединить")

        row = daily_by_key.setdefault((day, currency), _empty_daily(day, currency))
        _sum(row, "marketplace_net", authoritative_total)
        row["source_accrual_ids"].append(accrual_id)
        category = _accrual_category(accrual.get("accrued_category"))
        row["source_categories"].append(category)

        raw_components, complete = _normalize_accrual(
            accrual=accrual,
            category=category,
            authoritative_total=authoritative_total,
            day=day,
            currency=currency,
            accrual_id=accrual_id,
            type_names=type_names,
            daily=row,
            products=products_by_key,
            warnings=warnings,
        )
        if complete:
            difference = authoritative_total - raw_components
            # Sum magnitudes so failures cannot cancel within a day or across
            # days in the UI. Direction remains in the per-accrual warning.
            _sum(row, "reconciliation_difference", abs(difference))
            accrual_reconciled[accrual_id] = difference == 0
            if difference != 0:
                warnings.append(
                    f"{day}: начисление {accrual_id} не сверяется: total_amount отличается "
                    f"от суммы деталей на {difference} {currency}"
                )
        else:
            accrual_reconciled[accrual_id] = False
            row["reconciliation_difference"] = None
            row["_reconciliation_incomplete"] = True

    if len(seen_currencies) > 1:
        raise OzonContractError("Начисления Ozon в разных валютах нельзя объединить")

    daily_rows: list[dict[str, Any]] = []
    daily_quality: dict[tuple[str, str], dict[str, bool]] = {}
    product_day_unknown_fields: dict[tuple[str, str], set[str]] = {}
    for key in sorted(daily_by_key):
        row = daily_by_key[key]
        day = row["date"]
        day_gap_fields = _resolve_gap_fields(row)
        if row.pop("_reconciliation_incomplete", False):
            row["reconciliation_difference"] = None
            warnings.append(f"{day}: сверка недоступна, так как детали начислений неполные")
        sale_activity = row.pop("_has_sale_activity", False)
        quantity_complete = row.pop("_quantity_complete", True)
        quantity_sku_complete = row.pop("_quantity_sku_complete", True)
        return_activity = row.pop("_return_activity", False)
        unscoped_return_activity = row.pop("_unscoped_return_activity", False)
        missing_sku_cost = False
        cogs = Decimal("0")
        known_cost_revenue = Decimal("0")
        cost_share_unknown = False
        for product_key, product in products_by_key.items():
            if product_key[0] != key[0] or product_key[2] != key[1]:
                continue
            product.pop("_has_sale_activity", None)
            product_quantity_complete = product.pop("_quantity_complete", True)
            # The flag is read here and consumed by the product pass below (profit gate).
            product_return = product.get("_return_activity", False) or unscoped_return_activity
            sold_quantity = product.get("sold_quantity")
            returned_quantity = product.get("returned_quantity")
            if not product_quantity_complete:
                product["returned_quantity"] = None
                returned_quantity = None
            # Owner decision (2026-10-25): units of a return posting go back to stock and are resold, so they
            # net out of COGS. A return signal with no return posting quantity cannot be netted: stays unknown.
            return_blocked = product_return and not (returned_quantity is not None and returned_quantity > 0)
            if return_blocked:
                # A return fee without its return posting: the returned units themselves are unknown.
                product["returned_quantity"] = None
                returned_quantity = None
            net_quantity = (
                sold_quantity - returned_quantity
                if product_quantity_complete
                and not return_blocked
                and sold_quantity is not None
                and returned_quantity is not None
                else None
            )
            product["net_quantity"] = net_quantity
            if net_quantity is None:
                product["cogs"] = None
            elif net_quantity == 0:
                product["cogs"] = Decimal("0")
            else:
                cost = _valid_cost((costs or {}).get(product_key[1]))
                if cost is None:
                    product["cogs"] = None
                    missing_sku_cost = True
                else:
                    product["cogs"] = cost * net_quantity
                    cogs += product["cogs"]
            # Revenue of SKUs whose COGS is known, to report how much of the day's revenue has a cost behind it.
            if product.get("cogs") is not None:
                product_revenue = product.get("revenue")
                if product_revenue is None:
                    cost_share_unknown = True
                else:
                    known_cost_revenue += product_revenue

        sold_quantity = row.get("sold_quantity")
        returned_quantity = row.get("returned_quantity")
        if not quantity_complete:
            row["returned_quantity"] = None
            returned_quantity = None
        return_blocked = return_activity and not (returned_quantity is not None and returned_quantity > 0)
        if return_blocked:
            row["returned_quantity"] = None
            returned_quantity = None
        row["net_quantity"] = (
            sold_quantity - returned_quantity
            if quantity_complete
            and not return_blocked
            and sold_quantity is not None
            and returned_quantity is not None
            else None
        )
        if not sale_activity and quantity_complete and returned_quantity == 0:
            row["cogs"] = Decimal("0")
        elif (
            quantity_complete
            and quantity_sku_complete
            and not return_blocked
            and sold_quantity is not None
            and returned_quantity is not None
            and not missing_sku_cost
        ):
            row["cogs"] = cogs
        else:
            row["cogs"] = None
            warnings.append(f"{day}: себестоимость неизвестна из-за неполного количества, возврата или отсутствующей себестоимости")
        # Tax and general operating expenses are handled by the aggregation
        # service; this ledger does not contain those values.
        row["tax"] = None
        row["operation_expenses"] = None
        row["margin"] = None
        day_revenue = row.get("revenue")
        # A partial day revenue (a posting's sale is missing) cannot anchor a share.
        if cost_share_unknown or "revenue" in day_gap_fields or day_revenue is None or day_revenue == 0:
            row["cost_known_revenue_share"] = None
        else:
            row["cost_known_revenue_share"] = min(
                (known_cost_revenue / day_revenue).quantize(Decimal("0.0001")), Decimal("1")
            )
        product_day_unknown_fields[key] = set(row.get("_unallocated_product_fields", set()))
        product_day_unknown_fields[key].update(row.get("_unscoped_uncertain_fields", set()))
        row.pop("_uncertain_fields", None)
        classification_complete = row.pop("_classification_complete", True)
        sku_allocation_complete = row.pop("_sku_allocation_complete", True)
        row.pop("_unallocated_product_fields", None)
        row.pop("_unscoped_uncertain_fields", None)
        reconciliation_complete = row.get("reconciliation_difference") is not None
        reconciliation_matches = reconciliation_complete and row.get("reconciliation_difference") == 0
        row["accrual_classification_complete"] = bool(classification_complete)
        row["sku_allocation_complete"] = bool(sku_allocation_complete)
        row["profitability_complete"] = bool(
            classification_complete
            and reconciliation_matches
            and quantity_complete
            and quantity_sku_complete
            and not return_blocked
            and row.get("cogs") is not None
            and row.get("compensation") == 0
        )
        if row.get("compensation") not in (None, Decimal("0")):
            row["profitability_complete"] = False
            warnings.append(f"{day}: налоговая база для компенсаций не подтверждена; прибыль не рассчитана")
        daily_quality[key] = {
            "accrual_classification_complete": bool(classification_complete),
            "sku_allocation_complete": bool(sku_allocation_complete),
            "reconciliation_matches": bool(reconciliation_matches),
            "profitability_complete": bool(row["profitability_complete"]),
            "unknown_sku_posting": bool(row.pop("_unknown_sku_posting", False)),
            "unscoped_return_activity": bool(unscoped_return_activity),
        }
        row["source_categories"] = list(dict.fromkeys(row["source_categories"]))
        row["source_accrual_ids"] = list(dict.fromkeys(row["source_accrual_ids"]))
        daily_rows.append(row)

    products_grouped: dict[str, dict[str, Any]] = {}
    for (day, sku, currency), row in sorted(products_by_key.items()):
        _resolve_gap_fields(row)
        product_quantity_complete = row.pop("_quantity_complete", True)
        product_return_activity = row.pop("_return_activity", False)
        row.pop("_has_sale_activity", None)
        row["tax"] = None
        row["operation_expenses"] = None
        row["margin"] = None
        row["reconciliation_difference"] = None
        uncertain = set(row.pop("_uncertain_fields", set()))
        uncertain.update(product_day_unknown_fields.get((day, currency), set()))
        classification_complete = row.pop("_classification_complete", True)
        sku_allocation_complete = row.pop("_sku_allocation_complete", True)
        row.pop("_unallocated_product_fields", None)
        quality = daily_quality.get((day, currency), {})
        product_return_activity = product_return_activity or quality.get("unscoped_return_activity", False)
        # A return signal blocks profit only when no return posting quantity explains it; COGS was decided above.
        product_returned = row.get("returned_quantity")
        product_return_blocked = product_return_activity and not (product_returned is not None and product_returned > 0)
        unknown_sku_posting = quality.get("unknown_sku_posting", False)
        # A product's profit depends on its own postings and on the reconciliation of its own accruals.
        # Another SKU's missing detail or a mismatched accrual on the same day must not hide this margin.
        product_source_accruals = row["source_accrual_ids"]
        product_reconciled = bool(product_source_accruals) and all(
            accrual_reconciled.get(accrual_id, False) for accrual_id in product_source_accruals
        )
        sku_allocation_complete = sku_allocation_complete and quality.get("sku_allocation_complete", False)
        if unknown_sku_posting:
            row["sold_quantity"] = None
            row["returned_quantity"] = None
            row["net_quantity"] = None
            row["cogs"] = None
        row["marketplace_net"] = (
            row["revenue"]
            - row["commission"]
            - row["logistics"]
            - row["acquiring"]
            - row["penalties"]
            - row["storage"]
            - row["ads_spend"]
            - row["other_expenses"]
            + row["compensation"]
            if (
                sku_allocation_complete
                and classification_complete
                and product_reconciled
                and not uncertain
            )
            else None
        )
        for field in uncertain:
            if field in row:
                row[field] = None
        row["accrual_classification_complete"] = bool(classification_complete)
        row["sku_allocation_complete"] = bool(sku_allocation_complete)
        row["profitability_complete"] = bool(
            classification_complete
            and sku_allocation_complete
            and product_reconciled
            and product_quantity_complete
            and not product_return_blocked
            and not unknown_sku_posting
            and row.get("marketplace_net") is not None
            and row.get("cogs") is not None
            and row.get("compensation") == 0
        )
        row["source_accrual_ids"] = list(dict.fromkeys(row["source_accrual_ids"]))
        grouped = products_grouped.setdefault(sku, {"sku": sku, "daily": []})
        grouped["daily"].append(row)

    # Internal quality markers should not leak into the persistence/UI contract.
    for row in daily_rows:
        row.pop("_uncertain_fields", None)
    return {
        "daily": daily_rows,
        "products": list(products_grouped.values()),
        "warnings": list(dict.fromkeys(warnings)),
    }


def _type_names(accrual_types: list[dict[str, Any]]) -> dict[str, dict[str, str | None]]:
    """Keep the source dictionary for warnings; no unverified aliases are guessed."""
    result: dict[str, dict[str, str | None]] = {}
    for item in accrual_types:
        if not isinstance(item, dict) or item.get("id") is None:
            raise OzonContractError("Справочник типов начислений Ozon содержит ошибку")
        key = str(item["id"])
        if key in result:
            raise OzonContractError("В справочнике типов начислений Ozon повторяется ID")
        result[key] = {
            "name": _bounded_text(item.get("name")),
            "description": _bounded_text(item.get("description")),
        }
    return result


def _deduplicate(accruals: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    by_id: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for accrual in accruals:
        if not isinstance(accrual, dict):
            raise OzonContractError("Начисление Ozon имеет неверный формат")
        raw_id = accrual.get("accrual_id")
        if raw_id is None or (isinstance(raw_id, str) and not raw_id.strip()):
            raise OzonContractError("В начислении Ozon отсутствует accrual_id")
        accrual_id = str(raw_id)
        prior = by_id.get(accrual_id)
        if prior is not None:
            if prior != accrual:
                raise OzonContractError("Ozon вернул разные записи с одним accrual_id")
            warnings.append("Повторная идентичная запись начисления учтена один раз")
            continue
        by_id[accrual_id] = accrual
    return list(by_id.values()), warnings


def _parse_money(value: Any, field: str) -> tuple[Decimal, str]:
    if not isinstance(value, dict):
        raise OzonContractError(f"В поле {field} отсутствует денежная сумма")
    amount = value.get("amount")
    currency = value.get("currency")
    if not isinstance(amount, str) or not isinstance(currency, str) or not currency.strip():
        raise OzonContractError(f"Поле {field} должно содержать amount и currency")
    try:
        parsed = Decimal(amount)
    except (InvalidOperation, ValueError) as exc:
        raise OzonContractError(f"В поле {field} неверная денежная сумма") from exc
    if not parsed.is_finite():
        raise OzonContractError(f"В поле {field} нечисловая денежная сумма")
    return parsed, currency.strip().upper()


def _accrual_day(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or not _ISO_DAY_RE.fullmatch(value):
        raise OzonContractError("Дата начисления должна иметь формат YYYY-MM-DD")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise OzonContractError("У начисления Ozon указана несуществующая дата") from exc


def _empty_daily(day: str, currency: str) -> dict[str, Any]:
    result: dict[str, Any] = {field: Decimal("0") for field in _AMOUNT_FIELDS}
    result.update(
        {
            "sold_quantity": Decimal("0"),
            "returned_quantity": Decimal("0"),
            "net_quantity": Decimal("0"),
            "date": day,
            "currency": currency,
            "source_accrual_ids": [],
            "source_categories": [],
            "_uncertain_fields": set(),
            # Known contributions per field and fields with a missing posting detail; see _mark_component_gap.
            "_contributions": {},
            "_gap_fields": set(),
            "_quantity_complete": True,
            "_quantity_sku_complete": True,
            "_unknown_sku_posting": False,
            "_return_activity": False,
            "_classification_complete": True,
            "_sku_allocation_complete": True,
            "_unallocated_product_fields": set(),
            "_unscoped_uncertain_fields": set(),
        }
    )
    return result


def _empty_product(day: str, sku: str, currency: str) -> dict[str, Any]:
    result: dict[str, Any] = {field: Decimal("0") for field in _AMOUNT_FIELDS}
    result.update(
        {
            "sold_quantity": Decimal("0"),
            "returned_quantity": Decimal("0"),
            "net_quantity": Decimal("0"),
            "date": day,
            "sku": sku,
            "currency": currency,
            "source_accrual_ids": [],
            "_uncertain_fields": set(),
            "_contributions": {},
            "_gap_fields": set(),
            "_has_sale_activity": False,
            "_quantity_complete": True,
            "_return_activity": False,
            "_classification_complete": True,
            "_sku_allocation_complete": True,
        }
    )
    return result


def _product(
    products: dict[tuple[str, str, str], dict[str, Any]],
    *,
    day: str,
    sku: str | None,
    currency: str,
    accrual_id: str,
) -> dict[str, Any] | None:
    if sku is None:
        return None
    row = products.setdefault((day, sku, currency), _empty_product(day, sku, currency))
    row["source_accrual_ids"].append(accrual_id)
    return row


def _sum(row: dict[str, Any], field: str, amount: Decimal) -> None:
    if row.get(field) is not None:
        row[field] += amount


def _mark_unknown(
    daily: dict[str, Any],
    products: dict[tuple[str, str, str], dict[str, Any]],
    field: str,
    sku: str | None,
    *,
    day: str,
    currency: str,
    accrual_id: str,
) -> None:
    daily["_classification_complete"] = False
    if sku is None:
        daily["_sku_allocation_complete"] = False
    daily[field] = None
    daily["_uncertain_fields"].add(field)
    if sku is not None:
        product = _product(
            products,
            day=day,
            sku=sku,
            currency=currency,
            accrual_id=accrual_id,
        )
        assert product is not None
        product["_classification_complete"] = False
        product[field] = None
        product["_uncertain_fields"].add(field)
    else:
        daily["_unscoped_uncertain_fields"].add(field)
        for key, product in products.items():
            if key[0] == day and key[2] == currency:
                product["_classification_complete"] = False
                product[field] = None
                product["_uncertain_fields"].add(field)


def _mark_component_gap(
    daily: dict[str, Any],
    products: dict[tuple[str, str, str], dict[str, Any]],
    field: str,
    sku: str | None,
    *,
    day: str,
    currency: str,
    accrual_id: str,
) -> None:
    """One posting detail is missing: keep the sum of the components that are known.

    Unlike _mark_unknown, the day and product amounts are not nulled. The missing detail
    only invalidates classification (so profit stays unknown) for the affected SKU, or for
    the whole day when the posting has no SKU identity.
    """
    daily["_classification_complete"] = False
    daily["_gap_fields"].add(field)
    if sku is None:
        daily["_sku_allocation_complete"] = False
        for key, product in products.items():
            if key[0] == day and key[2] == currency:
                product["_classification_complete"] = False
    else:
        product = _product(products, day=day, sku=sku, currency=currency, accrual_id=accrual_id)
        assert product is not None
        product["_classification_complete"] = False
        product["_gap_fields"].add(field)


def _metric_amount(field: str, source_amount: Decimal) -> Decimal:
    # WB Finance reports expense columns as positive numbers; a source reversal
    # therefore appears as a negative expense after this sign-preserving flip.
    return -source_amount if field in _SOURCE_EXPENSE_FIELDS else source_amount


def _valid_cost(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        cost = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not cost.is_finite() or cost < 0:
        return None
    return cost


def _parse_quantity(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not quantity.is_finite() or quantity <= 0:
        return None
    return quantity


def _mark_quantity_unknown(
    daily: dict[str, Any],
    products: dict[tuple[str, str, str], dict[str, Any]],
    sku: str | None,
    *,
    day: str,
    currency: str,
    accrual_id: str,
) -> None:
    daily["_quantity_complete"] = False
    daily["_quantity_sku_complete"] = False
    daily["sold_quantity"] = None
    daily["returned_quantity"] = None
    daily["net_quantity"] = None
    if sku is None:
        for key, product in products.items():
            if key[0] == day and key[2] == currency:
                product["_quantity_complete"] = False
                product["sold_quantity"] = None
                product["returned_quantity"] = None
                product["net_quantity"] = None
        return
    product = _product(
        products,
        day=day,
        sku=sku,
        currency=currency,
        accrual_id=accrual_id,
    )
    assert product is not None
    product["_quantity_complete"] = False
    product["sold_quantity"] = None
    product["returned_quantity"] = None
    product["net_quantity"] = None


def _add_component(
    *,
    field: str,
    source_amount: Decimal,
    sku: str | None,
    daily: dict[str, Any],
    products: dict[tuple[str, str, str], dict[str, Any]],
    day: str,
    currency: str,
    accrual_id: str,
) -> None:
    if sku is None:
        daily["_sku_allocation_complete"] = False
        daily["_unallocated_product_fields"].add(field)
        for key, existing in products.items():
            if key[0] == day and key[2] == currency:
                existing[field] = None
                existing["_uncertain_fields"].add(field)
    metric_amount = _metric_amount(field, source_amount)
    _sum(daily, field, metric_amount)
    _record_contribution(daily, field)
    product = _product(
        products,
        day=day,
        sku=sku,
        currency=currency,
        accrual_id=accrual_id,
    )
    if product is None:
        # This column is a signed control sum of detail rows without a SKU,
        # not another expense category displayed with the WB expense sign.
        _sum(daily, "unallocated_amount", source_amount)
    else:
        _sum(product, field, metric_amount)
        _record_contribution(product, field)


def _record_contribution(row: dict[str, Any], field: str) -> None:
    contributions = row.setdefault("_contributions", {})
    contributions[field] = contributions.get(field, 0) + 1


def _resolve_gap_fields(row: dict[str, Any]) -> set[str]:
    """A field with a missing posting detail keeps the sum of known details; with none known it is unknown.

    Returns the fields that had a gap, so callers can tell when a total is only a partial sum.
    """
    contributions = row.pop("_contributions", {})
    gap_fields = set(row.pop("_gap_fields", set()))
    for field in gap_fields:
        if not contributions.get(field):
            row[field] = None
    return gap_fields


def _mark_return_activity(
    *,
    daily: dict[str, Any],
    products: dict[tuple[str, str, str], dict[str, Any]],
    sku: str | None,
    day: str,
    currency: str,
    accrual_id: str,
) -> None:
    daily["_return_activity"] = True
    if sku is None:
        # A return-like fee without a SKU may affect any product's COGS.
        daily["_unscoped_return_activity"] = True
        daily["_quantity_sku_complete"] = False
        return
    product = _product(
        products,
        day=day,
        sku=sku,
        currency=currency,
        accrual_id=accrual_id,
    )
    if product is not None:
        product["_return_activity"] = True


def _mark_return_fee_type(
    *,
    type_id: Any,
    type_names: dict[str, dict[str, str | None]],
    daily: dict[str, Any],
    products: dict[tuple[str, str, str], dict[str, Any]],
    sku: str | None,
    day: str,
    currency: str,
    accrual_id: str,
    warnings: list[str],
) -> None:
    key = (str(type_id) if type_id is not None else "unknown")[:64]
    name = type_names.get(key, {}).get("name") or "без названия"
    if (key, name) not in _RETURN_RELATED_TYPE_ID_NAMES:
        return
    _mark_return_activity(
        daily=daily,
        products=products,
        sku=sku,
        day=day,
        currency=currency,
        accrual_id=accrual_id,
    )
    warnings.append(
        f"{day}: начисление содержит тип логистики возврата ({name}); "
        "количество, себестоимость возврата и прибыль не подтверждены"
    )


def _fee_details(
    *,
    type_id: Any,
    source_amount: Decimal,
    type_names: dict[str, dict[str, str | None]],
    daily: dict[str, Any],
    products: dict[tuple[str, str, str], dict[str, Any]],
    sku: str | None,
    day: str,
    currency: str,
    accrual_id: str,
    warnings: list[str],
) -> Decimal:
    key = (str(type_id) if type_id is not None else "unknown")[:64]
    type_info = type_names.get(key, {})
    name = type_info.get("name") or "без названия"
    description = type_info.get("description") or "без описания"
    exact_key = (key, name)
    category = _TYPE_CATEGORY_BY_ID_NAME.get(exact_key)
    if category is not None:
        _add_component(
            field=category,
            source_amount=source_amount,
            sku=sku,
            daily=daily,
            products=products,
            day=day,
            currency=currency,
            accrual_id=accrual_id,
        )
        return source_amount

    daily["_classification_complete"] = False
    product = _product(
        products,
        day=day,
        sku=sku,
        currency=currency,
        accrual_id=accrual_id,
    )
    if exact_key in _AMBIGUOUS_TYPE_NAMES:
        if sku is None:
            daily["_sku_allocation_complete"] = False
        _sum(daily, "unallocated_amount", source_amount)
        if product is not None:
            _sum(product, "unallocated_amount", source_amount)
        warnings.append(
            f"{day}: неоднозначный тип начисления {key} ({name}; {description}); "
            "исходная сумма сохранена без отнесения к доходам или расходам"
        )
        return source_amount

    # Unknown dictionary entries remain visible as signed "other" items, but
    # their actual expense type is not guessed. Possible affected categories are
    # set to unknown and the quality marker prevents a profit claim.
    _add_component(
        field="other_expenses",
        source_amount=source_amount,
        sku=sku,
        daily=daily,
        products=products,
        day=day,
        currency=currency,
        accrual_id=accrual_id,
    )
    for field in _POSSIBLE_FEE_FIELDS:
        _mark_unknown(
            daily,
            products,
            field,
            sku,
            day=day,
            currency=currency,
            accrual_id=accrual_id,
        )
    warnings.append(
        f"{day}: неизвестный тип начисления {key} ({name}; {description}); "
        "сумма видна в прочих расходах, но разложение по статьям и прибыль не подтверждены"
    )
    return source_amount


def _mark_return_delivery_service(
    *,
    service: dict[str, Any],
    type_names: dict[str, dict[str, str | None]],
    daily: dict[str, Any],
    products: dict[tuple[str, str, str], dict[str, Any]],
    sku: str | None,
    day: str,
    currency: str,
    accrual_id: str,
    warnings: list[str],
) -> None:
    type_id = str(service.get("type_id"))[:64] if service.get("type_id") is not None else "unknown"
    name = type_names.get(type_id, {}).get("name") or "без названия"
    if (type_id, name) not in _RETURN_RELATED_TYPE_ID_NAMES:
        return
    _mark_return_activity(
        daily=daily,
        products=products,
        sku=sku,
        day=day,
        currency=currency,
        accrual_id=accrual_id,
    )
    warnings.append(
        f"{day}: начисление содержит услугу логистики возврата ({name}); "
        "количество, себестоимость возврата и прибыль не подтверждены"
    )


def _normalize_accrual(
    *,
    accrual: dict[str, Any],
    category: str,
    authoritative_total: Decimal,
    day: str,
    currency: str,
    accrual_id: str,
    type_names: dict[str, dict[str, str | None]],
    daily: dict[str, Any],
    products: dict[tuple[str, str, str], dict[str, Any]],
    warnings: list[str],
) -> tuple[Decimal, bool]:
    raw_total = Decimal("0")
    complete = True

    def parse_component(value: Any, field: str, sku: str | None, source: str) -> Decimal | None:
        nonlocal complete, raw_total
        try:
            amount, component_currency = _parse_money(value, source)
        except OzonContractError:
            complete = False
            _mark_component_gap(
                daily,
                products,
                field,
                sku,
                day=day,
                currency=currency,
                accrual_id=accrual_id,
            )
            if field == "revenue":
                _mark_unknown(
                    daily,
                    products,
                    "cogs",
                    sku,
                    day=day,
                    currency=currency,
                    accrual_id=accrual_id,
                )
            warnings.append(f"{day}: в начислении {accrual_id} отсутствует или повреждена сумма {source}")
            return None
        if component_currency != currency:
            raise OzonContractError("В одном начислении Ozon обнаружены разные валюты")
        raw_total += amount
        _add_component(
            field=field,
            source_amount=amount,
            sku=sku,
            daily=daily,
            products=products,
            day=day,
            currency=currency,
            accrual_id=accrual_id,
        )
        return amount

    def parse_fee(value: Any, type_id: Any, sku: str | None, source: str) -> None:
        nonlocal complete, raw_total
        # Record a return signal before parsing its amount: a missing or bad
        # amount must not let a return silently keep normal sale COGS.
        _mark_return_fee_type(
            type_id=type_id,
            type_names=type_names,
            daily=daily,
            products=products,
            sku=sku,
            day=day,
            currency=currency,
            accrual_id=accrual_id,
            warnings=warnings,
        )
        try:
            amount, component_currency = _parse_money(value, source)
        except OzonContractError:
            complete = False
            _mark_unknown(
                daily,
                products,
                "other_expenses",
                sku,
                day=day,
                currency=currency,
                accrual_id=accrual_id,
            )
            for possible_field in _POSSIBLE_FEE_FIELDS:
                _mark_unknown(
                    daily,
                    products,
                    possible_field,
                    sku,
                    day=day,
                    currency=currency,
                    accrual_id=accrual_id,
                )
            warnings.append(f"{day}: в начислении {accrual_id} отсутствует или повреждена сумма {source}")
            return
        if component_currency != currency:
            raise OzonContractError("В одном начислении Ozon обнаружены разные валюты")
        raw_total += _fee_details(
            type_id=type_id,
            source_amount=amount,
            type_names=type_names,
            daily=daily,
            products=products,
            sku=sku,
            day=day,
            currency=currency,
            accrual_id=accrual_id,
            warnings=warnings,
        )

    if category == "POSTING":
        posting = accrual.get("posting")
        posting_products = posting.get("products") if isinstance(posting, dict) else None
        if not isinstance(posting_products, list):
            for field in ("revenue", "commission", "logistics", "cogs"):
                _mark_unknown(daily, products, field, None, day=day, currency=currency, accrual_id=accrual_id)
            _mark_quantity_unknown(daily, products, None, day=day, currency=currency, accrual_id=accrual_id)
            warnings.append(f"{day}: в начислении {accrual_id} отсутствуют товары posting")
            return raw_total, False

        for detail in posting_products:
            if not isinstance(detail, dict):
                complete = False
                warnings.append(f"{day}: начисление {accrual_id} содержит повреждённую строку товара")
                for field in ("revenue", "commission", "logistics", "cogs"):
                    _mark_unknown(daily, products, field, None, day=day, currency=currency, accrual_id=accrual_id)
                _mark_quantity_unknown(daily, products, None, day=day, currency=currency, accrual_id=accrual_id)
                continue
            sku = _clean_sku(detail.get("sku"))
            if sku is None:
                # A posting without product identity may be any of the known
                # SKU rows. Keep store quantities, but invalidate SKU quantities.
                daily["_unknown_sku_posting"] = True
            commission = detail.get("commission")
            if not isinstance(commission, dict):
                complete = False
                # Revenue and commission of the other postings stay known; only this posting's share is missing.
                for field in ("revenue", "commission"):
                    _mark_component_gap(daily, products, field, sku, day=day, currency=currency, accrual_id=accrual_id)
                # Units sold are stated only inside this block, so COGS for the SKU cannot be confirmed.
                _mark_unknown(daily, products, "cogs", sku, day=day, currency=currency, accrual_id=accrual_id)
                _mark_quantity_unknown(daily, products, sku, day=day, currency=currency, accrual_id=accrual_id)
                warnings.append(f"{day}: в начислении {accrual_id} отсутствует блок commission товара")
            else:
                sale = parse_component(commission.get("sale_amount"), "revenue", sku, "sale_amount")
                if sale is not None:
                    daily["_has_sale_activity"] = True
                    product = _product(
                        products,
                        day=day,
                        sku=sku,
                        currency=currency,
                        accrual_id=accrual_id,
                    )
                    if product is not None:
                        product["_has_sale_activity"] = True
                    quantity = _parse_quantity(detail.get("quantity"))
                    if sale > 0 and quantity is not None:
                        _sum(daily, "sold_quantity", quantity)
                        if sku is None:
                            daily["_quantity_sku_complete"] = False
                            daily["_sku_allocation_complete"] = False
                        elif product is not None:
                            _sum(product, "sold_quantity", quantity)
                    elif sale <= 0 and quantity is not None:
                        # Owner decision (2026-10-25): a return posting's units go back to the warehouse and are
                        # resold, so the explicit posting quantity nets out of COGS for this SKU and day.
                        _sum(daily, "returned_quantity", quantity)
                        if sku is None:
                            daily["_quantity_sku_complete"] = False
                            daily["_sku_allocation_complete"] = False
                        elif product is not None:
                            _sum(product, "returned_quantity", quantity)
                    else:
                        _mark_quantity_unknown(
                            daily,
                            products,
                            sku,
                            day=day,
                            currency=currency,
                            accrual_id=accrual_id,
                        )
                        warnings.append(
                            f"{day}: в начислении {accrual_id} нет подтверждённого количества товара"
                        )
                else:
                    _mark_unknown(
                        daily,
                        products,
                        "cogs",
                        sku,
                        day=day,
                        currency=currency,
                        accrual_id=accrual_id,
                    )
                    _mark_quantity_unknown(
                        daily,
                        products,
                        sku,
                        day=day,
                        currency=currency,
                        accrual_id=accrual_id,
                    )
                # commission is the final amount. sale_commission is its tariff
                # diagnostic, and bonus/coinvestment are not extra ledger lines.
                parse_component(commission.get("commission"), "commission", sku, "commission")

            delivery = detail.get("delivery")
            if not isinstance(delivery, dict):
                complete = False
                _mark_component_gap(daily, products, "logistics", sku, day=day, currency=currency, accrual_id=accrual_id)
                warnings.append(f"{day}: в начислении {accrual_id} отсутствует блок delivery товара")
                continue
            delivery_total = delivery.get("total_accrued")
            services = delivery.get("services")
            if isinstance(services, list):
                # Scan all service identities before parsing any amounts. A
                # malformed earlier service must not hide a later return.
                for service in services:
                    if isinstance(service, dict):
                        _mark_return_delivery_service(
                            service=service,
                            type_names=type_names,
                            daily=daily,
                            products=products,
                            sku=sku,
                            day=day,
                            currency=currency,
                            accrual_id=accrual_id,
                            warnings=warnings,
                        )
            if delivery_total is not None:
                amount = parse_component(delivery_total, "logistics", sku, "delivery.total_accrued")
                if amount is not None and isinstance(services, list):
                    service_sum = Decimal("0")
                    services_valid = True
                    for service in services:
                        if not isinstance(service, dict):
                            services_valid = False
                            break
                        try:
                            service_amount, service_currency = _parse_money(
                                service.get("accrued"), "delivery service accrued"
                            )
                        except OzonContractError:
                            services_valid = False
                            break
                        if service_currency != currency:
                            raise OzonContractError("В одном начислении Ozon обнаружены разные валюты")
                        service_sum += service_amount
                    if services_valid and service_sum != amount:
                        warnings.append(
                            f"{day}: услуги доставки в начислении {accrual_id} не совпадают с total_accrued; "
                            "в учёте использован total_accrued один раз"
                        )
            elif isinstance(services, list):
                for service in services:
                    if not isinstance(service, dict):
                        complete = False
                        _mark_unknown(daily, products, "logistics", sku, day=day, currency=currency, accrual_id=accrual_id)
                        warnings.append(f"{day}: в начислении {accrual_id} повреждена услуга доставки")
                        continue
                    # This field is already scoped to delivery; when the
                    # subtotal is absent, its service rows form the logistics
                    # total. Do not classify it from an unverified type name.
                    parse_component(service.get("accrued"), "logistics", sku, "delivery service accrued")
            else:
                complete = False
                _mark_unknown(daily, products, "logistics", sku, day=day, currency=currency, accrual_id=accrual_id)
                warnings.append(f"{day}: в начислении {accrual_id} нет суммы или списка услуг доставки")

    elif category == "ITEM":
        item_fees = accrual.get("item_fees")
        fee_groups = item_fees.get("fees") if isinstance(item_fees, dict) else None
        if not isinstance(fee_groups, list):
            for field in ("other_expenses", *_POSSIBLE_FEE_FIELDS):
                _mark_unknown(daily, products, field, None, day=day, currency=currency, accrual_id=accrual_id)
            warnings.append(f"{day}: в начислении {accrual_id} отсутствует список item_fees")
            return raw_total, False
        for group in fee_groups:
            if not isinstance(group, dict) or not isinstance(group.get("fees"), list):
                complete = False
                sku = _clean_sku(group.get("sku")) if isinstance(group, dict) else None
                for field in ("other_expenses", *_POSSIBLE_FEE_FIELDS):
                    _mark_unknown(daily, products, field, sku, day=day, currency=currency, accrual_id=accrual_id)
                warnings.append(f"{day}: в начислении {accrual_id} повреждена группа item_fees")
                continue
            sku = _clean_sku(group.get("sku"))
            for fee in group["fees"]:
                if not isinstance(fee, dict):
                    complete = False
                    for field in ("other_expenses", *_POSSIBLE_FEE_FIELDS):
                        _mark_unknown(daily, products, field, sku, day=day, currency=currency, accrual_id=accrual_id)
                    warnings.append(f"{day}: в начислении {accrual_id} повреждена строка item_fees")
                    continue
                parse_fee(fee.get("accrued"), fee.get("type_id"), sku, "item_fees accrued")

    elif category == "NON_ITEM":
        fee = accrual.get("non_item_fee")
        if not isinstance(fee, dict):
            for field in ("other_expenses", *_POSSIBLE_FEE_FIELDS):
                _mark_unknown(daily, products, field, None, day=day, currency=currency, accrual_id=accrual_id)
            warnings.append(f"{day}: в начислении {accrual_id} отсутствует non_item_fee")
            return raw_total, False
        parse_fee(fee.get("accrued"), fee.get("type_id"), None, "non_item_fee accrued")

    elif category == "CONTAINER_FEES":
        container_fees = accrual.get("container_fees")
        fees = container_fees.get("fees") if isinstance(container_fees, dict) else None
        if not isinstance(fees, list):
            for field in ("other_expenses", *_POSSIBLE_FEE_FIELDS):
                _mark_unknown(daily, products, field, None, day=day, currency=currency, accrual_id=accrual_id)
            warnings.append(f"{day}: в начислении {accrual_id} отсутствует список container_fees")
            return raw_total, False
        for fee in fees:
            if not isinstance(fee, dict):
                complete = False
                for field in ("other_expenses", *_POSSIBLE_FEE_FIELDS):
                    _mark_unknown(daily, products, field, None, day=day, currency=currency, accrual_id=accrual_id)
                warnings.append(f"{day}: в начислении {accrual_id} повреждена строка container_fees")
                continue
            parse_fee(fee.get("accrued"), fee.get("type_id"), None, "container_fees accrued")

    else:
        # Unknown categories have no safe component interpretation. Preserve
        # their authoritative signed total in the unallocated bucket.
        _sum(daily, "unallocated_amount", authoritative_total)
        for field in ("revenue", "other_expenses", *_POSSIBLE_FEE_FIELDS):
            _mark_unknown(daily, products, field, None, day=day, currency=currency, accrual_id=accrual_id)
        warnings.append(
            f"{day}: неизвестная категория начисления {category} (ID {accrual_id}); "
            "итог оставлен без распределения"
        )
        return authoritative_total, False

    return raw_total, complete


def _accrual_category(value: Any) -> str:
    return value.strip().upper() if isinstance(value, str) and value.strip() else "UNSPECIFIED"


def _clean_sku(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _bounded_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip()[:160] or None
