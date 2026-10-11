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
    "penalties",
    "storage",
    "ads_spend",
    "compensation",
)
_SOURCE_EXPENSE_FIELDS = {
    "commission",
    "logistics",
    "penalties",
    "storage",
    "ads_spend",
    "other_expenses",
    "operation_expenses",
}
_ISO_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_accruals(
    accruals: list[dict[str, Any]],
    accrual_types: list[dict[str, Any]],
    costs: dict[str, Decimal | None] | None = None,
) -> dict[str, Any]:
    """Return daily values, SKU values grouped by day, and data-quality warnings.

    ``costs`` is accepted for caller compatibility, but the documented accrual
    fields do not supply a verified sold quantity. The normalizer will not
    multiply costs by a quantity inferred from money or another field.
    """
    del costs
    if not isinstance(accruals, list):
        raise OzonContractError("Список начислений Ozon имеет неверный формат")
    if not isinstance(accrual_types, list):
        raise OzonContractError("Справочник типов начислений Ozon имеет неверный формат")

    type_names = _type_names(accrual_types)
    unique, warnings = _deduplicate(accruals)
    daily_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    products_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    seen_currencies: set[str] = set()

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
            if difference != 0:
                warnings.append(
                    f"{day}: начисление {accrual_id} не сверяется: total_amount отличается "
                    f"от суммы деталей на {difference} {currency}"
                )
        else:
            row["reconciliation_difference"] = None
            row["_reconciliation_incomplete"] = True

    if len(seen_currencies) > 1:
        raise OzonContractError("Начисления Ozon в разных валютах нельзя объединить")

    daily_rows: list[dict[str, Any]] = []
    for key in sorted(daily_by_key):
        row = daily_by_key[key]
        day = row["date"]
        if row.pop("_reconciliation_incomplete", False):
            row["reconciliation_difference"] = None
            warnings.append(f"{day}: сверка недоступна, так как детали начислений неполные")
        if row.pop("_has_sale_activity", False) or row.get("revenue") is None:
            row["cogs"] = None
            warnings.append(f"{day}: себестоимость и прибыль неизвестны без подтверждённого количества продаж")
        # No verified fee-type mapping or ads endpoint is part of this feed, so
        # the named ad-spend category remains unknown instead of displaying 0.
        # Taxes and general operating expenses are also outside this feed.
        row["ads_spend"] = None
        # Bonus and co-investment are present as commission diagnostics, but
        # they are not additive lines and have no verified standalone mapping.
        row["compensation"] = None
        row["tax"] = None
        row["operation_expenses"] = None
        row["margin"] = None
        uncertain_fields = row.pop("_uncertain_fields", set())
        row["source_categories"] = list(dict.fromkeys(row["source_categories"]))
        row["source_accrual_ids"] = list(dict.fromkeys(row["source_accrual_ids"]))
        for product_key, product in products_by_key.items():
            if product_key[0] == key[0] and product_key[2] == key[1]:
                product["_day_uncertain_fields"] = set(uncertain_fields)
        daily_rows.append(row)

    products_grouped: dict[str, dict[str, Any]] = {}
    for (day, sku, currency), row in sorted(products_by_key.items()):
        sale_activity = row.pop("_has_sale_activity", False)
        row["cogs"] = None if sale_activity or row.get("revenue") is None else Decimal("0")
        row["tax"] = None
        row["operation_expenses"] = None
        row["ads_spend"] = None
        row["compensation"] = None
        row["margin"] = None
        row["marketplace_net"] = None
        row["unallocated_amount"] = None
        row["reconciliation_difference"] = None
        uncertain = set(row.pop("_uncertain_fields", set()))
        uncertain.update(row.pop("_day_uncertain_fields", set()))
        for field in uncertain:
            if field in row:
                row[field] = None
        row.pop("_day_uncertain_fields", None)
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
            "date": day,
            "currency": currency,
            "source_accrual_ids": [],
            "source_categories": [],
            "_uncertain_fields": set(),
        }
    )
    return result


def _empty_product(day: str, sku: str, currency: str) -> dict[str, Any]:
    result: dict[str, Any] = {field: Decimal("0") for field in _AMOUNT_FIELDS}
    result.update(
        {
            "date": day,
            "sku": sku,
            "currency": currency,
            "source_accrual_ids": [],
            "_uncertain_fields": set(),
            "_has_sale_activity": False,
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
        product[field] = None
        product["_uncertain_fields"].add(field)
    else:
        for key, product in products.items():
            if key[0] == day and key[2] == currency:
                product[field] = None
                product["_uncertain_fields"].add(field)


def _metric_amount(field: str, source_amount: Decimal) -> Decimal:
    # WB Finance reports expense columns as positive numbers; a source reversal
    # therefore appears as a negative expense after this sign-preserving flip.
    return -source_amount if field in _SOURCE_EXPENSE_FIELDS else source_amount


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
    metric_amount = _metric_amount(field, source_amount)
    _sum(daily, field, metric_amount)
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
    # Ozon's live type dictionary has not yet been validated against a closed
    # seller statement, so no display category is inferred from translated or
    # machine-generated names. Keep every signed amount visible as other expense.
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
    key = (str(type_id) if type_id is not None else "unknown")[:64]
    type_info = type_names.get(key, {})
    name = type_info.get("name") or "без названия"
    description = type_info.get("description") or "без описания"
    warnings.append(
        f"{day}: неизвестный тип начисления {key} ({name}; {description}); "
        "сумма сохранена в прочих расходах, разложение по статьям не подтверждено"
    )
    return source_amount


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
            _mark_unknown(
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
            warnings.append(f"{day}: в начислении {accrual_id} отсутствуют товары posting")
            return raw_total, False

        for detail in posting_products:
            if not isinstance(detail, dict):
                complete = False
                warnings.append(f"{day}: начисление {accrual_id} содержит повреждённую строку товара")
                for field in ("revenue", "commission", "logistics", "cogs"):
                    _mark_unknown(daily, products, field, None, day=day, currency=currency, accrual_id=accrual_id)
                continue
            sku = _clean_sku(detail.get("sku"))
            commission = detail.get("commission")
            if not isinstance(commission, dict):
                complete = False
                for field in ("revenue", "commission", "cogs"):
                    _mark_unknown(daily, products, field, sku, day=day, currency=currency, accrual_id=accrual_id)
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
                # commission is the final amount. sale_commission is its tariff
                # diagnostic, and bonus/coinvestment are not extra ledger lines.
                parse_component(commission.get("commission"), "commission", sku, "commission")

            delivery = detail.get("delivery")
            if not isinstance(delivery, dict):
                complete = False
                _mark_unknown(daily, products, "logistics", sku, day=day, currency=currency, accrual_id=accrual_id)
                warnings.append(f"{day}: в начислении {accrual_id} отсутствует блок delivery товара")
                continue
            delivery_total = delivery.get("total_accrued")
            services = delivery.get("services")
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
