import logging
from datetime import date, datetime, timedelta, timezone

from celery import chord
from fastapi import APIRouter, Depends, Body, HTTPException, status

from app.dependencies import get_current_user, get_store_context
from app.models.user import User
from app.models.funnel_backfill_state import FunnelBackfillState
from app.models.finance_missing_sync_state import FinanceMissingSyncState
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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# Импорт задачи Celery — по имени, чтобы воркер видел ту же задачу
from celery_app.tasks import (
    sync_funnel_ytd_step,
    recalculate_pnl,
    recalculate_sku_daily,
    wb_orchestrator_kick,
)
from app.services.store_access_service import StoreContext

router = APIRouter(prefix="/sync", tags=["sync"])
logger = logging.getLogger(__name__)

_QUEUE_UNAVAILABLE = (
    "Очередь фоновых задач недоступна (Redis или celery_worker). "
    "Синхронизация не запущена. Администратору: проверьте `docker compose ps` — должны быть "
    "запущены сервисы redis и celery_worker."
)
_SALES_SYNC_RUNNING_TTL = timedelta(minutes=10)


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


def _sales_retry_block_message(db: Session, *, user_id: str, date_from: str, date_to: str) -> str | None:
    """
    Если WB уже вернул retry-after по sales для пересекающегося диапазона,
    не ставим новый sales sync при каждом входе/refresh.
    """
    try:
        df = date.fromisoformat(date_from)
        dt = date.fromisoformat(date_to)
    except ValueError:
        return None

    now_dt = datetime.now(timezone.utc)
    row = (
        db.query(FinanceMissingSyncState)
        .filter(
            FinanceMissingSyncState.user_id == user_id,
            FinanceMissingSyncState.date_from <= dt,
            FinanceMissingSyncState.date_to >= df,
        )
        .order_by(FinanceMissingSyncState.updated_at.desc())
        .first()
    )
    status = getattr(row, "status", None)
    updated_at = getattr(row, "updated_at", None)
    if status in {"queued", "running"} and updated_at is not None:
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        if now_dt - updated_at <= _SALES_SYNC_RUNNING_TTL:
            return "WB sales sync уже поставлен или выполняется; повторный запуск пропущен, чтобы не плодить запросы к WB."

    next_run_at = getattr(row, "next_run_at", None)
    if next_run_at is None:
        return None
    if next_run_at.tzinfo is None:
        next_run_at = next_run_at.replace(tzinfo=timezone.utc)
    if next_run_at <= now_dt:
        return None
    minutes = max(1, int((next_run_at - now_dt).total_seconds() // 60))
    return f"WB sales временно ограничил запросы; повторная попытка уже запланирована примерно через {minutes} мин."


def _reserve_sales_sync_state(db: Session, *, user_id: str, date_from: str, date_to: str) -> None:
    try:
        df = date.fromisoformat(date_from)
        dt = date.fromisoformat(date_to)
    except ValueError:
        return

    state = (
        db.query(FinanceMissingSyncState)
        .filter(
            FinanceMissingSyncState.user_id == user_id,
            FinanceMissingSyncState.date_from == df,
            FinanceMissingSyncState.date_to == dt,
        )
        .first()
    )
    if state is None:
        state = FinanceMissingSyncState(
            user_id=user_id,
            date_from=df,
            date_to=dt,
            status="running",
        )
        db.add(state)
    else:
        state.status = "running"
        state.next_run_at = None
        state.error_message = None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


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
    """Запросить синхронизацию продаж с WB за период (через оркестратор)."""
    store_user = store_ctx.store_owner
    _require_wb_key(store_user)
    user_id = str(store_user.id)
    result = _delay_or_503(
        wb_orchestrator_kick,
        "wb_orchestrator_kick_sales",
        user_id,
        {"high": {"finance_range": {"date_from": body.date_from, "date_to": body.date_to}}},
    )
    return SyncTaskResponse(
        task_id=getattr(result, "id", "orchestrator"),
        message="Запрошена синхронизация продаж (оркестратор).",
    )


@router.post("/ads", response_model=SyncTaskResponse)
def trigger_sync_ads(
    body: SyncSalesRequest,
    store_ctx: StoreContext = Depends(get_store_context),
):
    """Запросить синхронизацию рекламы с WB за период (через оркестратор)."""
    store_user = store_ctx.store_owner
    _require_wb_key(store_user)
    user_id = str(store_user.id)
    result = _delay_or_503(
        wb_orchestrator_kick,
        "wb_orchestrator_kick_ads",
        user_id,
        {"high": {"finance_range": {"date_from": body.date_from, "date_to": body.date_to}}},
    )
    return SyncTaskResponse(
        task_id=getattr(result, "id", "orchestrator"),
        message="Запрошена синхронизация рекламы (оркестратор).",
    )


@router.post("/funnel", response_model=SyncTaskResponse)
def trigger_sync_funnel(
    body: SyncFunnelRequest | None = Body(None),
    store_ctx: StoreContext = Depends(get_store_context),
):
    """Запросить починку хвоста воронки (последние 7 дней) через оркестратор."""
    store_user = store_ctx.store_owner
    _require_wb_key(store_user)
    user_id = str(store_user.id)
    result = _delay_or_503(
        wb_orchestrator_kick,
        "wb_orchestrator_kick_funnel_tail",
        user_id,
        {"high": {"funnel_tail": True}},
    )
    return SyncTaskResponse(
        task_id=getattr(result, "id", "orchestrator"),
        message="Запрошена починка хвоста воронки (оркестратор).",
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
    user_id = str(store_user.id)
    result = _delay_or_503(
        wb_orchestrator_kick,
        "wb_orchestrator_kick_period",
        user_id,
        {"high": {"finance_range": {"date_from": body.date_from, "date_to": body.date_to}}},
    )
    return SyncTaskResponse(
        task_id=getattr(result, "id", "orchestrator"),
        message="Запрошена синхронизация финансового периода (оркестратор).",
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
    async_result = _delay_or_503(
        wb_orchestrator_kick,
        "wb_orchestrator_kick_initial",
        user_id,
        {
            "high": {
                "finance_range": {"date_from": df, "date_to": dt},
                "funnel_tail": True,
            },
            "low": {
                "finance_backfill_year": 2026,
            },
        },
    )

    return SyncTaskResponse(
        task_id=getattr(async_result, "id", "orchestrator"),
        message=f"Первая синхронизация запрошена через оркестратор: {df} — {dt} (и хвост воронки).",
    )


@router.post("/recent", response_model=SyncTaskResponse)
def trigger_recent_sync(
    db: Session = Depends(get_db),
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
    # Оставляем UI-блокировку как подсказку пользователю, но фактический ретрай теперь централизован.
    blocked = _sales_retry_block_message(db, user_id=user_id, date_from=df, date_to=dt)
    if blocked is not None:
        return SyncTaskResponse(task_id="wb-sales-retry-scheduled", message=blocked)
    _reserve_sales_sync_state(db, user_id=user_id, date_from=df, date_to=dt)
    async_result = _delay_or_503(
        wb_orchestrator_kick,
        "wb_orchestrator_kick_recent",
        user_id,
        {"high": {"finance_range": {"date_from": df, "date_to": dt}, "funnel_tail": True}},
    )

    return SyncTaskResponse(
        task_id=getattr(async_result, "id", "orchestrator"),
        message=f"Автосинхронизация последних 7 дней запрошена через оркестратор: {df} — {dt}.",
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
    r = _delay_or_503(
        wb_orchestrator_kick,
        "wb_orchestrator_kick_backfill_2026",
        user_id,
        {"low": {"finance_backfill_year": 2026}},
    )
    return SyncBatchResponse(
        task_ids=[getattr(r, "id", "orchestrator")],
        message=f"Догрузка финансов 2026 запрошена через оркестратор: 2026-01-01 — {date_to.isoformat()}",
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
    user_id = str(store_user.id)
    r = _delay_or_503(
        wb_orchestrator_kick,
        "wb_orchestrator_kick_backfill_2025",
        user_id,
        {"low": {"finance_backfill_year": 2025}},
    )
    return SyncBatchResponse(
        task_ids=[getattr(r, "id", "orchestrator")],
        message="Догрузка финансового архива 2025 запрошена через оркестратор: 2025-01-01 — 2025-12-31",
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
