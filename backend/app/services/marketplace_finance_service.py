from __future__ import annotations

import hashlib
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.crypto import CryptoConfigError, decrypt_text, encrypt_text
from app.models.article import Article
from app.models.ozon import (
    OzonAccrualSnapshot,
    OzonConnection,
    OzonFinanceSettings,
    OzonOperationalExpense,
    OzonProduct,
    OzonProductCost,
    OzonSyncDay,
    OzonSyncRun,
)
from app.models.pnl_daily import PnlDaily
from app.models.sku_daily import SkuDaily
from app.models.user import User

logger = logging.getLogger(__name__)

DAILY_AMOUNT_FIELDS = (
    "revenue",
    "commission",
    "acquiring",
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
OZON_QUANTITY_FIELDS = ("sold_quantity", "returned_quantity", "net_quantity")
DAILY_SOURCE_QUALITY_FIELDS = (
    "accrual_classification_complete",
    "sku_allocation_complete",
    "profitability_complete",
    *OZON_QUANTITY_FIELDS,
    # Share (0..1) of the day's revenue whose SKU COGS is known; null when the day's revenue is unknown or zero.
    "cost_known_revenue_share",
)
ZERO_DAY_FIELDS = DAILY_AMOUNT_FIELDS
MAX_SYNC_DAYS = 31
# Why a run was queued; stored on the run so chaining never guesses from timing.
MANUAL_TRIGGER = "manual"
BACKFILL_TRIGGER = "backfill"
DAILY_TRIGGER = "daily"
SYNC_TRIGGERS = (MANUAL_TRIGGER, BACKFILL_TRIGGER, DAILY_TRIGGER)
# Beat refreshes the last three completed days for every ready connection.
DAILY_REFRESH_DAYS = 3
ACTIVE_RUN_STATUSES = ("queued", "running")
FINISHED_RUN_STATUSES = ("complete", "partial")
# Field names of the WB daily P&L row, in the order the shared Dashboard reads them.
WB_SHAPED_PNL_FIELDS = (
    "revenue",
    "commission",
    "logistics",
    "penalties",
    "storage",
    "ads_spend",
    "cogs",
    "tax",
    "operation_expenses",
    "margin",
)


class MarketplaceFinanceError(ValueError):
    pass


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _money(value: Any) -> float | None:
    amount = _decimal(value)
    if amount is None:
        return None
    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _share(value: Any) -> float | None:
    amount = _decimal(value)
    if amount is None:
        return None
    return float(amount.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _money_sum(values: list[Any]) -> float | None:
    parsed = [_decimal(value) for value in values]
    if any(value is None for value in parsed):
        return None
    return _money(sum((value for value in parsed if value is not None), Decimal("0")))


def _date_rows(value: Any, date_key: str = "date") -> list[dict[str, Any]]:
    """Accept the normalizer's date-keyed mapping or list without changing its values."""
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        rows: list[dict[str, Any]] = []
        for key, item in value.items():
            if isinstance(item, dict):
                row = dict(item)
                row.setdefault(date_key, str(key))
                rows.append(row)
        return rows
    return []


def _normalizer_daily_for_day(result: dict[str, Any], day: date) -> dict[str, Any]:
    rows = _date_rows(result.get("daily"))
    target = day.isoformat()
    for row in rows:
        if str(row.get("date") or row.get("day") or "")[:10] == target:
            return row
    if len(rows) == 1 and not (rows[0].get("date") or rows[0].get("day")):
        return rows[0]
    return {}


def _normalizer_products_for_day(result: dict[str, Any], day: date) -> list[dict[str, Any]]:
    raw = result.get("products")
    rows: list[dict[str, Any]] = []
    if isinstance(raw, list):
        rows = [dict(row) for row in raw if isinstance(row, dict)]
    elif isinstance(raw, dict):
        for sku, item in raw.items():
            if isinstance(item, dict):
                row = dict(item)
                row.setdefault("sku", str(sku))
                rows.append(row)

    target = day.isoformat()
    out: list[dict[str, Any]] = []
    for product in rows:
        sku = str(product.get("sku") or "").strip()
        if not sku:
            continue
        daily_rows = _date_rows(product.get("daily"))
        chosen = next(
            (
                row
                for row in daily_rows
                if str(row.get("date") or row.get("day") or "")[:10] == target
            ),
            None,
        )
        if chosen is None and len(daily_rows) == 1 and not (daily_rows[0].get("date") or daily_rows[0].get("day")):
            chosen = daily_rows[0]
        # Some normalizer implementations return a single-day product summary directly.
        if chosen is None and not daily_rows:
            chosen = {key: value for key, value in product.items() if key not in {"sku", "daily"}}
        if chosen is None:
            continue
        out.append({"sku": sku, **chosen})
    return out


def _serialize_daily(row: dict[str, Any], day: date) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        **{key: _json_safe(row.get(key)) for key in DAILY_AMOUNT_FIELDS},
        **{key: _json_safe(row.get(key)) for key in DAILY_SOURCE_QUALITY_FIELDS},
    }


def _client_credentials(db: Session, user_id: str) -> tuple[OzonConnection, str, str]:
    connection = db.query(OzonConnection).filter(OzonConnection.user_id == user_id).first()
    if connection is None:
        raise MarketplaceFinanceError("Ozon is not connected")
    try:
        client_id = decrypt_text(connection.client_id_enc)
        api_key = decrypt_text(connection.api_key_enc)
    except (CryptoConfigError, ValueError) as exc:
        raise MarketplaceFinanceError("Ozon connection cannot be decrypted; check server encryption configuration") from exc
    return connection, client_id, api_key


def save_connection(*, db: Session, user_id: str, client_id: str, api_key: str) -> OzonConnection:
    clean_client_id = (client_id or "").strip()
    clean_api_key = (api_key or "").strip()
    if not clean_client_id or not clean_api_key:
        raise MarketplaceFinanceError("client_id and api_key are required")
    lock = _acquire_owner_lock(db, user_id)
    if lock is None:
        raise MarketplaceFinanceError("Подключение нельзя менять во время синхронизации")
    try:
        return _save_connection_while_locked(
            db=db,
            user_id=user_id,
            client_id=clean_client_id,
            api_key=clean_api_key,
        )
    finally:
        _release_owner_lock(lock, user_id)


def _save_connection_while_locked(
    *, db: Session, user_id: str, client_id: str, api_key: str
) -> OzonConnection:
    row = db.query(OzonConnection).filter(OzonConnection.user_id == user_id).first()
    active = (
        db.query(OzonSyncRun)
        .filter(OzonSyncRun.user_id == user_id, OzonSyncRun.status.in_(("queued", "running")))
        .first()
    )
    if active is not None:
        if not _run_is_stale(active):
            raise MarketplaceFinanceError("Подключение нельзя менять во время синхронизации")
        _mark_stale_run_failed(db, active)
    if row is not None:
        try:
            current_client_id = decrypt_text(row.client_id_enc)
        except (CryptoConfigError, ValueError) as exc:
            raise MarketplaceFinanceError("Текущее подключение не удаётся расшифровать; проверьте настройки шифрования") from exc
        if current_client_id != client_id:
            has_ledger = (
                db.query(OzonAccrualSnapshot.id)
                .filter(OzonAccrualSnapshot.user_id == user_id)
                .first()
                is not None
            ) or (
                db.query(OzonSyncDay.id)
                .filter(OzonSyncDay.user_id == user_id, OzonSyncDay.status.in_(("complete", "empty")))
                .first()
                is not None
            ) or (
                db.query(OzonProduct.id).filter(OzonProduct.user_id == user_id).first() is not None
            ) or (
                db.query(OzonProductCost.id).filter(OzonProductCost.user_id == user_id).first() is not None
            )
            if has_ledger:
                raise MarketplaceFinanceError("Client-Id нельзя менять, пока сохранена история этого магазина")
    try:
        encrypted_client_id = encrypt_text(client_id)
        encrypted_api_key = encrypt_text(api_key)
    except CryptoConfigError as exc:
        raise MarketplaceFinanceError("Server encryption is not configured") from exc

    if row is None:
        row = OzonConnection(
            user_id=user_id,
            client_id_enc=encrypted_client_id,
            api_key_enc=encrypted_api_key,
            status="configured",
        )
    else:
        row.client_id_enc = encrypted_client_id
        row.api_key_enc = encrypted_api_key
        row.status = "configured"
        row.last_error = None
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _latest_run(db: Session, user_id: str) -> OzonSyncRun | None:
    return (
        db.query(OzonSyncRun)
        .filter(OzonSyncRun.user_id == user_id)
        .order_by(OzonSyncRun.created_at.desc())
        .first()
    )


def _run_is_stale(run: OzonSyncRun, *, now: datetime | None = None) -> bool:
    if run.updated_at is None:
        return False
    updated_at = cast(datetime, run.updated_at)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - updated_at > timedelta(hours=2)


def _mark_stale_run_failed(db: Session, run: OzonSyncRun) -> None:
    run.status = "failed"
    run.error_message = "Синхронизация остановлена: более двух часов не было обновлений. Запустите её снова."
    run.finished_at = datetime.now(UTC)
    db.add(run)
    db.commit()


def _sync_payload(run: OzonSyncRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    total_days = (run.date_to - run.date_from).days + 1
    return {
        "run_id": str(run.id),
        "status": run.status,
        "progress": run.progress,
        "total_days": total_days,
        "date_from": run.date_from.isoformat(),
        "date_to": run.date_to.isoformat(),
        "error": run.error_message,
    }


def connection_status(*, db: Session, user_id: str, today: date | None = None) -> dict[str, Any]:
    row = db.query(OzonConnection).filter(OzonConnection.user_id == user_id).first()
    run = _latest_run(db, user_id)
    backfill = _backfill_payload(db, user_id=user_id, today=today or date.today())
    if row is None:
        return {
            "configured": False,
            "client_id": None,
            "status": "missing",
            "error": None,
            "sync": _sync_payload(run),
            "backfill": backfill,
        }
    try:
        client_id = decrypt_text(row.client_id_enc)
    except (CryptoConfigError, ValueError):
        client_id = None
        visible_error = "Не удалось расшифровать подключение; проверьте настройки шифрования сервера"
    else:
        visible_error = row.last_error
    status = row.status
    if run is not None and run.status in {"queued", "running"}:
        status = "syncing"
    return {
        "configured": True,
        "client_id": client_id,
        "status": status,
        "error": visible_error,
        "sync": _sync_payload(run),
        "backfill": backfill,
    }


def enqueue_sync(
    *,
    db: Session,
    user_id: str,
    date_from: date,
    date_to: date,
    trigger: str = MANUAL_TRIGGER,
) -> OzonSyncRun:
    if date_to < date_from:
        raise MarketplaceFinanceError("date_to must be on or after date_from")
    if (date_to - date_from).days + 1 > MAX_SYNC_DAYS:
        raise MarketplaceFinanceError(f"A manual Ozon sync can cover at most {MAX_SYNC_DAYS} days")
    if trigger not in SYNC_TRIGGERS:
        raise MarketplaceFinanceError("unknown Ozon sync trigger")
    lock = _acquire_owner_lock(db, user_id)
    if lock is None:
        raise MarketplaceFinanceError("Синхронизация уже ожидает запуска или выполняется")
    try:
        connection = db.query(OzonConnection).filter(OzonConnection.user_id == user_id).first()
        if connection is None:
            raise MarketplaceFinanceError("Connect Ozon before starting a sync")
        active = (
            db.query(OzonSyncRun)
            .filter(OzonSyncRun.user_id == user_id, OzonSyncRun.status.in_(("queued", "running")))
            .order_by(OzonSyncRun.created_at.desc())
            .first()
        )
        if active is not None:
            if not _run_is_stale(active):
                raise MarketplaceFinanceError("Синхронизация уже ожидает запуска или выполняется")
            # Keep the owner lock through both stale-run retirement and insertion of the
            # replacement. A waiting Celery delivery then sees either failed or queued,
            # never an acknowledged run orphaned by a racing replacement.
            _mark_stale_run_failed(db, active)
        run = OzonSyncRun(
            user_id=user_id,
            date_from=date_from,
            date_to=date_to,
            status="queued",
            trigger=trigger,
            progress=0,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run
    finally:
        _release_owner_lock(lock, user_id)


def get_product_cost(*, db: Session, user_id: str, sku: str) -> Decimal | None:
    row = (
        db.query(OzonProductCost)
        .filter(OzonProductCost.user_id == user_id, OzonProductCost.sku == sku)
        .first()
    )
    return Decimal(row.cost_price) if row is not None else None


def set_product_cost(*, db: Session, user_id: str, sku: str, cost_price: Decimal | None) -> Decimal | None:
    clean_sku = (sku or "").strip()
    if not clean_sku or len(clean_sku) > 64:
        raise MarketplaceFinanceError("sku must contain 1 to 64 characters")
    lock = _acquire_owner_lock(db, user_id)
    if lock is None:
        raise MarketplaceFinanceError("Синхронизация уже ожидает запуска или выполняется; повторите после её завершения")
    try:
        canonical_amount: Decimal | None = None
        active_run = (
            db.query(OzonSyncRun)
            .filter(OzonSyncRun.user_id == user_id, OzonSyncRun.status.in_(('queued', 'running')))
            .first()
        )
        if active_run is not None:
            raise MarketplaceFinanceError(
                "Синхронизация уже ожидает запуска или выполняется; повторите после её завершения"
            )

        row = (
            db.query(OzonProductCost)
            .filter(OzonProductCost.user_id == user_id, OzonProductCost.sku == clean_sku)
            .first()
        )
        if cost_price is None:
            if row is not None:
                db.delete(row)
        else:
            try:
                amount = Decimal(cost_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            except (InvalidOperation, ValueError, TypeError) as exc:
                raise MarketplaceFinanceError("Себестоимость должна быть конечным числом") from exc
            if not amount.is_finite() or amount < 0 or amount > Decimal("999999999999.99"):
                raise MarketplaceFinanceError("Себестоимость должна быть от 0 до 999999999999.99 руб.")
            canonical_amount = amount
            if row is None:
                row = OzonProductCost(user_id=user_id, sku=clean_sku, cost_price=amount)
            else:
                row.cost_price = amount
            db.add(row)

        # The ORM session disables autoflush. Persist the staged cost change to the
        # transaction so the replay sees it, but keep it uncommitted with the snapshots.
        db.flush()
        recompute_saved_ozon_costs(db=db, user_id=user_id)
        db.commit()
        return canonical_amount
    except Exception:
        db.rollback()
        raise
    finally:
        _release_owner_lock(lock, user_id)


def get_marketplace_settings(*, db: Session, user_id: str) -> dict[str, Any]:
    row = (
        db.query(OzonFinanceSettings)
        .filter(OzonFinanceSettings.user_id == user_id)
        .first()
    )
    owner = db.query(User).filter(User.id == user_id).first()
    return {
        "tax_rate": float(owner.tax_rate) if owner is not None and owner.tax_rate is not None else None,
        "tax_rate_source": "wb",
        "operation_expenses_confirmed": bool(row.operation_expenses_confirmed) if row is not None else False,
    }


def update_marketplace_settings(
    *,
    db: Session,
    user_id: str,
    operation_expenses_confirmed: bool,
) -> dict[str, Any]:
    statement = insert(OzonFinanceSettings).values(
        user_id=user_id,
        operation_expenses_confirmed=bool(operation_expenses_confirmed),
    )
    statement = statement.on_conflict_do_update(
        index_elements=[OzonFinanceSettings.user_id],
        set_={
            "operation_expenses_confirmed": statement.excluded.operation_expenses_confirmed,
            "updated_at": datetime.now(UTC),
        },
    )
    db.execute(statement)
    db.commit()
    return get_marketplace_settings(db=db, user_id=user_id)


def set_ozon_operational_expense(
    *, db: Session, user_id: str, expense_date: date, amount: Decimal
) -> Decimal:
    try:
        parsed = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MarketplaceFinanceError("Операционный расход должен быть конечной неотрицательной суммой.") from exc
    if not parsed.is_finite() or parsed < 0 or parsed > Decimal("999999999999.99"):
        raise MarketplaceFinanceError("Операционный расход должен быть от 0 до 999999999999.99 руб.")

    statement = insert(OzonOperationalExpense).values(
        user_id=user_id,
        date=expense_date,
        amount=parsed,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[OzonOperationalExpense.user_id, OzonOperationalExpense.date],
        set_={"amount": statement.excluded.amount, "updated_at": datetime.now(UTC)},
    )
    db.execute(statement)
    db.commit()
    return parsed


def _ozon_expenses_by_date(
    *, db: Session, user_id: str, date_from: date, date_to: date
) -> dict[date, Decimal]:
    return {
        cast(date, row.date): Decimal(row.amount)
        for row in db.query(OzonOperationalExpense)
        .filter(
            OzonOperationalExpense.user_id == user_id,
            OzonOperationalExpense.date >= date_from,
            OzonOperationalExpense.date <= date_to,
        )
        .all()
    }


def recompute_saved_ozon_costs(*, db: Session, user_id: str) -> int:
    """Reapply edited SKU costs to retained days using only saved accruals and type metadata."""
    states = (
        db.query(OzonSyncDay)
        .filter(OzonSyncDay.user_id == user_id, OzonSyncDay.status == "complete")
        .order_by(OzonSyncDay.day.asc())
        .all()
    )
    if not states:
        return 0
    from app.services.ozon_finance import normalize_accruals

    costs = {
        str(row.sku): Decimal(row.cost_price)
        for row in db.query(OzonProductCost).filter(OzonProductCost.user_id == user_id).all()
    }
    changed = 0
    for state in states:
        snapshots = (
            db.query(OzonAccrualSnapshot)
            .filter(OzonAccrualSnapshot.user_id == user_id, OzonAccrualSnapshot.accrual_date == state.day)
            .order_by(OzonAccrualSnapshot.accrual_id.asc())
            .all()
        )
        if not snapshots:
            continue
        if not isinstance(state.accrual_types, list):
            continue
        normalized = normalize_accruals([row.raw_payload for row in snapshots], state.accrual_types, costs=costs)
        daily = _normalizer_daily_for_day(normalized, state.day)
        if not daily:
            continue
        state.daily_data = _serialize_daily(daily, state.day)
        state.products_data = [
            {"sku": product["sku"], **{key: _json_safe(value) for key, value in product.items() if key != "sku"}}
            for product in _normalizer_products_for_day(normalized, state.day)
        ]
        existing_warnings = state.warnings if isinstance(state.warnings, list) else []
        stale_cost_warning = (
            f"{state.day}: себестоимость неизвестна из-за неполного количества, возврата или отсутствующей себестоимости"
        )
        current_warnings = [warning for warning in existing_warnings if warning != stale_cost_warning]
        state.warnings = list(dict.fromkeys([*current_warnings, *(normalized.get("warnings") or [])]))
        db.add(state)
        changed += 1
    db.flush()
    return changed


def _safe_error(exc: Exception, client_id: str | None = None, api_key: str | None = None) -> str:
    # Provider errors have already discarded response bodies and credentials. Keep those
    # useful contract messages, but do not persist arbitrary exception text from transports.
    try:
        from app.services.ozon_client import OzonAPIError, OzonContractError

        trusted = isinstance(exc, (OzonAPIError, OzonContractError, MarketplaceFinanceError))
    except ImportError:
        trusted = False
    value = (str(exc).strip() or exc.__class__.__name__) if trusted else ""
    for secret in (client_id, api_key):
        if secret:
            value = value.replace(secret, "[redacted]")
    lowered = value.lower()
    if "access was denied" in lowered or "отклонил доступ" in lowered or "401" in lowered or "403" in lowered:
        return "Нет доступа к Ozon API. Проверьте Client-Id, ключ и права доступа."
    if "rate limit" in lowered or "ограничил частоту" in lowered or "429" in lowered:
        return "Ozon ограничил частоту запросов. Попробуйте синхронизировать позже."
    if "server error" in lowered or "500" in lowered or "502" in lowered or "503" in lowered or "504" in lowered:
        return "Ozon API временно недоступен. Попробуйте синхронизировать позже."
    if any(token in lowered for token in ("api-key", "api_key", "authorization", "request failed")):
        return "Не удалось получить ответ Ozon API. Проверьте подключение и попробуйте позже."
    messages = {
        "Ozon is not connected": "Подключение Ozon не настроено.",
        "Ozon connection cannot be decrypted; check server encryption configuration": "Не удалось расшифровать подключение Ozon; проверьте настройки шифрования сервера.",
        "Server encryption is not configured": "На сервере не настроено шифрование подключений.",
        "Ozon returned a malformed accrual row": "Ozon вернул начисление в неизвестном формате; день не обновлён.",
        "Ozon accrual is missing accrual_id; day was not replaced": "В ответе Ozon нет ID начисления; день не обновлён.",
        "Ozon returned an accrual for a different date; day was not replaced": "Ozon вернул начисление за другую дату; день не обновлён.",
        "Ozon returned conflicting data for a repeated accrual ID": "Ozon вернул разные данные для одного начисления; день не обновлён.",
        "An Ozon accrual has no currency; day was not replaced": "В начислении Ozon не указана валюта; день не обновлён.",
        "Ozon returned a non-RUB accrual; currencies were not combined": "Ozon вернул начисление не в рублях; валюты не объединялись.",
        "Ozon returned an invalid daily accrual response": "Ozon вернул начисления дня в неверном формате; день не обновлён.",
        "Ozon normalizer returned an invalid result": "Не удалось проверить начисления Ozon; день не обновлён.",
        "Synchronization run disappeared": "Не удалось найти задачу синхронизации; запустите её снова.",
    }
    if value in messages:
        return messages[value]
    english_contract_messages = {
        "Ozon API returned invalid JSON": "Ozon вернул ответ, который не удалось прочитать; день не обновлён.",
        "Ozon API returned a non-object JSON response": "Ozon вернул ответ неизвестного формата; день не обновлён.",
    }
    if value in english_contract_messages:
        return english_contract_messages[value]
    if trusted and value:
        return value[:500]
    return "Синхронизация Ozon не выполнена. Проверьте подключение и попробуйте позже."


def _day_state(db: Session, user_id: str, day: date, *, create: bool = True) -> OzonSyncDay | None:
    row = db.query(OzonSyncDay).filter(OzonSyncDay.user_id == user_id, OzonSyncDay.day == day).first()
    if row is None and create:
        row = OzonSyncDay(user_id=user_id, day=day, status="missing", warnings=[])
        db.add(row)
        db.flush()
    return row


def _mark_day_attempt(
    db: Session,
    *,
    user_id: str,
    day: date,
    status: str,
    error: str | None = None,
) -> None:
    row = _day_state(db, user_id, day)
    assert row is not None
    row.last_attempt_status = status
    row.last_error = error
    db.add(row)
    db.commit()


def _day_product_skus(accruals: list[dict[str, Any]]) -> list[str]:
    skus: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("sku") is not None:
                sku = str(value["sku"]).strip()
                if sku:
                    skus.add(sku)
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(accruals)
    return sorted(skus)


def _dedupe_accruals(accruals: list[dict[str, Any]], day: date) -> tuple[list[dict[str, Any]], list[str]]:
    by_id: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for accrual in accruals:
        if not isinstance(accrual, dict):
            raise MarketplaceFinanceError("Ozon returned a malformed accrual row")
        accrual_id = str(accrual.get("accrual_id") or "").strip()
        if not accrual_id:
            raise MarketplaceFinanceError("Ozon accrual is missing accrual_id; day was not replaced")
        accrual_date = str(accrual.get("date") or "")[:10]
        if accrual_date and accrual_date != day.isoformat():
            raise MarketplaceFinanceError("Ozon returned an accrual for a different date; day was not replaced")
        if accrual_id in by_id:
            if by_id[accrual_id] != accrual:
                raise MarketplaceFinanceError("Ozon returned conflicting data for a repeated accrual ID")
            warnings.append("Repeated accrual ID was returned; it was counted once")
        by_id[accrual_id] = accrual
    return list(by_id.values()), list(dict.fromkeys(warnings))


def _extract_currency(accrual: dict[str, Any]) -> str | None:
    total = accrual.get("total_amount")
    currency = total.get("currency") if isinstance(total, dict) else None
    return str(currency).upper() if currency else None


def _store_catalogue(db: Session, *, user_id: str, products: list[dict[str, Any]]) -> None:
    for product in products:
        if not isinstance(product, dict) or product.get("sku") is None:
            continue
        sku = str(product["sku"]).strip()
        if not sku:
            continue
        statement = insert(OzonProduct).values(
            user_id=user_id,
            sku=sku,
            offer_id=str(product.get("offer_id"))[:255] if product.get("offer_id") is not None else None,
            name=str(product.get("name"))[:1000] if product.get("name") is not None else None,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_ozon_product_user_sku",
            set_={
                "offer_id": statement.excluded.offer_id,
                "name": statement.excluded.name,
                "updated_at": datetime.now(UTC),
            },
        )
        db.execute(statement)


def _replace_complete_day(
    db: Session,
    *,
    user_id: str,
    day: date,
    accruals: list[dict[str, Any]],
    normalized: dict[str, Any],
    catalogue: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    from sqlalchemy import delete

    daily = _normalizer_daily_for_day(normalized, day)
    if not accruals and not daily:
        daily = {key: Decimal("0") for key in ZERO_DAY_FIELDS}
        daily["date"] = day.isoformat()
        daily["sold_quantity"] = Decimal("0")
        daily["returned_quantity"] = Decimal("0")
        daily["net_quantity"] = Decimal("0")
        daily["accrual_classification_complete"] = True
        daily["sku_allocation_complete"] = True
        daily["profitability_complete"] = True
        # The finance feed has no coverage for advertising, tax, or general
        # expenses; an empty accrual ledger cannot turn those unknowns into zero.
        daily["ads_spend"] = None
        daily["tax"] = None
        daily["operation_expenses"] = None
        daily["margin"] = None
    products = _normalizer_products_for_day(normalized, day)
    daily_json = _serialize_daily(daily, day)
    products_json = [{"sku": product["sku"], **{key: _json_safe(value) for key, value in product.items() if key != "sku"}}
                     for product in products]

    state = _day_state(db, user_id, day)
    assert state is not None
    # Delete, insert raw source rows and publish normalized output in one transaction.
    db.execute(
        delete(OzonAccrualSnapshot).where(
            OzonAccrualSnapshot.user_id == user_id,
            OzonAccrualSnapshot.accrual_date == day,
        )
    )
    for accrual in accruals:
        db.add(
            OzonAccrualSnapshot(
                user_id=user_id,
                accrual_id=str(accrual["accrual_id"]),
                accrual_date=day,
                category=str(accrual.get("accrued_category") or accrual.get("category") or "")[:40] or None,
                currency=_extract_currency(accrual),
                raw_payload=_json_safe(accrual),
            )
        )
    _store_catalogue(db, user_id=user_id, products=catalogue)
    state.status = "empty" if not accruals else "complete"
    state.last_attempt_status = "succeeded"
    state.source_count = len(accruals)
    state.daily_data = daily_json
    state.products_data = products_json
    source_types = normalized.get("accrual_types")
    state.accrual_types = _json_safe(source_types) if isinstance(source_types, list) else None
    state.warnings = list(dict.fromkeys(str(w)[:500] for w in warnings if w))
    state.last_error = None
    state.completed_at = datetime.now(UTC)
    db.add(state)
    db.commit()


def _lock_key(user_id: str) -> int:
    return int.from_bytes(hashlib.sha256(user_id.encode("utf-8")).digest()[:8], "big", signed=True)


def _acquire_owner_lock(db: Session, user_id: str):
    # Postgres session lock protects against duplicate task deliveries and stale queued work.
    bind = db.get_bind()
    owns_connection = isinstance(bind, Engine)
    lock_connection = bind.connect() if isinstance(bind, Engine) else db.connection()
    try:
        acquired = bool(
            lock_connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"), {"lock_key": _lock_key(user_id)}
            ).scalar()
        )
        # A standalone engine connection needs its transaction ended between queries.
        # A test/session-bound connection belongs to the caller and must stay open.
        if owns_connection:
            lock_connection.commit()
        if not acquired:
            if owns_connection:
                lock_connection.close()
            return None
        return lock_connection, owns_connection
    except Exception:
        if owns_connection:
            lock_connection.rollback()
            lock_connection.close()
        raise


def _release_owner_lock(lock: tuple[Any, bool], user_id: str) -> None:
    lock_connection, owns_connection = lock
    try:
        lock_connection.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": _lock_key(user_id)})
        if owns_connection:
            lock_connection.commit()
    except Exception:
        if owns_connection:
            lock_connection.rollback()
    finally:
        if owns_connection:
            lock_connection.close()


def run_sync_run(*, db: Session, run_id: str) -> dict[str, Any]:
    """Run one bounded sync. Provider calls happen only here, never from read APIs."""
    run = db.query(OzonSyncRun).filter(OzonSyncRun.id == run_id).first()
    if run is None or run.status not in {"queued", "running"}:
        return {"status": run.status if run is not None else "missing"}
    user_id = str(run.user_id)
    lock = _acquire_owner_lock(db, user_id)
    if lock is None:
        # A duplicate delivery can observe `queued` while the first worker already
        # holds the owner lock but has not committed `running` yet. Never change the
        # shared run record on contention; the lock owner remains responsible for it.
        return {"status": "busy", "current_status": run.status}

    client_id: str | None = None
    api_key: str | None = None
    client: Any | None = None
    try:
        # Refresh after acquiring the lock; duplicate Celery delivery must not replay a run.
        db.expire_all()
        run = db.query(OzonSyncRun).filter(OzonSyncRun.id == run_id).first()
        if run is None or run.status not in {"queued", "running"}:
            return {"status": run.status if run is not None else "missing"}
        connection, client_id, api_key = _client_credentials(db, user_id)
        from app.services.ozon_client import OzonClient
        from app.services.ozon_finance import normalize_accruals

        client = OzonClient(client_id, api_key)
        run.status = "running"
        run.started_at = run.started_at or datetime.now(UTC)
        run.error_message = None
        connection.status = "syncing"
        connection.last_error = None
        db.add_all([run, connection])
        db.commit()

        try:
            accrual_types = client.fetch_accrual_types()
        except Exception as exc:  # type/status-specific message is sanitized before storage
            safe = _safe_error(exc, client_id, api_key)
            run.status = "failed"
            run.error_message = safe
            run.finished_at = datetime.now(UTC)
            connection.status = "error"
            connection.last_error = safe
            db.add_all([run, connection])
            db.commit()
            return {"status": "failed", "error": safe}
        connection.accrual_types = _json_safe(accrual_types)
        db.add(connection)
        db.commit()

        total_days = (run.date_to - run.date_from).days + 1
        completed_count = 0
        failed_count = 0
        errors: list[str] = []
        day = run.date_from
        while day <= run.date_to:
            _mark_day_attempt(db, user_id=user_id, day=day, status="running")
            try:
                fetched = client.fetch_accruals_day(day)
                if not isinstance(fetched, list):
                    raise MarketplaceFinanceError("Ozon returned an invalid daily accrual response")
                accruals, dedupe_warnings = _dedupe_accruals(fetched, day)
                currencies = {_extract_currency(row) for row in accruals}
                if None in currencies:
                    raise MarketplaceFinanceError("An Ozon accrual has no currency; day was not replaced")
                if any(currency != "RUB" for currency in currencies):
                    raise MarketplaceFinanceError("Ozon returned a non-RUB accrual; currencies were not combined")
                skus = _day_product_skus(accruals)
                catalogue: list[dict[str, Any]] = []
                catalogue_warnings: list[str] = []
                for start in range(0, len(skus), 1000):
                    try:
                        catalogue.extend(client.fetch_products(skus[start : start + 1000]))
                    except Exception as exc:
                        catalogue_warnings.append(
                            "Не удалось обновить названия товаров и offer ID: " + _safe_error(exc, client_id, api_key)
                        )
                costs = {
                    str(row.sku): Decimal(row.cost_price)
                    for row in db.query(OzonProductCost)
                    .filter(OzonProductCost.user_id == user_id, OzonProductCost.sku.in_(skus or ["__none__"]))
                    .all()
                }
                normalized = normalize_accruals(accruals, accrual_types, costs=costs)
                if not isinstance(normalized, dict):
                    raise MarketplaceFinanceError("Ozon normalizer returned an invalid result")
                normalized["accrual_types"] = accrual_types
                warnings = [
                    *dedupe_warnings,
                    *catalogue_warnings,
                    *(normalized.get("warnings") or []),
                ]
                _replace_complete_day(
                    db,
                    user_id=user_id,
                    day=day,
                    accruals=accruals,
                    normalized=normalized,
                    catalogue=catalogue,
                    warnings=warnings,
                )
                completed_count += 1
            except Exception as exc:
                db.rollback()
                safe = _safe_error(exc, client_id, api_key)
                _mark_day_attempt(db, user_id=user_id, day=day, status="failed", error=safe)
                failed_count += 1
                errors.append(f"{day.isoformat()}: {safe}")
            run = db.query(OzonSyncRun).filter(OzonSyncRun.id == run_id).first()
            if run is None:
                raise MarketplaceFinanceError("Synchronization run disappeared")
            run.progress = completed_count + failed_count
            run.error_message = "\n".join(errors)[:1000] if errors else None
            db.add(run)
            db.commit()
            day += timedelta(days=1)

        run = db.query(OzonSyncRun).filter(OzonSyncRun.id == run_id).first()
        connection = db.query(OzonConnection).filter(OzonConnection.user_id == user_id).first()
        assert run is not None and connection is not None
        run.status = "partial" if failed_count and completed_count else "failed" if failed_count else "complete"
        run.finished_at = datetime.now(UTC)
        run.progress = total_days
        connection.status = "error" if failed_count and not completed_count else "ready"
        connection.last_error = run.error_message
        if completed_count:
            connection.last_success_at = datetime.now(UTC)
        db.add_all([run, connection])
        db.commit()
        return {"status": run.status, "progress": run.progress, "total_days": total_days, "error": run.error_message}
    except Exception as exc:
        db.rollback()
        safe = _safe_error(exc, client_id, api_key)
        failed_run = db.query(OzonSyncRun).filter(OzonSyncRun.id == run_id).first()
        connection = db.query(OzonConnection).filter(OzonConnection.user_id == user_id).first()
        if failed_run is not None and failed_run.status in {"queued", "running"}:
            failed_run.status = "failed"
            failed_run.error_message = safe
            failed_run.finished_at = datetime.now(UTC)
            db.add(failed_run)
        if connection is not None:
            connection.status = "error"
            connection.last_error = safe
            db.add(connection)
        db.commit()
        logger.warning("Ozon sync run failed owner=%s run=%s reason=%s", user_id, run_id, safe)
        return {"status": "failed", "error": safe}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        _release_owner_lock(lock, user_id)


def _day_numbers(row: dict[str, Any]) -> dict[str, float | None]:
    return {
        **{field: _money(row.get(field)) for field in DAILY_AMOUNT_FIELDS},
        **{field: _money(row.get(field)) for field in OZON_QUANTITY_FIELDS},
    }


def _warning_list(state: OzonSyncDay | None) -> list[str]:
    if state is None:
        return []
    values: list[Any] = state.warnings if isinstance(state.warnings, list) else []
    warnings = [_friendly_warning(str(item)) for item in values if item]
    if state.last_attempt_status == "failed" and state.last_error:
        if state.status in {"complete", "empty"}:
            warnings.append(f"{state.day}: обновить день не удалось; показывается последний сохранённый срез: {state.last_error}")
        else:
            warnings.append(f"{state.day}: день не загружен из-за ошибки: {state.last_error}")
    return warnings


def _friendly_warning(value: str) -> str:
    if "reconciliation is unavailable" in value:
        day = value.split(":", 1)[0]
        return f"Не удалось сверить итог начислений с деталями за {day}: части исходных данных не хватает."
    if "source component total differs" in value:
        day = value.split(":", 1)[0]
        detail = value.split("differs from authoritative total_amount by", 1)[-1].strip()
        return f"Сумма деталей за {day} отличается от итога операции на {detail}."
    if "COGS and margin are unknown" in value:
        day = value.split(":", 1)[0]
        return f"Себестоимость и прибыль за {day} пока неизвестны: нет подтверждённого количества продаж."
    if "Repeated identical accrual ID" in value:
        return "Ozon повторил одно начисление; оно учтено один раз."
    if "related breakdown is incomplete" in value:
        day = value.split(":", 1)[0]
        return f"Детализация за {day} неполная; исходный итог начисления сохранён."
    if "posting contains a malformed product detail" in value or "posting commission detail is missing" in value:
        day = value.split(":", 1)[0]
        return f"Детали продажи за {day} неполные; исходный итог начисления сохранён."
    return value


# Charges that reduce a SKU's margin. Absent means no such accrual for the SKU; null means unknown.
_PRODUCT_CHARGE_FIELDS = ("commission", "logistics", "acquiring", "penalties", "storage", "ads_spend", "other_expenses")


def _product_margin(row: dict[str, Any], *, cogs: Decimal | None, tax: Decimal | None) -> Decimal | None:
    """Owner rule (2026-10-25): SKU margin = revenue - Ozon charges - COGS - tax (+ compensation), for one day.

    Uses only the SKU's own fields and its known cost; sku-allocation and profitability flags are not required.
    Not-tied charges and operating expenses belong to the day, not to the SKU.
    """
    # An unclassified charge with a known amount is still a charge (the reference workbook counts it in «Прочие»);
    # only a null amount, which means the value itself is unknown, makes the margin unknown.
    revenue = _decimal(row.get("revenue"))
    if revenue is None or cogs is None or tax is None:
        return None
    charges = Decimal("0")
    for field in _PRODUCT_CHARGE_FIELDS:
        if field not in row:
            continue
        value = _decimal(row.get(field))
        if value is None:
            return None
        charges += value
    compensation = Decimal("0")
    if "compensation" in row:
        value = _decimal(row.get("compensation"))
        if value is None:
            return None
        compensation = value
    return revenue - charges + compensation - cogs - tax


def _uncatalogued_label(sku: str) -> str:
    """Name of a SKU that Ozon reports in accruals but that has no catalogue card (no name or offer_id)."""
    return f"SKU {sku} (нет карточки)"


def _build_ozon_products(
    db: Session,
    *,
    user_id: str,
    day_states: list[OzonSyncDay],
    tax_rate: float | None,
) -> list[dict[str, Any]]:
    products: dict[str, dict[str, Any]] = {}
    catalogue = {
        str(row.sku): row
        for row in db.query(OzonProduct).filter(OzonProduct.user_id == user_id).all()
    }
    costs = {
        str(row.sku): Decimal(row.cost_price)
        for row in db.query(OzonProductCost).filter(OzonProductCost.user_id == user_id).all()
    }
    for state in day_states:
        raw_data = state.products_data
        data: list[dict[str, Any]] = (
            [row for row in raw_data if isinstance(row, dict)] if isinstance(raw_data, list) else []
        )
        for row in data:
            sku = str(row.get("sku") or "").strip()
            # Every SKU in the accruals is reported, with or without a catalogue card (see the label below).
            if not sku:
                continue
            product = products.setdefault(
                sku,
                {
                    "marketplace": "ozon",
                    "sku": sku,
                    "offer_id": None,
                    "name": _uncatalogued_label(sku),
                    "cost_price": _money(costs.get(sku)),
                    "daily": [],
                },
            )
            product_daily: dict[str, Any] = {
                "date": state.day.isoformat(),
                **{field: _money(row.get(field)) for field in DAILY_AMOUNT_FIELDS},
                **{field: _money(row.get(field)) for field in OZON_QUANTITY_FIELDS},
            }
            product_tax = _derive_ozon_tax(row.get("revenue"), tax_rate)
            product_daily["tax"] = _money(product_tax)
            # A SKU without an entered cost has no COGS or margin on any day (not even a zero-unit day), so its
            # period totals are never a partial sum.
            product_cogs = _decimal(row.get("cogs")) if sku in costs else None
            product_daily["cogs"] = _money(product_cogs)
            product_daily["margin"] = _money(_product_margin(row, cogs=product_cogs, tax=product_tax))
            product_daily["operation_expenses"] = None
            product["daily"].append(product_daily)
    for sku, product in products.items():
        row = catalogue.get(sku)
        if row is not None:
            product["offer_id"] = row.offer_id
            product["name"] = row.name or sku
    return sorted(products.values(), key=lambda row: (row["name"] or "", row["sku"]))


def _build_wb_products(
    db: Session,
    *,
    user_id: str,
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    articles = {
        int(row.nm_id): row
        for row in db.query(Article).filter(Article.user_id == user_id).all()
    }
    rows = (
        db.query(SkuDaily)
        .filter(SkuDaily.user_id == user_id, SkuDaily.date >= date_from, SkuDaily.date <= date_to)
        .order_by(SkuDaily.date.asc())
        .all()
    )
    products: dict[str, dict[str, Any]] = {}
    for row in rows:
        sku = str(row.nm_id)
        article = articles.get(int(row.nm_id))
        product = products.setdefault(
            sku,
            {
                "marketplace": "wb",
                "sku": sku,
                "offer_id": article.vendor_code if article else None,
                "name": (article.name or article.vendor_code or sku) if article else sku,
                "cost_price": _money(article.cost_price) if article else None,
                "daily": [],
            },
        )
        product["daily"].append(
            {
                "date": row.date.isoformat(),
                "revenue": _money(row.revenue),
                "commission": _money(row.commission),
                "acquiring": None,
                "logistics": _money(row.logistics),
                "penalties": _money(row.penalties),
                "storage": _money(row.storage),
                "ads_spend": _money(row.ads_spend),
                "other_expenses": None,
                "compensation": None,
                "cogs": _money(row.cogs),
                "tax": _money(row.tax),
                "operation_expenses": None,
                "margin": _money(row.margin),
                "marketplace_net": None,
                "unallocated_amount": None,
                "reconciliation_difference": None,
            }
        )
    return sorted(products.values(), key=lambda row: (row["name"] or "", row["sku"]))


def _wb_daily_rows(db: Session, *, user_id: str, date_from: date, date_to: date) -> dict[date, PnlDaily]:
    return {
        cast(date, row.date): row
        for row in db.query(PnlDaily)
        .filter(PnlDaily.user_id == user_id, PnlDaily.date >= date_from, PnlDaily.date <= date_to)
        .all()
    }


def _wb_daily(row: PnlDaily) -> dict[str, Any]:
    return {
        "date": row.date.isoformat(),
        "revenue": row.revenue,
        "commission": row.commission,
        "acquiring": None,
        "logistics": row.logistics,
        "penalties": row.penalties,
        "storage": row.storage,
        "ads_spend": row.ads_spend,
        "other_expenses": None,
        "compensation": None,
        "cogs": row.cogs,
        "tax": row.tax,
        # Combined output reads the saved WB store-level expense once from PnlDaily.
        "operation_expenses": Decimal("0"),
        "margin": row.margin,
        "marketplace_net": None,
        "unallocated_amount": None,
        "reconciliation_difference": None,
    }


def _sum_known_sources(sources: list[dict[str, Any]], field: str) -> float | None:
    if not sources:
        return None
    return _money_sum([source.get(field) for source in sources])


def _derive_ozon_tax(revenue: Any, tax_rate: Any) -> Decimal | None:
    """Owner decision (2026-10-25): tax = rate x revenue, signed. A day of net returns lowers the tax instead of
    counting as zero, so day and SKU taxes both add up to rate x period revenue (sales minus returns)."""
    amount = _decimal(revenue)
    rate = _decimal(tax_rate)
    if amount is None or rate is None:
        return None
    return amount * rate


def build_overview(
    *,
    db: Session,
    store_owner: User,
    marketplace: str,
    date_from: date,
    date_to: date,
) -> dict[str, Any]:
    if marketplace not in {"ozon", "all"}:
        raise MarketplaceFinanceError("marketplace must be ozon or all")
    if date_to < date_from:
        raise MarketplaceFinanceError("date_to must be on or after date_from")
    user_id = str(store_owner.id)
    connection = connection_status(db=db, user_id=user_id)
    finance_settings = get_marketplace_settings(db=db, user_id=user_id)
    tax_rate = finance_settings["tax_rate"]
    expenses_by_date = _ozon_expenses_by_date(
        db=db, user_id=user_id, date_from=date_from, date_to=date_to
    )
    wb_enabled = bool((store_owner.wb_api_key or "").strip())
    ozon_enabled = bool(connection["configured"])
    # `all` is a comparison of both source ledgers. Missing connection or coverage stays visible.
    require_wb = marketplace == "all"
    require_ozon = marketplace in {"ozon", "all"}

    day_states = (
        db.query(OzonSyncDay)
        .filter(OzonSyncDay.user_id == user_id, OzonSyncDay.day >= date_from, OzonSyncDay.day <= date_to)
        .order_by(OzonSyncDay.day.asc())
        .all()
    )
    ozon_state_map: dict[date, OzonSyncDay] = {cast(date, row.day): row for row in day_states}
    wb_rows = _wb_daily_rows(db, user_id=user_id, date_from=date_from, date_to=date_to) if require_wb else {}
    warnings: list[str] = []
    if marketplace == "all" and not wb_enabled:
        warnings.append("Ключ Wildberries не настроен; используются только сохранённые данные Wildberries")
    if marketplace == "all" and not ozon_enabled:
        warnings.append("Ozon не подключён; общий отчёт будет неполным, пока Ozon не подключён и не синхронизирован")
    if marketplace == "all":
        warnings.append("Эквайринг Wildberries отдельно не выделяется из комиссии; количества в сводной строке не объединяются")
        warnings.append("Прибыль WB и общий итог предварительные: старый импорт WB не сохраняет все удержания и основания доплат; полной сверки с финансовым отчётом нет")
    if marketplace == "ozon" and not ozon_enabled:
        warnings.append("Ozon не подключён")
    if require_ozon and tax_rate is None:
        warnings.append("Ставка налога Ozon не задана; налог и прибыль Ozon неизвестны")

    daily: list[dict[str, Any]] = []
    missing_dates: list[str] = []
    ozon_day_results: dict[date, dict[str, Any]] = {}
    day = date_from
    while day <= date_to:
        state = ozon_state_map.get(cast(date, day))
        ozon_covered = bool(ozon_enabled and state and state.status in {"complete", "empty"})
        wb_covered = bool(wb_rows.get(day)) if require_wb else False
        required = []
        if marketplace == "ozon":
            required = [ozon_covered]
        else:
            if require_wb:
                required.append(wb_covered)
            if require_ozon:
                required.append(ozon_covered)
            if not required:
                required = [False]
        complete = all(required)
        warnings.extend(_warning_list(state))
        if not complete:
            missing_dates.append(day.isoformat())
            daily.append(
                {
                    "date": day.isoformat(),
                    **{field: None for field in DAILY_AMOUNT_FIELDS},
                    **{field: None for field in OZON_QUANTITY_FIELDS},
                }
            )
            if marketplace == "all" and require_wb and not wb_covered:
                warnings.append(f"Нет данных Wildberries за {day.isoformat()}")
            if require_ozon and not ozon_covered:
                warnings.append(f"Нет данных Ozon за {day.isoformat()}")
            day += timedelta(days=1)
            continue

        ozon_daily = state.daily_data if state and isinstance(state.daily_data, dict) else None
        ozon_result: dict[str, Any] | None = None
        if require_ozon:
            # The whole Ozon day, every SKU included: Ozon's money is never left out of the totals (2026-10-25).
            ozon_result = dict(ozon_daily or {field: 0 for field in ZERO_DAY_FIELDS})
            ozon_result["date"] = day.isoformat()
            ozon_day_results[day] = ozon_result
            tax = _derive_ozon_tax(ozon_result.get("revenue"), tax_rate)
            # Entered operating expenses; a day without an entry counts as 0, as in WB.
            expense = expenses_by_date.get(day, Decimal("0"))
            ozon_result["tax"] = tax
            ozon_result["operation_expenses"] = expense
            # Margin is set after the products are built (see the known-cost pass below), not per day here.
            ozon_result["margin"] = None

        if marketplace == "ozon":
            result = ozon_result or {field: None for field in DAILY_AMOUNT_FIELDS}
            daily.append(
                {
                    "date": day.isoformat(),
                    **_day_numbers(result),
                    "cost_known_revenue_share": _share(result.get("cost_known_revenue_share")),
                }
            )
            day += timedelta(days=1)
            continue

        sources: list[dict[str, Any]] = []
        if require_wb:
            sources.append(_wb_daily(wb_rows[day]))
        if require_ozon and ozon_result is not None:
            sources.append(ozon_result)
        result = {"date": day.isoformat()}
        for field in DAILY_AMOUNT_FIELDS:
            if field == "operation_expenses":
                ozon_expense = expenses_by_date.get(day, Decimal("0")) if require_ozon else Decimal("0")
                wb_expense = _decimal(wb_rows[day].operation_expenses) if require_wb else Decimal("0")
                result[field] = _money(wb_expense + ozon_expense) if wb_expense is not None else None
            elif field == "unallocated_amount" and require_wb:
                # WB's legacy ledger has no unallocated-fee field, so the combined total is unknown.
                result[field] = None
            else:
                result[field] = _sum_known_sources(sources, field)
        daily.append({"date": day.isoformat(), **_day_numbers(result)})
        day += timedelta(days=1)

    products: list[dict[str, Any]] = []
    if marketplace == "ozon":
        products = _build_ozon_products(db, user_id=user_id, day_states=day_states, tax_rate=tax_rate)
    elif marketplace == "all":
        products.extend(_build_wb_products(db, user_id=user_id, date_from=date_from, date_to=date_to))
        products.extend(_build_ozon_products(db, user_id=user_id, day_states=day_states, tax_rate=tax_rate))

    if require_ozon:
        # Owner decision (2026-10-25): margin = balance (net) − COGS of the SKUs whose cost is known − tax − entered
        # operating expenses, as WB sums its day. Missing operating expenses count as 0; no confirmation gate.
        known_by_day = _known_cost_by_day(products)
        for row in daily:
            day_obj = date.fromisoformat(row["date"])
            row.update(
                _day_profit_known(
                    marketplace=marketplace,
                    row=row,
                    ozon_day=ozon_day_results.get(day_obj),
                    known=known_by_day.get(row["date"]),
                    ozon_expense=expenses_by_date.get(day_obj, Decimal("0")),
                    wb_row=wb_rows.get(day_obj) if require_wb else None,
                )
            )
    warnings = list(dict.fromkeys(warnings))
    return {
        "marketplace": marketplace,
        "currency": "RUB",
        "accounting_basis": {
            "margin": "preliminary_wb_and_ozon" if marketplace == "all" else "current_cost_estimate",
            "costs": "current_sku_cost_restates_all_dates",
            "tax": "estimated_revenue_times_wb_rate",
            "wb_fully_reconciled": False if marketplace == "all" else None,
        },
        "daily": daily,
        "products": products,
        "warnings": warnings,
        "coverage": {"complete": not missing_dates, "missing_dates": missing_dates},
        "connection": {
            "configured": connection["configured"],
            "status": connection["status"],
            "error": connection["error"],
        },
        "sync": connection["sync"],
    }


# ── Automatic synchronisation: year-to-date backfill chain and daily refresh ──


def backfill_window(today: date) -> tuple[date, date] | None:
    """1 January of the current year through yesterday; None until one day can be complete."""
    through = today - timedelta(days=1)
    start = date(today.year, 1, 1)
    if through < start:
        return None
    return start, through


def _latest_backfill_run(db: Session, *, user_id: str, start: date) -> OzonSyncRun | None:
    # Chunks are contiguous, so the latest chunk has the largest start date; created_at only breaks ties
    # (a retry shares its failed chunk's start date).
    return (
        db.query(OzonSyncRun)
        .filter(
            OzonSyncRun.user_id == user_id,
            OzonSyncRun.trigger == BACKFILL_TRIGGER,
            OzonSyncRun.date_from >= start,
        )
        .order_by(OzonSyncRun.date_from.desc(), OzonSyncRun.created_at.desc())
        .first()
    )


def _backfill_payload(db: Session, *, user_id: str, today: date) -> dict[str, Any] | None:
    window = backfill_window(today)
    if window is None:
        return None
    start, through = window
    latest = _latest_backfill_run(db, user_id=user_id, start=start)
    last_done = (
        db.query(OzonSyncRun.date_to)
        .filter(
            OzonSyncRun.user_id == user_id,
            OzonSyncRun.trigger == BACKFILL_TRIGGER,
            OzonSyncRun.date_from >= start,
            OzonSyncRun.status.in_(FINISHED_RUN_STATUSES),
        )
        .order_by(OzonSyncRun.date_to.desc())
        .first()
    )
    last_completed = last_done[0] if last_done else None
    if latest is None:
        status = "idle"
    elif latest.status in ACTIVE_RUN_STATUSES:
        status = "running"
    elif latest.status == "failed":
        status = "failed"
    elif last_completed is not None and last_completed >= through:
        status = "complete"
    else:
        # The next bounded chunk is queued by the finishing worker or, if that was blocked, by the daily job.
        status = "running"
    return {
        "status": status,
        "date_from": start.isoformat(),
        "through_date": through.isoformat(),
        "last_completed_date": last_completed.isoformat() if last_completed else None,
        "error": latest.error_message if latest is not None and latest.status == "failed" else None,
    }


def next_backfill_run(db: Session, *, user_id: str, today: date) -> OzonSyncRun | None:
    """Queue the next due ≤31-day backfill chunk; None when nothing is due or the owner is busy."""
    window = backfill_window(today)
    if window is None:
        return None
    start, through = window
    latest = _latest_backfill_run(db, user_id=user_id, start=start)
    if latest is None:
        date_from = start
        date_to = min(start + timedelta(days=MAX_SYNC_DAYS - 1), through)
    elif latest.status in ACTIVE_RUN_STATUSES:
        return None
    elif latest.status == "failed":
        # Retry the same chunk; days already saved as complete are replaced atomically, never double-counted.
        date_from, date_to = latest.date_from, latest.date_to
    elif latest.date_to >= through:
        return None
    else:
        date_from = latest.date_to + timedelta(days=1)
        date_to = min(date_from + timedelta(days=MAX_SYNC_DAYS - 1), through)
    try:
        return enqueue_sync(
            db=db,
            user_id=user_id,
            date_from=date_from,
            date_to=date_to,
            trigger=BACKFILL_TRIGGER,
        )
    except MarketplaceFinanceError:
        # A manual or daily run holds the owner's single slot; the daily job resumes the chain.
        return None
    except IntegrityError:
        db.rollback()
        return None


def schedule_initial_backfill(db: Session, *, user_id: str, today: date) -> OzonSyncRun | None:
    """Called after a connection is saved: start the year-to-date backfill from its first chunk."""
    return next_backfill_run(db, user_id=user_id, today=today)


def chain_backfill_after_run(db: Session, *, run_id: str, today: date) -> OzonSyncRun | None:
    """Called by the worker after a run finishes: queue the next chunk only after a finished backfill run."""
    run = db.query(OzonSyncRun).filter(OzonSyncRun.id == run_id).first()
    if run is None or run.trigger != BACKFILL_TRIGGER or run.status not in FINISHED_RUN_STATUSES:
        return None
    return next_backfill_run(db, user_id=str(run.user_id), today=today)


def plan_daily_refresh_for_ready_connections(db: Session, *, today: date) -> list[str]:
    """Beat job: resume an unfinished backfill, else refresh the last three completed days."""
    through = today - timedelta(days=1)
    date_from = through - timedelta(days=DAILY_REFRESH_DAYS - 1)
    user_ids = [
        str(row.user_id)
        for row in db.query(OzonConnection.user_id).filter(OzonConnection.status == "ready").all()
    ]
    queued: list[str] = []
    for user_id in user_ids:
        run = next_backfill_run(db, user_id=user_id, today=today)
        if run is None:
            try:
                run = enqueue_sync(
                    db=db,
                    user_id=user_id,
                    date_from=date_from,
                    date_to=through,
                    trigger=DAILY_TRIGGER,
                )
            except MarketplaceFinanceError:
                continue
            except IntegrityError:
                db.rollback()
                continue
        queued.append(str(run.id))
    return queued


# ── Marketplace rows shaped like the WB screens' API responses ────────────────


def _known_cost_by_day(products: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Per Ozon day, over SKU rows: COGS and revenue of SKUs with a known COGS, and SKU-days whose units are unknown.

    A SKU with known units but no entered cost adds nothing to COGS (as in WB, where a missing cost counts as 0);
    a SKU whose units are unknown makes the day's COGS unknown.
    """
    by_day: dict[str, dict[str, float]] = {}
    for product in products:
        if product.get("marketplace") != "ozon":
            continue
        for row in product.get("daily") or []:
            bucket = by_day.setdefault(row["date"], {"cogs": 0.0, "revenue": 0.0, "units_unknown": 0.0})
            if row.get("cogs") is not None:
                bucket["cogs"] += float(row["cogs"])
                if row.get("revenue") is not None:
                    bucket["revenue"] += float(row["revenue"])
            elif row.get("net_quantity") is None:
                bucket["units_unknown"] += 1
    return by_day


def _ozon_day_profit(
    known: dict[str, float] | None,
    *,
    ozon_day: dict[str, Any],
    ozon_expense: Decimal,
) -> tuple[float | None, float | None, float]:
    """Owner rule: day margin = balance (Ozon's net for the day) − known COGS − tax − entered operating expenses.

    The balance already holds the not-tied charges and compensations. A day without SKU rows has zero COGS unless it
    has revenue, which then has no SKU behind it and leaves COGS and margin unknown.
    """
    revenue = _decimal(ozon_day.get("revenue"))
    if known is None:
        cogs: float | None = None if revenue is None or revenue != 0 else 0.0
        known_revenue = 0.0
    else:
        cogs = None if known["units_unknown"] else known["cogs"]
        known_revenue = known["revenue"]
    net = _decimal(ozon_day.get("marketplace_net"))
    tax = _decimal(ozon_day.get("tax"))
    margin = (
        None
        if cogs is None or net is None or tax is None
        else float(net - Decimal(str(cogs)) - tax - ozon_expense)
    )
    return (
        round(cogs, 2) if cogs is not None else None,
        round(margin, 2) if margin is not None else None,
        round(known_revenue, 2),
    )


def _day_profit_known(
    *,
    marketplace: str,
    row: dict[str, Any],
    ozon_day: dict[str, Any] | None,
    known: dict[str, float] | None,
    ozon_expense: Decimal,
    wb_row: PnlDaily | None,
) -> dict[str, float | None]:
    """Day COGS, margin and known-cost revenue; the combined view adds the WB day as saved.

    ozon_day is the Ozon ledger's own day (every SKU, with its tax), which the combined row does not carry.
    """
    if row.get("revenue") is None or ozon_day is None:
        return {"cogs": None, "margin": None, "known_cost_revenue": None}
    cogs, margin, known_revenue = _ozon_day_profit(known, ozon_day=ozon_day, ozon_expense=ozon_expense)
    if marketplace == "ozon":
        return {"cogs": cogs, "margin": margin, "known_cost_revenue": known_revenue}
    cogs_parts: list[float] = []
    margin_parts: list[float] = []
    revenue_parts: list[float] = [known_revenue]
    if wb_row is not None:
        if wb_row.cogs is not None:
            cogs_parts.append(float(wb_row.cogs))
        if wb_row.margin is not None:
            margin_parts.append(float(wb_row.margin))
        if wb_row.revenue is not None:
            revenue_parts.append(float(wb_row.revenue))
    if cogs is not None:
        cogs_parts.append(cogs)
    if margin is not None:
        margin_parts.append(margin)
    return {
        "cogs": round(sum(cogs_parts), 2) if cogs_parts else None,
        "margin": round(sum(margin_parts), 2) if margin_parts else None,
        "known_cost_revenue": round(sum(revenue_parts), 2),
    }


def build_marketplace_pnl_rows(
    *, db: Session, store_owner: User, marketplace: str, date_from: date, date_to: date
) -> list[dict[str, Any]]:
    """Daily rows in the WB /dashboard/pnl shape, read from the overview's day rules.

    Owner decision (2026-10-25): a day's revenue and expense lines sum the known postings; COGS and margin are sums
    over the SKUs whose cost is known; margin = net - COGS - tax - entered operating expenses (missing = 0). A day with
    no data at all stays null. known_cost_revenue is the revenue that carries that cost.
    """
    overview = build_overview(
        db=db,
        store_owner=store_owner,
        marketplace=marketplace,
        date_from=date_from,
        date_to=date_to,
    )
    return [
        {
            "date": row["date"],
            **{field: row.get(field) for field in WB_SHAPED_PNL_FIELDS},
            "known_cost_revenue": row.get("known_cost_revenue"),
        }
        for row in overview["daily"]
    ]


def build_marketplace_sku_rows(
    *, db: Session, store_owner: User, marketplace: str, date_from: date, date_to: date
) -> list[dict[str, Any]]:
    """Per product and day rows; Ozon has no funnel, so order counts are null rather than zero."""
    overview = build_overview(
        db=db,
        store_owner=store_owner,
        marketplace=marketplace,
        date_from=date_from,
        date_to=date_to,
    )
    rows: list[dict[str, Any]] = []
    for product in overview["products"]:
        nm_id = str(product["sku"])
        for day_row in product.get("daily") or []:
            rows.append(
                {
                    "date": day_row["date"],
                    "nm_id": nm_id,
                    "marketplace": product["marketplace"],
                    "name": product.get("name"),
                    "revenue": day_row.get("revenue"),
                    "commission": day_row.get("commission"),
                    "logistics": day_row.get("logistics"),
                    "penalties": day_row.get("penalties"),
                    "storage": day_row.get("storage"),
                    "ads_spend": day_row.get("ads_spend"),
                    "cogs": day_row.get("cogs"),
                    "tax": day_row.get("tax"),
                    "margin": day_row.get("margin"),
                    "open_count": None,
                    "cart_count": None,
                    "order_count": None,
                    "order_sum": None,
                }
            )
    rows.sort(key=lambda row: (row["date"], row["nm_id"]))
    return rows


def build_marketplace_article_rows(*, db: Session, store_owner: User, marketplace: str) -> list[dict[str, Any]]:
    """Product list with the owner's cost; an Ozon SKU without a saved cost stays null (unknown)."""
    user_id = str(store_owner.id)
    rows: list[dict[str, Any]] = []
    if marketplace in {"ozon", "all"}:
        catalogue = {str(row.sku): row for row in db.query(OzonProduct).filter(OzonProduct.user_id == user_id).all()}
        costs = {str(row.sku): row for row in db.query(OzonProductCost).filter(OzonProductCost.user_id == user_id).all()}
        skus = set(catalogue) | set(costs)
        # Owner decision (2026-10-25): SKUs without a catalogue card are left out, as in the reports; a SKU that
        # already has a saved cost stays listed so the cost can still be changed or cleared.
        for state in (
            db.query(OzonSyncDay)
            .filter(OzonSyncDay.user_id == user_id, OzonSyncDay.status == "complete")
            .all()
            if not catalogue
            else []
        ):
            for product in state.products_data or []:
                if isinstance(product, dict) and str(product.get("sku") or "").strip():
                    skus.add(str(product["sku"]).strip())
        for sku in sorted(skus):
            item = catalogue.get(sku)
            cost = costs.get(sku)
            rows.append(
                {
                    "nm_id": sku,
                    "marketplace": "ozon",
                    "vendor_code": item.offer_id if item is not None else None,
                    "name": item.name if item is not None else None,
                    "subject_name": None,
                    "cost_price": _money(cost.cost_price) if cost is not None else None,
                }
            )
    if marketplace == "all":
        for article in db.query(Article).filter(Article.user_id == user_id).order_by(Article.nm_id.asc()).all():
            rows.append(
                {
                    "nm_id": str(article.nm_id),
                    "marketplace": "wb",
                    "vendor_code": article.vendor_code,
                    "name": article.name,
                    "subject_name": article.subject_name,
                    "cost_price": _money(article.cost_price),
                }
            )
    return rows


def list_ozon_operational_expenses(
    *, db: Session, user_id: str, date_from: date, date_to: date
) -> list[dict[str, Any]]:
    expenses = _ozon_expenses_by_date(db=db, user_id=user_id, date_from=date_from, date_to=date_to)
    return [{"date": day.isoformat(), "amount": _money(amount)} for day, amount in sorted(expenses.items())]
