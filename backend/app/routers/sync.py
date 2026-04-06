import logging
from datetime import date, datetime, timedelta, timezone

from celery import chord
from fastapi import APIRouter, Depends, Body, HTTPException, status

from app.dependencies import get_current_user
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


def _require_wb_key(current_user: User):
    if not current_user.wb_api_key or not current_user.wb_api_key.strip():
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
    current_user: User = Depends(get_current_user),
):
    """Поставить в очередь задачу синхронизации продаж с WB за период."""
    _require_wb_key(current_user)
    result = _delay_or_503(
        sync_sales,
        "sync_sales",
        str(current_user.id),
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
    current_user: User = Depends(get_current_user),
):
    """Поставить в очередь задачу синхронизации рекламы с WB за период."""
    _require_wb_key(current_user)
    result = _delay_or_503(
        sync_ads,
        "sync_ads",
        str(current_user.id),
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
    current_user: User = Depends(get_current_user),
):
    """Поставить в очередь задачу синхронизации воронки. Период опционален — по умолчанию последние 7 дней."""
    _require_wb_key(current_user)
    if body is None:
        body = SyncFunnelRequest()
    result = _delay_or_503(sync_funnel, "sync_funnel", str(current_user.id), body.date_from, body.date_to)
    return SyncTaskResponse(
        task_id=result.id,
        message="Задача синхронизации воронки поставлена в очередь.",
    )


@router.post("/period", response_model=SyncTaskResponse)
def trigger_sync_period(
    body: SyncSalesRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Синхронизация продаж+рекламы+воронки за произвольный период, в правильном порядке:
    sync_sales + sync_ads -> sync_funnel (чтобы articles успели заполниться).
    """
    _require_wb_key(current_user)
    user_id = str(current_user.id)
    async_result = _chord_or_503(
        [
            sync_sales.s(user_id, body.date_from, body.date_to),
            sync_ads.s(user_id, body.date_from, body.date_to),
        ],
        after_period_sync_enqueue_funnel.s(user_id, body.date_from, body.date_to),
        context="sync_period",
    )
    return SyncTaskResponse(
        task_id=async_result.id,
        message="Синхронизация периода поставлена в очередь (sales+ads, затем funnel).",
    )


@router.post("/funnel/backfill-ytd", response_model=SyncTaskResponse)
def trigger_funnel_backfill_ytd(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Фоновая догрузка воронки с 1 января текущего года до вчера.
    Использует POST /analytics/v3/sales-funnel/products по одному дню (агрегаты), чанки nmIds.
    """
    _require_wb_key(current_user)
    y = date.today().year
    row = (
        db.query(FunnelBackfillState)
        .filter(
            FunnelBackfillState.user_id == current_user.id,
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
    result = _delay_or_503(sync_funnel_ytd_step, "sync_funnel_ytd_step", str(current_user.id), y)
    return SyncTaskResponse(
        task_id=result.id,
        message=f"Запущена фоновая догрузка воронки за {y} год.",
    )


@router.post("/recalculate", response_model=SyncTaskResponse)
def trigger_recalculate(
    body: SyncSalesRequest,
    current_user: User = Depends(get_current_user),
):
    """Поставить в очередь пересчёт pnl_daily и sku_daily за период (без синка с WB)."""
    _delay_or_503(recalculate_pnl, "recalculate_pnl", str(current_user.id), body.date_from, body.date_to)
    result = _delay_or_503(
        recalculate_sku_daily,
        "recalculate_sku_daily",
        str(current_user.id),
        body.date_from,
        body.date_to,
    )
    return SyncTaskResponse(
        task_id=result.id,
        message="Задачи пересчёта P&L и витрины по артикулам поставлены в очередь.",
    )


@router.post("/initial", response_model=SyncTaskResponse)
def trigger_initial_sync(
    current_user: User = Depends(get_current_user),
):
    """
    Первая синхронизация для нового пользователя.

    Стратегия как в GAS:
    - берём последние 30 дней (от вчера назад);
    - ставим в очередь sync_sales и sync_ads по этому периоду;
    - после обеих задач ставим sync_funnel (nm_id берутся из articles, которые заполняются sales/ads);
    - по завершении sync_sales/sync_ads Celery вызовет пересчёт витрин (pnl_daily, sku_daily).

    Endpoint возвращает только факт постановки задач; фронт может опрашивать /dashboard/state,
    чтобы понять, когда has_data и has_funnel станут True.
    """
    _require_wb_key(current_user)
    today = date.today()
    date_to = today - timedelta(days=1)
    date_from = date_to - timedelta(days=29)

    df = date_from.isoformat()
    dt = date_to.isoformat()

    user_id = str(current_user.id)
    # chord: нельзя.delay(sync_funnel) сразу — воронке нужны articles из sales/ads
    async_result = _chord_or_503(
        [sync_sales.s(user_id, df, dt), sync_ads.s(user_id, df, dt)],
        after_initial_sync_enqueue_funnel.s(user_id),
        context="sync_initial",
    )

    return SyncTaskResponse(
        task_id=async_result.id,
        message=f"Первая синхронизация поставлена в очередь: {df} — {dt} (затем воронка).",
    )


@router.post("/recent", response_model=SyncTaskResponse)
def trigger_recent_sync(
    current_user: User = Depends(get_current_user),
):
    """
    Автосинк для сценария «не первый вход».

    Стратегия:
    - каждый вход пользователя мы обновляем «хвост» последних 7 дней (включая вчера);
    - ставим в очередь sync_sales, sync_ads и sync_funnel по этому окну;
    - пересчёт витрин (pnl_daily, sku_daily) произойдёт из задач sync_sales/sync_ads.

    Окно 7 дней совпадает с дефолтным окном воронки и логикой GAS.
    """
    _require_wb_key(current_user)
    today = date.today()
    date_to = today - timedelta(days=1)
    date_from = date_to - timedelta(days=6)

    df = date_from.isoformat()
    dt = date_to.isoformat()

    user_id = str(current_user.id)
    async_result = _chord_or_503(
        [sync_sales.s(user_id, df, dt), sync_ads.s(user_id, df, dt)],
        after_period_sync_enqueue_funnel.s(user_id, df, dt),
        context="sync_recent",
    )

    return SyncTaskResponse(
        task_id=async_result.id,
        message=f"Автосинхронизация последних 7 дней поставлена в очередь: {df} — {dt}.",
    )


@router.post("/backfill/2026", response_model=SyncBatchResponse)
def trigger_backfill_2026(
    current_user: User = Depends(get_current_user),
):
    """
    Догрузка «основного диапазона» 2026 (как в GAS triggerLoad2026ThenMaybe2025):
    - период: 2026-01-01 .. вчера;
    - продажи и реклама ставятся в очередь чанками по месяцам (чтобы задачи были стабильнее);
    - воронка ставится отдельно на окно последних 7 дней (по умолчанию в задаче именно так).
    """
    _require_wb_key(current_user)
    today = date.today()
    date_to = today - timedelta(days=1)
    date_from = date(2026, 1, 1)

    if date_to < date_from:
        return SyncBatchResponse(
            task_ids=[],
            message="Догрузка 2026 не требуется: текущая дата меньше начала 2026 года.",
        )

    user_id = str(current_user.id)
    chunks = _month_chunks(date_from, date_to)
    task_ids: list[str] = []
    for df, dt in chunks:
        r1 = _delay_or_503(sync_sales, "backfill_2026_sales", user_id, df, dt)
        r2 = _delay_or_503(sync_ads, "backfill_2026_ads", user_id, df, dt)
        task_ids.extend([r1.id, r2.id])

    # Воронка — отдельной задачей (окно 7 дней), как в GAS
    rf = _delay_or_503(sync_funnel, "backfill_2026_funnel", user_id, None, None)
    task_ids.append(rf.id)

    return SyncBatchResponse(
        task_ids=task_ids,
        message=f"Догрузка 2026 поставлена в очередь ({len(chunks)} мес. + воронка): 2026-01-01 — {date_to.isoformat()}",
    )


@router.post("/backfill/2025", response_model=SyncBatchResponse)
def trigger_backfill_2025(
    current_user: User = Depends(get_current_user),
):
    """
    Догрузка архива 2025 по месяцам (как в GAS loadNextArchiveChunk / apiLoadArchiveChunk).
    Период: 2025-01-01 .. 2025-12-31. Задачи sync_sales и sync_ads по каждому месяцу.
    """
    _require_wb_key(current_user)
    date_from = date(2025, 1, 1)
    date_to = date(2025, 12, 31)
    user_id = str(current_user.id)
    chunks = _month_chunks(date_from, date_to)
    task_ids: list[str] = []
    for df, dt in chunks:
        r1 = _delay_or_503(sync_sales, "backfill_2025_sales", user_id, df, dt)
        r2 = _delay_or_503(sync_ads, "backfill_2025_ads", user_id, df, dt)
        task_ids.extend([r1.id, r2.id])
    return SyncBatchResponse(
        task_ids=task_ids,
        message=f"Догрузка архива 2025 поставлена в очередь ({len(chunks)} мес.): 2025-01-01 — 2025-12-31",
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
