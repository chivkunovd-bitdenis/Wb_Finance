from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_store_context
from app.services.marketplace_finance_service import (
    MarketplaceFinanceError,
    build_overview,
    connection_status,
    enqueue_sync,
    save_connection,
    set_product_cost,
)
from app.services.store_access_service import StoreContext
from celery_app.ozon_tasks import ozon_sync_run

router = APIRouter(prefix="/dashboard/marketplace", tags=["marketplace-finance"])


class OzonConnectionRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=255)
    api_key: SecretStr = Field(min_length=1, max_length=4096)


class ProductCostRequest(BaseModel):
    cost_price: Decimal | None


class OzonSyncRequest(BaseModel):
    date_from: date
    date_to: date


def _require_owner(store: StoreContext) -> str:
    if not store.is_owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Изменять настройки Ozon может только владелец магазина.")
    return str(store.store_owner.id)


def _translate_service_error(exc: MarketplaceFinanceError) -> HTTPException:
    message = str(exc)
    lowered = message.lower()
    code = status.HTTP_422_UNPROCESSABLE_CONTENT
    safe_message = message
    translations = (
        ("date_to must be on or after date_from", "Дата окончания должна быть не раньше даты начала."),
        ("manual ozon sync can cover at most", "За один раз можно синхронизировать не более 31 дня."),
        ("connect ozon before starting a sync", "Сначала подключите Ozon в настройках."),
        ("marketplace must be ozon or all", "Выберите Ozon или все площадки."),
        ("синхронизация уже ожидает", "Синхронизация уже ожидает запуска или выполняется."),
        ("client_id and api_key are required", "Укажите Client-Id и API-ключ Ozon."),
        ("ozon is not connected", "Подключение Ozon не настроено."),
        ("server encryption is not configured", "На сервере не настроено шифрование подключений."),
        ("cannot be decrypted", "Не удалось расшифровать подключение; проверьте настройки шифрования сервера."),
        ("encryption configuration", "Не удалось расшифровать подключение; проверьте настройки шифрования сервера."),
        ("ozon is not connected", "Подключение Ozon не настроено."),
        ("cost_price must contain", "Артикул Ozon должен содержать от 1 до 64 символов."),
        ("cost_price cannot be negative", "Себестоимость не может быть отрицательной."),
    )
    for source, translated in translations:
        if source in lowered:
            safe_message = translated
            break
    if not any(ord(char) > 127 for char in message):
        safe_message = safe_message if safe_message != message else "Не удалось выполнить запрос. Проверьте данные и попробуйте снова."
    if "Connect Ozon" in message or "not connected" in message or "синхронизация уже ожидает" in lowered:
        code = status.HTTP_409_CONFLICT
    if "encryption" in message.lower() or "decrypted" in message.lower() or "шифрован" in message.lower() or "расшифров" in message.lower():
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HTTPException(status_code=code, detail=safe_message)


@router.get("/overview")
def marketplace_overview(
    marketplace: str = Query(..., pattern="^(ozon|all)$"),
    date_from: date = Query(...),
    date_to: date = Query(...),
    store: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return build_overview(
            db=db,
            store_owner=store.store_owner,
            marketplace=marketplace,
            date_from=date_from,
            date_to=date_to,
        )
    except MarketplaceFinanceError as exc:
        raise _translate_service_error(exc) from exc


@router.get("/connection")
def get_ozon_connection(
    store: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> dict:
    return connection_status(db=db, user_id=str(store.store_owner.id))


@router.put("/connection")
def put_ozon_connection(
    body: OzonConnectionRequest,
    store: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> dict:
    user_id = _require_owner(store)
    try:
        save_connection(
            db=db,
            user_id=user_id,
            client_id=body.client_id,
            api_key=body.api_key.get_secret_value(),
        )
    except MarketplaceFinanceError as exc:
        raise _translate_service_error(exc) from exc
    return connection_status(db=db, user_id=user_id)


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
def post_ozon_sync(
    body: OzonSyncRequest,
    store: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> dict:
    user_id = _require_owner(store)
    try:
        run = enqueue_sync(db=db, user_id=user_id, date_from=body.date_from, date_to=body.date_to)
    except MarketplaceFinanceError as exc:
        raise _translate_service_error(exc) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Синхронизация Ozon уже ожидает запуска или выполняется.") from exc
    try:
        # Only the database run ID enters the broker; credentials are loaded by the worker.
        ozon_sync_run.delay(str(run.id))
    except Exception as exc:
        run.status = "failed"
        run.error_message = "Очередь синхронизации недоступна; попробуйте позже."
        db.add(run)
        db.commit()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Очередь синхронизации недоступна; попробуйте позже.") from exc
    return {"status": "queued", "run_id": str(run.id)}


@router.put("/products/{sku}/cost")
def put_ozon_product_cost(
    sku: str,
    body: ProductCostRequest,
    store: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
) -> dict:
    user_id = _require_owner(store)
    if body.cost_price is not None:
        try:
            amount = Decimal(body.cost_price)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Укажите конечную сумму себестоимости или null, чтобы её очистить.") from exc
        if not amount.is_finite() or amount < 0 or amount > Decimal("999999999999.99"):
            raise HTTPException(status_code=422, detail="Себестоимость должна быть от 0 до 999999999999.99 руб.")
    else:
        amount = None
    try:
        saved = set_product_cost(db=db, user_id=user_id, sku=sku, cost_price=amount)
    except MarketplaceFinanceError as exc:
        raise _translate_service_error(exc) from exc
    return {
        "sku": sku.strip(),
        "cost_price": float(saved) if saved is not None else None,
        "message": (
            "Себестоимость очищена."
            if saved is None
            else "Себестоимость сохранена. Прибыль пересчитается, когда появится подтверждённое количество продаж."
        ),
    }
