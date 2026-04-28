"""
REST API для фронта: дашборд (P&L по дням), артикулы, воронка, time-series по SKU.
Все эндпоинты требуют JWT.
"""
from datetime import date as date_type
from datetime import timedelta
from datetime import datetime, timezone
from calendar import monthrange

from fastapi import APIRouter, Depends, Query, HTTPException, status

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_store_context
from app.models.user import User
from app.models.pnl_daily import PnlDaily
from app.models.article import Article
from app.models.funnel_backfill_state import FunnelBackfillState
from app.models.funnel_daily import FunnelDaily
from app.models.finance_backfill_state import FinanceBackfillState
from app.models.wb_orchestrator_state import WbOrchestratorState
from app.models.raw_sales import RawSale
from app.models.sku_daily import SkuDaily
from app.models.operational_expense import OperationalExpense
from app.models.monthly_plan import MonthlyPlan
from app.models.base import uuid_gen
import logging

from celery_app.tasks import sync_funnel_ytd_step, wb_orchestrator_kick
from app.models.finance_missing_sync_state import FinanceMissingSyncState
from app.services.finance_missing_tail import compute_missing_tail_range, compute_missing_ranges_in_window
from sqlalchemy.exc import IntegrityError
from app.schemas.dashboard import (
    PnlDayResponse,
    ArticleResponse,
    ArticleCostUpdate,
    FunnelDayResponse,
    SkuDayResponse,
    OperationalExpenseResponse,
    OperationalExpenseCreate,
    OperationalExpenseUpdate,
    PlanFactMonthRequest,
    PlanFactMonthResponse,
    PlanFactMonthMetricsResponse,
    PlanFactMetricRow,
)
from app.services.store_access_service import StoreContext


router = APIRouter(prefix="/dashboard", tags=["dashboard"])
logger = logging.getLogger(__name__)

FUNNEL_BACKFILL_START_DATE = date_type(2026, 1, 1)
FINANCE_MISSING_TAIL_LOOKBACK_DAYS = 45
FINANCE_MISSING_TAIL_COOLDOWN = timedelta(minutes=10)
FINANCE_HOLES_MAX_RANGES_PER_ENTRY = 1
FINANCE_HOLES_MAX_RANGE_DAYS = 7


def _repair_stuck_funnel_ytd_running(
    db: Session,
    user_id: str,
    calendar_year: int,
    *,
    max_age: timedelta = timedelta(minutes=30),
) -> None:
    """
    Сброс "залипшего" running: если задача помечена running, но давно не обновлялась (updated_at),
    то, скорее всего, воркер умер/задача потерялась. Без этого баннер может висеть сутками.
    """
    row = (
        db.query(FunnelBackfillState)
        .filter(
            FunnelBackfillState.user_id == user_id,
            FunnelBackfillState.calendar_year == calendar_year,
        )
        .first()
    )
    if not row or row.status != "running":
        return
    # updated_at заполняется в БД; если оно слишком старое — считаем running "битым".
    updated = row.updated_at
    if updated is None:
        return
    now = datetime.now(timezone.utc)
    if updated.tzinfo is None:
        # Safety net: на всякий случай считаем naive как UTC.
        updated = updated.replace(tzinfo=timezone.utc)
    if now - updated <= max_age:
        return
    row.status = "idle"
    # Сбрасываем маркеры, чтобы /dashboard/state мог заново автостартовать задачу.
    row.error_message = None
    db.add(row)
    db.commit()


def _repair_hollow_funnel_ytd(
    db: Session,
    user_id: str,
    calendar_year: int,
    year_start: date_type,
    through: date_type,
) -> None:
    """
    Сброс ложного complete: прогресс YTD доходил до конца года, а funnel_daily за период пуст
    (старый баг при пустых articles). Иначе фронт не вызывает POST и воркер не перезапускается.
    """
    if through < year_start:
        return
    row = (
        db.query(FunnelBackfillState)
        .filter(
            FunnelBackfillState.user_id == user_id,
            FunnelBackfillState.calendar_year == calendar_year,
        )
        .first()
    )
    if (
        not row
        or row.status != "complete"
        or not row.last_completed_date
        or row.last_completed_date < through
    ):
        return
    # Если задача помечена complete, но за вчера нет ни одной строки funnel_daily — это "ложный complete".
    has_yesterday = (
        db.query(FunnelDaily.id)
        .filter(FunnelDaily.user_id == user_id, FunnelDaily.date == through)
        .first()
        is not None
    )
    if has_yesterday:
        return
    row.status = "idle"
    row.last_completed_date = None
    row.error_message = None
    db.add(row)
    db.commit()


def _maybe_start_funnel_ytd_backfill(
    db: Session,
    user: User,
    calendar_year: int,
    year_start: date_type,
    through: date_type,
) -> None:
    """
    Автостарт YTD-догрузки прямо из /dashboard/state:
    - если нет данных за вчера в funnel_daily,
    - и задача сейчас не running/complete,
    ставим sync_funnel_ytd_step в очередь (таска сама сделает weekly→daily до 2026-01-01).
    """
    if through < year_start:
        return
    if not user.wb_api_key or not user.wb_api_key.strip():
        return

    # Запускаем, если за вчера нет данных funnel_daily (как было раньше).
    has_yesterday = (
        db.query(FunnelDaily.id)
        .filter(FunnelDaily.user_id == user.id, FunnelDaily.date == through)
        .first()
        is not None
    )
    if has_yesterday:
        return

    row = (
        db.query(FunnelBackfillState)
        .filter(
            FunnelBackfillState.user_id == user.id,
            FunnelBackfillState.calendar_year == calendar_year,
        )
        .first()
    )
    if row and row.status in {"running", "complete"}:
        return
    if row and row.error_message == "__autostart_scheduled__":
        return
    if row is None:
        row = FunnelBackfillState(
            user_id=user.id,
            calendar_year=calendar_year,
            status="idle",
            error_message="__autostart_scheduled__",
        )
    else:
        # Не перетираем реальную ошибку маркером автозапуска.
        if row.error_message in {None, "__autostart_scheduled__", "__retry_scheduled__"}:
            row.error_message = "__autostart_scheduled__"
    db.add(row)
    db.commit()
    try:
        sync_funnel_ytd_step.delay(str(user.id), calendar_year)
    except Exception as exc:
        # Очередь может быть недоступна (redis/celery_worker). Не валим /dashboard/state.
        logger.exception("Celery delay failed (sync_funnel_ytd_step): %s", exc)


def _kick_finance_range_with_funnel_tail(user_id: str, date_from: date_type, date_to: date_type) -> bool:
    """
    Warm-path contract: dashboard entry repairs finance first, then rolling funnel tail,
    through the single WB orchestrator instead of independent Celery fan-out.
    """
    df = date_from.isoformat()
    dt = date_to.isoformat()
    try:
        wb_orchestrator_kick.delay(
            user_id,
            {"high": {"finance_range": {"date_from": df, "date_to": dt}, "funnel_tail": True}},
        )
        return True
    except Exception as exc:
        logger.exception("Celery delay failed (wb_orchestrator_kick finance+funnel): %s", exc)
        return False


def _maybe_start_funnel_tail_repair(db: Session, user: User, through: date_type) -> bool:
    """
    Dashboard-entry contract: even if finance is already complete, missing funnel days in the
    rolling window must wake the single orchestrator and repair `funnel_daily` without a button.
    """
    if not user.wb_api_key or not user.wb_api_key.strip():
        return False

    window_from = through - timedelta(days=6)
    present_dates = {
        d
        for (d,) in (
            db.query(FunnelDaily.date)
            .filter(FunnelDaily.user_id == user.id, FunnelDaily.date >= window_from, FunnelDaily.date <= through)
            .distinct()
            .all()
        )
    }
    window_days = [window_from + timedelta(days=i) for i in range((through - window_from).days + 1)]
    if all(d in present_dates for d in window_days):
        return False

    orch = db.query(WbOrchestratorState).filter(WbOrchestratorState.user_id == str(user.id)).first()
    high = orch.intents.get("high", {}) if orch and isinstance(orch.intents, dict) else {}
    if isinstance(high, dict) and high.get("funnel_tail") is True:
        return False

    try:
        wb_orchestrator_kick.delay(str(user.id), {"high": {"funnel_tail": True}})
        return True
    except Exception as exc:
        logger.exception("Celery delay failed (wb_orchestrator_kick funnel_tail): %s", exc)
        return False


def _maybe_start_finance_backfill(
    db: Session,
    user: User,
    calendar_year: int,
    year_start: date_type,
    through: date_type,
) -> bool:
    """
    Автостарт догрузки финансов (sales+ads) ретроспективно:
    - если у пользователя есть WB ключ,
    - и если в pnl_daily нет покрытия нужного года,
    ставим sync_finance_backfill_step.

    2025 запускаем только после завершения 2026 (внутри таски).
    """
    if through < year_start:
        return False
    if not user.wb_api_key or not user.wb_api_key.strip():
        return False

    # Не стартуем "с нуля": финансовый backfill имеет смысл только если у пользователя уже есть
    # хотя бы какие-то продажи/реклама в БД (например, после initial sync за 30 дней).
    has_any_sales = (
        db.query(RawSale)
        .filter(
            RawSale.user_id == user.id,
            RawSale.date >= year_start,
            RawSale.date <= through,
        )
        .first()
        is not None
    )
    if not has_any_sales:
        return False

    # Warm path: точечно закрываем missing-tail (обычно вчера).
    miss = compute_missing_tail_range(
        db,
        user_id=str(user.id),
        through=through,
        lookback_days=FINANCE_MISSING_TAIL_LOOKBACK_DAYS,
    )
    if miss is not None:
        # Дедуп: не плодим одну и ту же задачу на каждый refresh.
        state = (
            db.query(FinanceMissingSyncState)
            .filter(
                FinanceMissingSyncState.user_id == user.id,
                FinanceMissingSyncState.date_from == miss.date_from,
                FinanceMissingSyncState.date_to == miss.date_to,
            )
            .first()
        )
        now_dt = datetime.now(timezone.utc)
        if state is not None:
            if state.status == "running":
                return False
            if state.next_run_at is not None:
                nr = state.next_run_at
                if nr.tzinfo is None:
                    nr = nr.replace(tzinfo=timezone.utc)
                if nr > now_dt:
                    return False
            # анти-спам кулдаун для idle/error без next_run_at
            updated = state.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if now_dt - updated <= FINANCE_MISSING_TAIL_COOLDOWN:
                return False
        else:
            state = FinanceMissingSyncState(
                user_id=str(user.id),
                date_from=miss.date_from,
                date_to=miss.date_to,
                status="queued",
            )
            db.add(state)
        state.status = "queued"
        state.next_run_at = None
        state.error_message = None
        try:
            db.commit()
        except IntegrityError:
            db.rollback()

        return _kick_finance_range_with_funnel_tail(str(user.id), miss.date_from, miss.date_to)

    # Если хвоста нет (вчера уже присутствует), можно закрывать остальные дыры в окне lookback.
    window_from = through - timedelta(days=FINANCE_MISSING_TAIL_LOOKBACK_DAYS)
    holes = compute_missing_ranges_in_window(
        db,
        user_id=str(user.id),
        date_from=window_from,
        date_to=through,
    )
    # Ставим только ограниченное число диапазонов за один вход, начиная с самых свежих дыр.
    queued = 0
    for rng in holes:
        if queued >= FINANCE_HOLES_MAX_RANGES_PER_ENTRY:
            break
        # Ограничиваем длину диапазона, чтобы не превращать warm-path в бэкфилл.
        # Если дыра длиннее — чиним только последнюю часть (ближе к today), остальное оставляем фону/следующим входам.
        df = rng.date_from
        dt = rng.date_to
        if (dt - df).days + 1 > FINANCE_HOLES_MAX_RANGE_DAYS:
            df = dt - timedelta(days=FINANCE_HOLES_MAX_RANGE_DAYS - 1)

        state = (
            db.query(FinanceMissingSyncState)
            .filter(
                FinanceMissingSyncState.user_id == user.id,
                FinanceMissingSyncState.date_from == df,
                FinanceMissingSyncState.date_to == dt,
            )
            .first()
        )
        now_dt = datetime.now(timezone.utc)
        if state is not None:
            if state.status == "running":
                continue
            if state.next_run_at is not None:
                nr = state.next_run_at
                if nr.tzinfo is None:
                    nr = nr.replace(tzinfo=timezone.utc)
                if nr > now_dt:
                    continue
            updated = state.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if now_dt - updated <= FINANCE_MISSING_TAIL_COOLDOWN:
                continue
        else:
            state = FinanceMissingSyncState(
                user_id=str(user.id),
                date_from=df,
                date_to=dt,
                status="queued",
            )
            db.add(state)
        state.status = "queued"
        state.next_run_at = None
        state.error_message = None
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        try:
            if _kick_finance_range_with_funnel_tail(str(user.id), df, dt):
                queued += 1
            else:
                continue
        except Exception as exc:
            logger.exception("Failed to kick finance+funnel orchestrator: %s", exc)

    # Важно: /dashboard/state — read-only по смыслу. Он НЕ должен запускать тяжелые фоновые процессы backfill.
    # Архивная догрузка управляется оркестратором + дозирующим менеджером (celery_beat), либо явной кнопкой.
    return queued > 0

def _num(v):
    if v is None:
        return None
    return float(v)


def _month_start(d: date_type) -> date_type:
    return date_type(d.year, d.month, 1)


def _month_end(d: date_type) -> date_type:
    last_day = monthrange(d.year, d.month)[1]
    return date_type(d.year, d.month, last_day)


def _iter_months(date_from: date_type, date_to: date_type) -> list[date_type]:
    """Return list of month starts between [date_from, date_to] inclusive."""
    if date_from > date_to:
        return []
    cur = _month_start(date_from)
    end = _month_start(date_to)
    months: list[date_type] = []
    while cur <= end:
        months.append(cur)
        # increment month
        y = cur.year + (1 if cur.month == 12 else 0)
        m = 1 if cur.month == 12 else (cur.month + 1)
        cur = date_type(y, m, 1)
    return months


PLAN_FACT_METRICS: list[tuple[str, bool]] = [
    ("revenue", False),
    ("orders_sum", False),
    ("commission", False),
    ("commission_pct", True),
    ("logistics", False),
    ("logistics_pct", True),
    ("penalties", False),
    ("cogs", False),
    ("cogs_share", True),
    ("tax", False),
    ("ads_spend", False),
    ("ads_pct", True),
    ("storage", False),
    ("storage_pct", True),
    ("wb_expenses_share", True),
    ("operation_expenses", False),
    ("margin", False),
    ("margin_pct", True),
    ("roi", True),
]


def _calc_pct_of_plan(fact: float | None, plan: float | None) -> float | None:
    if fact is None or plan is None:
        return None
    if plan == 0:
        if fact == 0:
            return None
        return 100.0
    return fact / plan


def _forecast_total_for_month(
    *,
    month_start: date_type,
    month_end: date_type,
    fact_to_yesterday: float,
    today: date_type,
) -> float:
    """
    Forecast month total:
      avg = current_fact / passed_days (excluding today)
      forecast = current_fact + avg * remaining_days (including today)
    If passed_days <= 0: return current_fact (no basis for extrapolation).
    """
    if today < month_start:
        # Month in the future for current "today": no actuals.
        return fact_to_yesterday
    passed_end = min(today - timedelta(days=1), month_end)
    passed_days = (passed_end - month_start).days + 1 if passed_end >= month_start else 0
    remaining_start = max(today, month_start)
    remaining_days = (month_end - remaining_start).days + 1 if remaining_start <= month_end else 0
    if passed_days <= 0:
        return fact_to_yesterday
    avg = fact_to_yesterday / passed_days
    return fact_to_yesterday + avg * remaining_days


def _derive_numeric_plans_from_revenue(values: dict[str, float]) -> dict[str, float]:
    """
    For cost categories where plan is entered in percent columns:
    - commission_pct -> commission
    - logistics_pct -> logistics
    - ads_pct -> ads_spend
    - storage_pct -> storage
    Uses revenue plan as base.
    """
    revenue_plan = values.get("revenue")
    if revenue_plan is None:
        return values
    derived_map = {
        "commission_pct": "commission",
        "logistics_pct": "logistics",
        "ads_pct": "ads_spend",
        "storage_pct": "storage",
    }
    out = dict(values)
    for pct_key, sum_key in derived_map.items():
        if pct_key in values:
            pct = values.get(pct_key) or 0.0
            out[sum_key] = revenue_plan * pct / 100.0
    return out


@router.get("/state")
def get_dashboard_state(
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
):
    """
    Состояние данных дашборда для текущего пользователя.

    Используется фронтом при первом входе:
    - has_data: есть ли хоть один день в pnl_daily;
    - has_2025 / has_2026: покрывает ли витрина 2025 / 2026 годы;
    - has_funnel: есть ли какие‑то данные в воронке;
    - funnel_ytd_backfill: статус фоновой догрузки воронки с начала года (products API).
    """
    store_user = store_ctx.store_owner
    q = db.query(PnlDaily).filter(PnlDaily.user_id == store_user.id)
    first = q.order_by(PnlDaily.date.asc()).first()
    last = q.order_by(PnlDaily.date.desc()).first()

    has_data = first is not None
    min_date = first.date if first else None
    max_date = last.date if last else None

    has_2025 = bool(min_date and min_date.year <= 2025)
    has_2026 = bool(max_date and max_date.year >= 2026)

    has_funnel = (
        db.query(FunnelDaily)
        .filter(FunnelDaily.user_id == store_user.id)
        .first()
        is not None
    )

    y = 2026
    y_start = FUNNEL_BACKFILL_START_DATE
    yesterday = date_type.today() - timedelta(days=1)
    through_cap = yesterday if yesterday <= date_type(2026, 12, 31) else date_type(2026, 12, 31)
    through_iso = through_cap.isoformat() if through_cap >= y_start else None

    _repair_stuck_funnel_ytd_running(db, str(store_user.id), y)
    _repair_hollow_funnel_ytd(db, str(store_user.id), y, y_start, through_cap)
    # Историческую YTD-догрузку воронки больше не автозапускаем при входе:
    # воронка теперь rolling 7 дней, а приоритет у финансового контура.
    funnel_tail_requested = _maybe_start_finance_backfill(db, store_user, y, y_start, through_cap)
    if not funnel_tail_requested:
        funnel_tail_requested = _maybe_start_funnel_tail_repair(db, store_user, through_cap)

    fb_row = (
        db.query(FunnelBackfillState)
        .filter(
            FunnelBackfillState.user_id == store_user.id,
            FunnelBackfillState.calendar_year == y,
        )
        .first()
    )
    fb_err = None
    if fb_row and fb_row.error_message and fb_row.error_message not in {"__retry_scheduled__", "__autostart_scheduled__"}:
        fb_err = fb_row.error_message[:500]

    funnel_ytd_backfill = {
        "year": y,
        "status": fb_row.status if fb_row else "idle",
        "last_completed_date": (
            fb_row.last_completed_date.isoformat()
            if fb_row and fb_row.last_completed_date
            else None
        ),
        "through_date": through_iso,
        "error_message": fb_err,
    }

    def _finance_state_for(year: int) -> dict:
        y_start = date_type(year, 1, 1)
        y_end = date_type(year, 12, 31)
        y_through = yesterday if yesterday <= y_end else y_end
        through_i = y_through.isoformat() if y_through >= y_start else None

        row = (
            db.query(FinanceBackfillState)
            .filter(
                FinanceBackfillState.user_id == store_user.id,
                FinanceBackfillState.calendar_year == year,
            )
            .first()
        )
        err = None
        if row and row.error_message and row.error_message not in {"__retry_scheduled__", "__autostart_scheduled__"}:
            err = row.error_message[:500]
        return {
            "year": year,
            "status": row.status if row else "idle",
            "last_completed_date": row.last_completed_date.isoformat() if row and row.last_completed_date else None,
            "through_date": through_i,
            "error_message": err,
        }

    def _finance_missing_sync_state() -> dict | None:
        """
        Для UI: состояние точечной догрузки "дыр" (missing-range).
        Возвращаем последнюю запись в окне lookback, чтобы фронт мог показать баннер/лоадер.
        """
        window_from = through_cap - timedelta(days=FINANCE_MISSING_TAIL_LOOKBACK_DAYS)
        row = (
            db.query(FinanceMissingSyncState)
            .filter(
                FinanceMissingSyncState.user_id == store_user.id,
                FinanceMissingSyncState.date_to >= window_from,
                FinanceMissingSyncState.date_to <= through_cap,
            )
            .order_by(FinanceMissingSyncState.updated_at.desc())
            .first()
        )
        if row is None:
            return None
        next_run = row.next_run_at
        if next_run is not None and next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=timezone.utc)
        return {
            "status": row.status,
            "date_from": row.date_from.isoformat(),
            "date_to": row.date_to.isoformat(),
            "next_run_at": next_run.isoformat() if next_run is not None else None,
            "error_message": (row.error_message[:500] if row.error_message else None),
        }

    def _funnel_tail_sync_state() -> dict:
        """
        Для UI polling: rolling repair воронки теперь живёт в WB orchestrator intents.
        Первый ответ /dashboard/state может вернуться раньше, чем celery обработает kick,
        поэтому учитываем локальный факт постановки `funnel_tail_requested`.
        """
        row = db.query(WbOrchestratorState).filter(WbOrchestratorState.user_id == str(store_user.id)).first()
        high = row.intents.get("high", {}) if row and isinstance(row.intents, dict) else {}
        pending = bool(funnel_tail_requested or (isinstance(high, dict) and high.get("funnel_tail") is True))
        status = "idle"
        if pending:
            status = row.status if row and row.status != "idle" else "queued"
        return {
            "status": status,
            "pending": pending,
            "cooldown_until": row.cooldown_until if row else None,
            "last_step": row.last_step if row else None,
        }

    return {
        "has_data": has_data,
        "min_date": min_date.isoformat() if min_date else None,
        "max_date": max_date.isoformat() if max_date else None,
        "has_2025": has_2025,
        "has_2026": has_2026,
        "has_funnel": has_funnel,
        "autostart_disabled": False,
        "autostart_disabled_reason": None,
        "funnel_ytd_backfill": funnel_ytd_backfill,
        # Для UI loader'ов: показывать прогресс ретроспективной догрузки финансов.
        "finance_backfill": _finance_state_for(2026),
        "finance_backfill_2025": _finance_state_for(2025),
        "finance_missing_sync": _finance_missing_sync_state(),
        "funnel_tail_sync": _funnel_tail_sync_state(),
    }


@router.get("/pnl", response_model=list[PnlDayResponse])
def get_pnl(
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
):
    """P&L по дням из витрины pnl_daily. Опционально фильтр по датам."""
    q = db.query(PnlDaily).filter(PnlDaily.user_id == store_ctx.store_owner.id)
    if date_from:
        q = q.filter(PnlDaily.date >= date_type.fromisoformat(date_from))
    if date_to:
        q = q.filter(PnlDaily.date <= date_type.fromisoformat(date_to))
    rows = q.order_by(PnlDaily.date).all()
    return [
        PnlDayResponse(
            date=r.date.isoformat(),
            revenue=_num(r.revenue),
            commission=_num(r.commission),
            logistics=_num(r.logistics),
            penalties=_num(r.penalties),
            storage=_num(r.storage),
            ads_spend=_num(r.ads_spend),
            cogs=_num(r.cogs),
            tax=_num(r.tax),
            operation_expenses=_num(r.operation_expenses),
            margin=_num(r.margin),
        )
        for r in rows
    ]


@router.get("/articles", response_model=list[ArticleResponse])
def get_articles(
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
):
    """Список артикулов с себестоимостью."""
    current_user = store_ctx.store_owner
    article_vendor_code_norm = func.nullif(func.btrim(Article.vendor_code), "")
    latest_vendor_code_sq = (
        db.query(
            FunnelDaily.user_id.label("user_id"),
            FunnelDaily.nm_id.label("nm_id"),
            FunnelDaily.vendor_code.label("vendor_code"),
            func.row_number()
            .over(
                partition_by=(FunnelDaily.user_id, FunnelDaily.nm_id),
                order_by=(
                    FunnelDaily.date.desc(),
                    FunnelDaily.updated_at.desc(),
                    FunnelDaily.id.desc(),
                ),
            )
            .label("rn"),
        )
        .filter(
            FunnelDaily.user_id == current_user.id,
            FunnelDaily.vendor_code.isnot(None),
            func.length(func.btrim(FunnelDaily.vendor_code)) > 0,
        )
        .subquery()
    )
    rows = (
        db.query(
            Article.nm_id,
            func.coalesce(article_vendor_code_norm, latest_vendor_code_sq.c.vendor_code).label(
                "vendor_code"
            ),
            Article.name,
            Article.subject_name,
            Article.cost_price,
        )
        .outerjoin(
            latest_vendor_code_sq,
            (latest_vendor_code_sq.c.user_id == Article.user_id)
            & (latest_vendor_code_sq.c.nm_id == Article.nm_id)
            & (latest_vendor_code_sq.c.rn == 1),
        )
        .filter(Article.user_id == current_user.id)
        .order_by(Article.nm_id.asc())
        .all()
    )
    return [
        ArticleResponse(
            nm_id=r.nm_id,
            vendor_code=r.vendor_code,
            name=r.name,
            subject_name=r.subject_name,
            cost_price=_num(r.cost_price),
        )
        for r in rows
    ]


@router.put("/articles/cost")
def save_articles_cost(
    body: list[ArticleCostUpdate],
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
):
    """Обновить себестоимость по артикулам (как в GAS apiSaveArticlesCost). После сохранения пересчёт P&L и sku_daily не вызывается автоматически — можно дернуть POST /sync/recalculate."""
    current_user = store_ctx.store_owner
    for item in body:
        art = (
            db.query(Article)
            .filter(Article.user_id == current_user.id, Article.nm_id == item.nm_id)
            .first()
        )
        if art:
            art.cost_price = item.cost_price
    db.commit()
    return {"success": True}


@router.get("/funnel", response_model=list[FunnelDayResponse])
def get_funnel(
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
):
    """Воронка по дням и артикулам. Опционально фильтр по датам."""
    current_user = store_ctx.store_owner
    def _normalize_vendor_code(v: str | None) -> str | None:
        """Treat None/empty/whitespace as missing."""
        if v is None:
            return None
        s = v.strip()
        return s if s else None

    # seller_article (vendor_code) используется фронтом как атрибут артикула,
    # но в FunnelDaily он может отсутствовать на некоторых днях.
    # Поэтому подтягиваем latest non-null vendor_code для (user_id, nm_id)
    # и используем его как fallback для строк, где vendor_code пустой.
    latest_vendor_code_sq = (
        db.query(
            FunnelDaily.user_id.label("user_id"),
            FunnelDaily.nm_id.label("nm_id"),
            FunnelDaily.vendor_code.label("vendor_code"),
            func.row_number()
            .over(
                partition_by=(FunnelDaily.user_id, FunnelDaily.nm_id),
                order_by=(FunnelDaily.date.desc(), FunnelDaily.updated_at.desc(), FunnelDaily.id.desc()),
            )
            .label("rn"),
        )
        .filter(
            FunnelDaily.user_id == current_user.id,
            FunnelDaily.vendor_code.isnot(None),
            func.length(func.btrim(FunnelDaily.vendor_code)) > 0,
        )
        .subquery()
    )

    q = (
        db.query(
            FunnelDaily,
            latest_vendor_code_sq.c.vendor_code.label("latest_vendor_code"),
        )
        .outerjoin(
            latest_vendor_code_sq,
            (latest_vendor_code_sq.c.user_id == FunnelDaily.user_id)
            & (latest_vendor_code_sq.c.nm_id == FunnelDaily.nm_id)
            & (latest_vendor_code_sq.c.rn == 1),
        )
        .filter(FunnelDaily.user_id == current_user.id)
    )
    if date_from:
        q = q.filter(FunnelDaily.date >= date_type.fromisoformat(date_from))
    if date_to:
        q = q.filter(FunnelDaily.date <= date_type.fromisoformat(date_to))
    rows = q.order_by(FunnelDaily.date, FunnelDaily.nm_id).all()

    result: list[FunnelDayResponse] = []
    for r, latest_vendor_code in rows:
        day_vendor_code = _normalize_vendor_code(r.vendor_code)
        resolved_vendor_code = day_vendor_code or _normalize_vendor_code(latest_vendor_code)
        result.append(
            FunnelDayResponse(
                date=r.date.isoformat(),
                nm_id=r.nm_id,
                vendor_code=resolved_vendor_code,
                open_count=r.open_count,
                cart_count=r.cart_count,
                order_count=r.order_count,
                order_sum=_num(r.order_sum),
                buyout_percent=_num(r.buyout_percent),
                cr_to_cart=_num(r.cr_to_cart),
                cr_to_order=_num(r.cr_to_order),
            )
        )

    return result


@router.get("/sku", response_model=list[SkuDayResponse])
def get_sku_timeseries(
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
    nm_id: int | None = Query(None, description="Фильтр по артикулу"),
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
):
    """Time-series по артикулам из витрины sku_daily. Опционально фильтр по датам и nm_id."""
    q = db.query(SkuDaily).filter(SkuDaily.user_id == store_ctx.store_owner.id)
    if date_from:
        q = q.filter(SkuDaily.date >= date_type.fromisoformat(date_from))
    if date_to:
        q = q.filter(SkuDaily.date <= date_type.fromisoformat(date_to))
    if nm_id is not None:
        q = q.filter(SkuDaily.nm_id == nm_id)
    rows = q.order_by(SkuDaily.date, SkuDaily.nm_id).all()
    return [
        SkuDayResponse(
            date=r.date.isoformat(),
            nm_id=r.nm_id,
            revenue=_num(r.revenue),
            commission=_num(r.commission),
            logistics=_num(r.logistics),
            penalties=_num(r.penalties),
            storage=_num(r.storage),
            ads_spend=_num(r.ads_spend),
            cogs=_num(r.cogs),
            tax=_num(r.tax),
            margin=_num(r.margin),
            open_count=r.open_count,
            cart_count=r.cart_count,
            order_count=r.order_count,
            order_sum=_num(r.order_sum),
        )
        for r in rows
    ]


@router.get("/operational-expenses", response_model=list[OperationalExpenseResponse])
def get_operational_expenses(
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
):
    """Операционные расходы по дням (для вкладки управления)."""
    q = db.query(OperationalExpense).filter(OperationalExpense.user_id == store_ctx.store_owner.id)
    if date_from:
        q = q.filter(OperationalExpense.date >= date_type.fromisoformat(date_from))
    if date_to:
        q = q.filter(OperationalExpense.date <= date_type.fromisoformat(date_to))

    rows = q.order_by(OperationalExpense.date.asc(), OperationalExpense.created_at.asc()).all()
    return [
        OperationalExpenseResponse(
            id=str(r.id),
            date=r.date.isoformat(),
            amount=float(r.amount),
            comment=r.comment,
        )
        for r in rows
    ]


@router.post("/operational-expenses", response_model=OperationalExpenseResponse)
def create_operational_expense(
    body: OperationalExpenseCreate,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
):
    """Создать операционные расходы и вернуть созданную запись."""
    from app.models.base import uuid_gen  # локальный импорт: чтобы не засорять файл

    current_user = store_ctx.store_owner
    exp = OperationalExpense(
        id=uuid_gen(),
        user_id=current_user.id,
        date=date_type.fromisoformat(body.date),
        amount=body.amount,
        comment=body.comment,
    )
    db.add(exp)
    db.commit()

    return OperationalExpenseResponse(
        id=str(exp.id),
        date=exp.date.isoformat(),
        amount=float(exp.amount),
        comment=exp.comment,
    )


@router.put("/operational-expenses/{expense_id}", response_model=OperationalExpenseResponse)
def update_operational_expense(
    expense_id: str,
    body: OperationalExpenseUpdate,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
):
    """Обновить операционные расходы."""
    current_user = store_ctx.store_owner
    exp = (
        db.query(OperationalExpense)
        .filter(OperationalExpense.user_id == current_user.id, OperationalExpense.id == expense_id)
        .first()
    )
    if exp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Операционные расходы не найдены")

    exp.date = date_type.fromisoformat(body.date)
    exp.amount = body.amount
    exp.comment = body.comment
    db.commit()

    return OperationalExpenseResponse(
        id=str(exp.id),
        date=exp.date.isoformat(),
        amount=float(exp.amount),
        comment=exp.comment,
    )


@router.post("/plan-fact/plans", response_model=PlanFactMonthResponse)
def save_plan_fact_month(
    body: PlanFactMonthRequest,
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
):
    """
    Save (upsert) plan values for a month.
    Month must be the first day of month (YYYY-MM-01).
    """
    current_user = store_ctx.store_owner
    month = _month_start(body.month)
    values = _derive_numeric_plans_from_revenue({k: float(v) for k, v in (body.values or {}).items()})

    # Upsert per metric_key.
    existing = (
        db.query(MonthlyPlan)
        .filter(MonthlyPlan.user_id == current_user.id, MonthlyPlan.month == month)
        .all()
    )
    by_key = {r.metric_key: r for r in existing}
    for key, val in values.items():
        row = by_key.get(key)
        if row is None:
            row = MonthlyPlan(id=uuid_gen(), user_id=str(current_user.id), month=month, metric_key=key, value=val)
        else:
            row.value = val
        db.add(row)
    db.commit()

    saved = (
        db.query(MonthlyPlan)
        .filter(MonthlyPlan.user_id == current_user.id, MonthlyPlan.month == month)
        .all()
    )
    return PlanFactMonthResponse(month=month, values={r.metric_key: float(r.value) for r in saved})


@router.get("/plan-fact/months", response_model=list[PlanFactMonthMetricsResponse])
def get_plan_fact_months(
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str = Query(..., description="YYYY-MM-DD"),
    store_ctx: StoreContext = Depends(get_store_context),
    db: Session = Depends(get_db),
):
    """
    Return plan/fact/%/forecast metrics for each month intersecting the given range.
    Facts/forecasts are ALWAYS computed for full month (independent of selected range).
    """
    df = date_type.fromisoformat(date_from)
    dt = date_type.fromisoformat(date_to)
    months = _iter_months(df, dt)
    if not months:
        return []

    # Preload plans for all months.
    current_user = store_ctx.store_owner
    plan_rows = (
        db.query(MonthlyPlan)
        .filter(MonthlyPlan.user_id == current_user.id, MonthlyPlan.month.in_(months))
        .all()
    )
    plans_by_month: dict[date_type, dict[str, float]] = {}
    for r in plan_rows:
        plans_by_month.setdefault(r.month, {})[r.metric_key] = float(r.value)

    today = date_type.today()
    result: list[PlanFactMonthMetricsResponse] = []

    for m_start in months:
        m_end = _month_end(m_start)

        # Numeric sums for full month.
        sums = (
            db.query(
                func.coalesce(func.sum(PnlDaily.revenue), 0.0).label("revenue"),
                func.coalesce(func.sum(PnlDaily.commission), 0.0).label("commission"),
                func.coalesce(func.sum(PnlDaily.logistics), 0.0).label("logistics"),
                func.coalesce(func.sum(PnlDaily.penalties), 0.0).label("penalties"),
                func.coalesce(func.sum(PnlDaily.storage), 0.0).label("storage"),
                func.coalesce(func.sum(PnlDaily.ads_spend), 0.0).label("ads_spend"),
                func.coalesce(func.sum(PnlDaily.cogs), 0.0).label("cogs"),
                func.coalesce(func.sum(PnlDaily.tax), 0.0).label("tax"),
                func.coalesce(func.sum(PnlDaily.operation_expenses), 0.0).label("operation_expenses"),
                func.coalesce(func.sum(PnlDaily.margin), 0.0).label("margin"),
            )
            .filter(PnlDaily.user_id == current_user.id, PnlDaily.date >= m_start, PnlDaily.date <= m_end)
            .one()
        )

        # Orders sum (full month) from funnel_daily (sum across nm_id).
        orders_sum = (
            db.query(func.coalesce(func.sum(FunnelDaily.order_sum), 0.0))
            .filter(FunnelDaily.user_id == current_user.id, FunnelDaily.date >= m_start, FunnelDaily.date <= m_end)
            .scalar()
            or 0.0
        )

        # Daily rows for percent averages + "current to yesterday" sums.
        daily_rows = (
            db.query(
                PnlDaily.date,
                PnlDaily.revenue,
                PnlDaily.commission,
                PnlDaily.logistics,
                PnlDaily.storage,
                PnlDaily.ads_spend,
                PnlDaily.cogs,
                PnlDaily.margin,
            )
            .filter(PnlDaily.user_id == current_user.id, PnlDaily.date >= m_start, PnlDaily.date <= m_end)
            .order_by(PnlDaily.date.asc())
            .all()
        )

        def _avg(nums: list[float]) -> float | None:
            if not nums:
                return None
            return sum(nums) / len(nums)

        com_pcts: list[float] = []
        log_pcts: list[float] = []
        ads_pcts: list[float] = []
        stor_pcts: list[float] = []
        margin_pcts: list[float] = []
        roi_pcts: list[float] = []

        fact_to_yesterday: dict[str, float] = {
            "revenue": 0.0,
            "orders_sum": 0.0,
            "commission": 0.0,
            "logistics": 0.0,
            "penalties": 0.0,
            "storage": 0.0,
            "ads_spend": 0.0,
            "cogs": 0.0,
            "tax": 0.0,
            "operation_expenses": 0.0,
            "margin": 0.0,
        }

        # Preload orders per day for "to yesterday" (funnel_daily).
        orders_per_day: dict[date_type, float] = dict(
            db.query(FunnelDaily.date, func.coalesce(func.sum(FunnelDaily.order_sum), 0.0))
            .filter(FunnelDaily.user_id == current_user.id, FunnelDaily.date >= m_start, FunnelDaily.date <= m_end)
            .group_by(FunnelDaily.date)
            .all()
        )

        for d, rev, comm, log, stor, ads, cogs, mar in daily_rows:
            revenue = float(rev or 0.0)
            commission = float(comm or 0.0)
            logistics = float(log or 0.0)
            storage = float(stor or 0.0)
            ads_spend = float(ads or 0.0)
            cogs_v = float(cogs or 0.0)
            margin = float(mar or 0.0)

            if revenue > 0:
                com_pcts.append((commission / revenue) * 100.0)
                log_pcts.append((logistics / revenue) * 100.0)
                ads_pcts.append((ads_spend / revenue) * 100.0)
                stor_pcts.append((storage / revenue) * 100.0)
                margin_pcts.append((margin / revenue) * 100.0)
            if cogs_v > 0:
                roi_pcts.append((margin / cogs_v) * 100.0)

            # to yesterday (exclude today)
            if d < today:
                fact_to_yesterday["revenue"] += revenue
                fact_to_yesterday["commission"] += commission
                fact_to_yesterday["logistics"] += logistics
                fact_to_yesterday["storage"] += storage
                fact_to_yesterday["ads_spend"] += ads_spend
                fact_to_yesterday["cogs"] += cogs_v
                fact_to_yesterday["margin"] += margin
                fact_to_yesterday["orders_sum"] += float(orders_per_day.get(d, 0.0) or 0.0)

        # For fields not present in daily_rows but required in forecast-to-yesterday:
        # penalties/tax/operation_expenses are in PnlDaily but not selected above; fetch sums to yesterday in one query.
        to_yesterday_end = min(today - timedelta(days=1), m_end)
        if to_yesterday_end >= m_start:
            extra = (
                db.query(
                    func.coalesce(func.sum(PnlDaily.penalties), 0.0),
                    func.coalesce(func.sum(PnlDaily.tax), 0.0),
                    func.coalesce(func.sum(PnlDaily.operation_expenses), 0.0),
                )
                .filter(PnlDaily.user_id == current_user.id, PnlDaily.date >= m_start, PnlDaily.date <= to_yesterday_end)
                .one()
            )
            fact_to_yesterday["penalties"] = float(extra[0] or 0.0)
            fact_to_yesterday["tax"] = float(extra[1] or 0.0)
            fact_to_yesterday["operation_expenses"] = float(extra[2] or 0.0)

        percent_facts: dict[str, float | None] = {
            "commission_pct": _avg(com_pcts),
            "logistics_pct": _avg(log_pcts),
            "ads_pct": _avg(ads_pcts),
            "storage_pct": _avg(stor_pcts),
            "margin_pct": _avg(margin_pcts),
            "roi": _avg(roi_pcts),
        }
        total_revenue = float(sums.revenue or 0.0)
        cogs_share: float | None = (float(sums.cogs or 0.0) / total_revenue) * 100.0 if total_revenue > 0 else 0.0
        percent_facts["cogs_share"] = cogs_share

        wb_expenses_share: float | None = None
        if total_revenue > 0:
            wb_expenses_share = (
                (
                    float(sums.storage or 0.0)
                    + float(sums.commission or 0.0)
                    + float(sums.ads_spend or 0.0)
                    + float(sums.logistics or 0.0)
                    + float(sums.penalties or 0.0)
                )
                / total_revenue
            ) * 100.0
        percent_facts["wb_expenses_share"] = wb_expenses_share

        facts: dict[str, float | None] = {
            "revenue": float(sums.revenue),
            "orders_sum": float(orders_sum),
            "commission": float(sums.commission),
            "logistics": float(sums.logistics),
            "penalties": float(sums.penalties),
            "storage": float(sums.storage),
            "ads_spend": float(sums.ads_spend),
            "cogs": float(sums.cogs),
            "tax": float(sums.tax),
            "operation_expenses": float(sums.operation_expenses),
            "margin": float(sums.margin),
            **{k: (float(v) if v is not None else None) for k, v in percent_facts.items()},
        }

        plan_values = plans_by_month.get(m_start, {})

        metric_rows: list[PlanFactMetricRow] = []
        for key, is_percent in PLAN_FACT_METRICS:
            plan = plan_values.get(key)
            fact = facts.get(key)
            if is_percent:
                metric_rows.append(
                    PlanFactMetricRow(
                        metric_key=key,
                        is_percent=True,
                        plan=plan,
                        fact=fact,
                        pct_of_plan=None,
                        forecast=None,
                        forecast_pct_of_plan=None,
                    )
                )
                continue

            fact_num = float(fact or 0.0)
            fc = _forecast_total_for_month(
                month_start=m_start,
                month_end=m_end,
                fact_to_yesterday=float(fact_to_yesterday.get(key, fact_num) or 0.0),
                today=today,
            )
            metric_rows.append(
                PlanFactMetricRow(
                    metric_key=key,
                    is_percent=False,
                    plan=plan,
                    fact=fact_num,
                    pct_of_plan=_calc_pct_of_plan(fact_num, plan),
                    forecast=fc,
                    forecast_pct_of_plan=_calc_pct_of_plan(fc, plan),
                )
            )

        result.append(PlanFactMonthMetricsResponse(month=m_start, metrics=metric_rows))

    return result
