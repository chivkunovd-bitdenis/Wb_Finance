import logging
from datetime import date, datetime, timedelta, timezone

from celery import chord
from fastapi import APIRouter, Depends, Body, HTTPException, status

from app.dependencies import get_current_user, get_store_context
from app.models.user import User
from app.models.funnel_backfill_state import FunnelBackfillState
from app.schemas.sync import (
    SyncSalesRequest,
    SyncFunnelRequest,
    SyncTaskResponse,
    SyncBatchResponse,
    FolderMigrationRequest,
    FolderMigrationResponse,
)
from app.services.folder_migration import run_folder_migration
from app.db import get_db
from sqlalchemy.orm import Session

# Импорт задачи Celery — по имени, чтобы воркер видел ту же задачу
from celery_app.tasks import (
    after_initial_sync_enqueue_funnel,
    after_period_sync_enqueue_funnel,
    sync_sales,
    sync_ads,
    sync_funnel,
    sync_funnel_ytd_step,
    recalculate_pnl,
    recalculate_sku_daily,
)
from app.services.store_access_service import StoreContext

router = APIRouter(prefix="/sync", tags=["sync"])
logger = logging.getLogger(__name__)

_QUEUE_UNAVAILABLE = (
    "Очередь фоновых задач недоступна (Redis или celery_worker). "
    "Синхронизация не запущена. Администратору: проверьте `docker compose ps` — должны быть "
    "запущены сервисы redis и celery_worker."
)


def _chord_or_503(header: list, body, *, context: str):
    """
    Постановка chord(header)(body) в очередь.

    В Celery 5 вызов chord(header)(body) уже выполняет apply_async и возвращает
    AsyncResult; повторный .apply_async() не нужен и даёт AttributeError.
    """
    try:
        return chord(header)(body)
    except Exception as exc:
        logger.exception("Celery chord failed (%s): %s", context, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_QUEUE_UNAVAILABLE,
        ) from exc


def _delay_or_503(task, context: str, *args, **kwargs):
    try:
        return task.delay(*args, **kwargs)
    except Exception as exc:
        logger.exception("Celery delay failed (%s): %s", context, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_QUEUE_UNAVAILABLE,
        ) from exc


def _require_wb_key(store_user: User):
    if not store_user.wb_api_key or not store_user.wb_api_key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="WB API ключ не задан. Добавь ключ при регистрации или в профиле.",
        )


def _month_end(d: date) -> date:
    """Последний день месяца для даты d."""
    if d.month == 12:
        first_next = date(d.year + 1, 1, 1)
    else:
        first_next = date(d.year, d.month + 1, 1)
    return first_next - timedelta(days=1)


def _month_chunks(start: date, end: date) -> list[tuple[str, str]]:
    """
    Разбить [start, end] на чанки по месяцам.
    Возвращает список пар (date_from, date_to) в isoformat.
    """
    if end < start:
        return []
    chunks: list[tuple[str, str]] = []
    cur = start
    while cur <= end:
        me = _month_end(cur)
        dt = me if me <= end else end
        chunks.append((cur.isoformat(), dt.isoformat()))
        cur = dt + timedelta(days=1)
    return chunks


@router.post("/sales", response_model=SyncTaskResponse)
def trigger_sync_sales(
    body: SyncSalesRequest,
    store_ctx: StoreContext = Depends(get_store_context),
):
    """Поставить в очередь задачу синхронизации продаж с WB за период."""
    store_user = store_ctx.store_owner
    _require_wb_key(store_user)
    result = _delay_or_503(
        sync_sales,
        "sync_sales",
        str(store_user.id),
        body.date_from,
        body.date_to,
    )
    return SyncTaskResponse(
        task_id=result.id,
        message="Задача синхронизации продаж поставлена в очередь.",
    )


@router.post("/ads", response_model=SyncTaskResponse)
def trigger_sync_ads(
    body: SyncSalesRequest,
    store_ctx: StoreContext = Depends(get_store_context),
):
    """Поставить в очередь задачу синхронизации рекламы с WB за период."""
    store_user = store_ctx.store_owner
    _require_wb_key(store_user)
    result = _delay_or_503(
        sync_ads,
        "sync_ads",
        str(store_user.id),
        body.date_from,
        body.date_to,
    )
    return SyncTaskResponse(
        task_id=result.id,
        message="Задача синхронизации рекламы поставлена в очередь.",
    )


@router.post("/funnel", response_model=SyncTaskResponse)
def trigger_sync_funnel(
    body: SyncFunnelRequest | None = Body(None),
    store_ctx: StoreContext = Depends(get_store_context),
):
    """Поставить в очередь задачу синхронизации воронки. Период опционален — по умолчанию последние 7 дней."""
    store_user = store_ctx.store_owner
    _require_wb_key(store_user)
    if body is None:
        body = SyncFunnelRequest()
    result = _delay_or_503(sync_funnel, "sync_funnel", str(store_user.id), body.date_from, body.date_to)
    return SyncTaskResponse(
        task_id=result.id,
        message="Задача синхронизации воронки поставлена в очередь.",
    )


@router.post("/period", response_model=SyncTaskResponse)
def trigger_sync_period(
    body: SyncSalesRequest,
    store_ctx: StoreContext = Depends(get_store_context),
):
    """
    Синхронизация финансов за произвольный период.

    Воронку автоматически не запускаем для произвольных периодов: она ограничена rolling-window
    последних 7 дней, чтобы не забивать WB и очередь.
    """
    store_user = store_ctx.store_owner
    _require_wb_key(store_user)
    result = _delay_or_503(sync_sales, "sync_period_sales", str(store_user.id), body.date_from, body.date_to)
    return SyncTaskResponse(
        task_id=result.id,
        message="Синхронизация финансового периода поставлена в очередь (sales).",
    )


@router.post("/funnel/backfill-ytd", response_model=SyncTaskResponse)
def trigger_funnel_backfill_ytd(
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
):
    """
    Фоновая догрузка воронки с 1 января текущего года до вчера.
    Использует POST /analytics/v3/sales-funnel/products по одному дню (агрегаты), чанки nmIds.
    """
    store_user = store_ctx.store_owner
    _require_wb_key(store_user)
    y = 2026
    row = (
        db.query(FunnelBackfillState)
        .filter(
            FunnelBackfillState.user_id == store_user.id,
            FunnelBackfillState.calendar_year == y,
        )
        .first()
    )
    if row and row.status == "running" and row.updated_at is not None:
        updated = row.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - updated
        if age.total_seconds() < 120:
            return SyncTaskResponse(
                task_id="running",
                message="Догрузка воронки уже идёт — данные появятся по мере готовности.",
            )
    result = _delay_or_503(sync_funnel_ytd_step, "sync_funnel_ytd_step", str(store_user.id), y)
    return SyncTaskResponse(
        task_id=result.id,
        message=f"Запущена фоновая догрузка воронки за {y} год.",
    )


@router.post("/recalculate", response_model=SyncTaskResponse)
def trigger_recalculate(
    body: SyncSalesRequest,
    store_ctx: StoreContext = Depends(get_store_context),
):
    """Поставить в очередь пересчёт pnl_daily и sku_daily за период (без синка с WB)."""
    store_user = store_ctx.store_owner
    _delay_or_503(recalculate_pnl, "recalculate_pnl", str(store_user.id), body.date_from, body.date_to)
    result = _delay_or_503(
        recalculate_sku_daily,
        "recalculate_sku_daily",
        str(store_user.id),
        body.date_from,
        body.date_to,
    )
    return SyncTaskResponse(
        task_id=result.id,
        message="Задачи пересчёта P&L и витрины по артикулам поставлены в очередь.",
    )


@router.post("/initial", response_model=SyncTaskResponse)
def trigger_initial_sync(
    store_ctx: StoreContext = Depends(get_store_context),
):
    """
    Первая синхронизация для нового пользователя.

    Стратегия как в GAS:
    - берём последние 30 дней (от вчера назад);
    - ставим в очередь sync_sales по этому периоду;
    - после sales ставим sync_funnel только за последние 7 дней;
    - по завершении sync_sales Celery вызовет пересчёт витрин (pnl_daily, sku_daily).

    Endpoint возвращает только факт постановки задач; фронт может опрашивать /dashboard/state,
    чтобы понять, когда has_data и has_funnel станут True.
    """
    store_user = store_ctx.store_owner
    _require_wb_key(store_user)
    today = date.today()
    date_to = today - timedelta(days=1)
    date_from = date_to - timedelta(days=29)

    df = date_from.isoformat()
    dt = date_to.isoformat()

    user_id = str(store_user.id)
    # chord: нельзя .delay(sync_funnel) сразу — воронке нужны articles из sales.
    async_result = _chord_or_503(
        [sync_sales.s(user_id, df, dt)],
        after_initial_sync_enqueue_funnel.s(user_id),
        context="sync_initial",
    )

    return SyncTaskResponse(
        task_id=async_result.id,
        message=f"Первая синхронизация поставлена в очередь: {df} — {dt} (затем воронка).",
    )


@router.post("/recent", response_model=SyncTaskResponse)
def trigger_recent_sync(
    store_ctx: StoreContext = Depends(get_store_context),
):
    """
    Автосинк для сценария «не первый вход».

    Стратегия:
    - каждый вход пользователя сначала обновляет финансовый хвост последних 7 дней (включая вчера);
    - только после успешного sales ставим sync_funnel по этому же 7-дневному окну;
    - рекламу не трогаем в автосинке, чтобы не блокировать финансы и не сжигать лимиты WB.

    Окно 7 дней совпадает с дефолтным окном воронки и логикой GAS.
    """
    store_user = store_ctx.store_owner
    _require_wb_key(store_user)
    today = date.today()
    date_to = today - timedelta(days=1)
    date_from = date_to - timedelta(days=6)

    df = date_from.isoformat()
    dt = date_to.isoformat()

    user_id = str(store_user.id)
    async_result = _chord_or_503(
        [sync_sales.s(user_id, df, dt)],
        after_period_sync_enqueue_funnel.s(user_id, df, dt),
        context="sync_recent",
    )

    return SyncTaskResponse(
        task_id=async_result.id,
        message=f"Автосинхронизация последних 7 дней поставлена в очередь: {df} — {dt}.",
    )


@router.post("/backfill/2026", response_model=SyncBatchResponse)
def trigger_backfill_2026(
    store_ctx: StoreContext = Depends(get_store_context),
):
    """
    Догрузка «основного диапазона» 2026 (как в GAS triggerLoad2026ThenMaybe2025):
    - период: 2026-01-01 .. вчера;
    - продажи ставятся в очередь чанками по месяцам (чтобы задачи были стабильнее);
    - реклама и историческая воронка не запускаются автоматически.
    """
    store_user = store_ctx.store_owner
    _require_wb_key(store_user)
    today = date.today()
    date_to = today - timedelta(days=1)
    date_from = date(2026, 1, 1)

    if date_to < date_from:
        return SyncBatchResponse(
            task_ids=[],
            message="Догрузка 2026 не требуется: текущая дата меньше начала 2026 года.",
        )

    user_id = str(store_user.id)
    chunks = _month_chunks(date_from, date_to)
    task_ids: list[str] = []
    for df, dt in chunks:
        r1 = _delay_or_503(sync_sales, "backfill_2026_sales", user_id, df, dt)
        task_ids.append(r1.id)

    return SyncBatchResponse(
        task_ids=task_ids,
        message=f"Догрузка финансов 2026 поставлена в очередь ({len(chunks)} мес.): 2026-01-01 — {date_to.isoformat()}",
    )


@router.post("/backfill/2025", response_model=SyncBatchResponse)
def trigger_backfill_2025(
    store_ctx: StoreContext = Depends(get_store_context),
):
    """
    Догрузка архива 2025 по месяцам (как в GAS loadNextArchiveChunk / apiLoadArchiveChunk).
    Период: 2025-01-01 .. 2025-12-31. Автоматически грузим только sales.
    """
    store_user = store_ctx.store_owner
    _require_wb_key(store_user)
    date_from = date(2025, 1, 1)
    date_to = date(2025, 12, 31)
    user_id = str(store_user.id)
    chunks = _month_chunks(date_from, date_to)
    task_ids: list[str] = []
    for df, dt in chunks:
        r1 = _delay_or_503(sync_sales, "backfill_2025_sales", user_id, df, dt)
        task_ids.append(r1.id)
    return SyncBatchResponse(
        task_ids=task_ids,
        message=f"Догрузка финансового архива 2025 поставлена в очередь ({len(chunks)} мес.): 2025-01-01 — 2025-12-31",
    )


@router.post("/migrate/folder", response_model=FolderMigrationResponse)
def trigger_folder_migration(
    body: FolderMigrationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Импорт CSV/XLSX из локальной папки в БД.
    filename_regex обязан содержать named group user_email.
    Для CSV дополнительно нужен group dataset (sales|ads).
    Для XLSX используются листы DB_Raw_Data и DB_Ads_Raw.
    Можно включить auto_create_users для отсутствующих email.
    По умолчанию dry_run=True (только валидация/сверка, без записи в БД).
    """
    try:
        return run_folder_migration(db, current_user, body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
