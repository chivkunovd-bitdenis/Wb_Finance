"""
REST API для фронта: дашборд (P&L по дням), артикулы, воронка, time-series по SKU.
Все эндпоинты требуют JWT.
"""
from datetime import date as date_type
from datetime import timedelta

from fastapi import APIRouter, Depends, Query, HTTPException, status

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.models.pnl_daily import PnlDaily
from app.models.article import Article
from app.models.funnel_backfill_state import FunnelBackfillState
from app.models.funnel_daily import FunnelDaily
from app.models.finance_backfill_state import FinanceBackfillState
from app.models.raw_sales import RawSale
from app.models.sku_daily import SkuDaily
from app.models.operational_expense import OperationalExpense
from celery_app.tasks import sync_funnel_ytd_step, sync_finance_backfill_step
from app.schemas.dashboard import (
    PnlDayResponse,
    ArticleResponse,
    ArticleCostUpdate,
    FunnelDayResponse,
    SkuDayResponse,
    OperationalExpenseResponse,
    OperationalExpenseCreate,
    OperationalExpenseUpdate,
)


router = APIRouter(prefix="/dashboard", tags=["dashboard"])


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
    # Детектор "ложного complete":
    # 1) есть продажи в старшем диапазоне (до последних 7 дней),
    # 2) а воронка есть только за последние дни/вообще пуста.
    early_window_end = year_start + timedelta(days=6)
    if through < early_window_end:
        return
    has_old_sales = (
        db.query(RawSale)
        .filter(
            RawSale.user_id == user_id,
            RawSale.date >= year_start,
            RawSale.date <= early_window_end,
        )
        .first()
        is not None
    )
    if not has_old_sales:
        return
    min_funnel_date = (
        db.query(FunnelDaily.date)
        .filter(FunnelDaily.user_id == user_id)
        .order_by(FunnelDaily.date.asc())
        .first()
    )
    first_funnel = min_funnel_date[0] if min_funnel_date else None
    if first_funnel is not None and first_funnel <= early_window_end:
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
    - если есть продажи старше последней недели,
    - а старой воронки нет,
    - и задача сейчас не running/complete,
    ставим sync_funnel_ytd_step в очередь.
    """
    if through < year_start:
        return
    if not user.wb_api_key or not user.wb_api_key.strip():
        return
    early_window_end = year_start + timedelta(days=6)
    if through < early_window_end:
        return
    has_old_sales = (
        db.query(RawSale)
        .filter(
            RawSale.user_id == user.id,
            RawSale.date >= year_start,
            RawSale.date <= early_window_end,
        )
        .first()
        is not None
    )
    if not has_old_sales:
        return
    min_funnel_date = (
        db.query(FunnelDaily.date)
        .filter(FunnelDaily.user_id == user.id)
        .order_by(FunnelDaily.date.asc())
        .first()
    )
    first_funnel = min_funnel_date[0] if min_funnel_date else None
    if first_funnel is not None and first_funnel <= early_window_end:
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
        row.error_message = "__autostart_scheduled__"
    db.add(row)
    db.commit()
    sync_funnel_ytd_step.delay(str(user.id), calendar_year)


def _maybe_start_finance_backfill(
    db: Session,
    user: User,
    calendar_year: int,
    year_start: date_type,
    through: date_type,
) -> None:
    """
    Автостарт догрузки финансов (sales+ads) ретроспективно:
    - если у пользователя есть WB ключ,
    - и если в pnl_daily нет покрытия нужного года,
    ставим sync_finance_backfill_step.

    2025 запускаем только после завершения 2026 (внутри таски).
    """
    if through < year_start:
        return
    if not user.wb_api_key or not user.wb_api_key.strip():
        return

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
        return

    # Если уже есть ранние дни года в pnl_daily — значит финансовый backfill хотя бы частично начат.
    first_pnl = (
        db.query(PnlDaily.date)
        .filter(
            PnlDaily.user_id == user.id,
            PnlDaily.date >= year_start,
            PnlDaily.date <= through,
        )
        .order_by(PnlDaily.date.asc())
        .first()
    )
    if first_pnl and first_pnl[0] <= year_start + timedelta(days=6):
        return

    row = (
        db.query(FinanceBackfillState)
        .filter(
            FinanceBackfillState.user_id == user.id,
            FinanceBackfillState.calendar_year == calendar_year,
        )
        .first()
    )
    if row and row.status in {"running", "complete"}:
        return
    if row and row.error_message == "__autostart_scheduled__":
        return
    if row is None:
        row = FinanceBackfillState(
            user_id=user.id,
            calendar_year=calendar_year,
            status="idle",
            error_message="__autostart_scheduled__",
        )
    else:
        row.error_message = "__autostart_scheduled__"
    db.add(row)
    db.commit()
    sync_finance_backfill_step.delay(str(user.id), calendar_year)

def _num(v):
    if v is None:
        return None
    return float(v)


@router.get("/state")
def get_dashboard_state(
    current_user: User = Depends(get_current_user),
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
    q = db.query(PnlDaily).filter(PnlDaily.user_id == current_user.id)
    first = q.order_by(PnlDaily.date.asc()).first()
    last = q.order_by(PnlDaily.date.desc()).first()

    has_data = first is not None
    min_date = first.date if first else None
    max_date = last.date if last else None

    has_2025 = bool(min_date and min_date.year <= 2025)
    has_2026 = bool(max_date and max_date.year >= 2026)

    has_funnel = (
        db.query(FunnelDaily)
        .filter(FunnelDaily.user_id == current_user.id)
        .first()
        is not None
    )

    y = date_type.today().year
    y_start = date_type(y, 1, 1)
    yesterday = date_type.today() - timedelta(days=1)
    through_cap = yesterday if yesterday <= date_type(y, 12, 31) else date_type(y, 12, 31)
    through_iso = through_cap.isoformat() if through_cap >= y_start else None

    _repair_hollow_funnel_ytd(db, str(current_user.id), y, y_start, through_cap)
    _maybe_start_funnel_ytd_backfill(db, current_user, y, y_start, through_cap)
    _maybe_start_finance_backfill(db, current_user, y, y_start, through_cap)

    fb_row = (
        db.query(FunnelBackfillState)
        .filter(
            FunnelBackfillState.user_id == current_user.id,
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

    return {
        "has_data": has_data,
        "min_date": min_date.isoformat() if min_date else None,
        "max_date": max_date.isoformat() if max_date else None,
        "has_2025": has_2025,
        "has_2026": has_2026,
        "has_funnel": has_funnel,
        "funnel_ytd_backfill": funnel_ytd_backfill,
    }


@router.get("/pnl", response_model=list[PnlDayResponse])
def get_pnl(
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """P&L по дням из витрины pnl_daily. Опционально фильтр по датам."""
    q = db.query(PnlDaily).filter(PnlDaily.user_id == current_user.id)
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Список артикулов с себестоимостью."""
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Обновить себестоимость по артикулам (как в GAS apiSaveArticlesCost). После сохранения пересчёт P&L и sku_daily не вызывается автоматически — можно дернуть POST /sync/recalculate."""
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Воронка по дням и артикулам. Опционально фильтр по датам."""
    q = db.query(FunnelDaily).filter(FunnelDaily.user_id == current_user.id)
    if date_from:
        q = q.filter(FunnelDaily.date >= date_type.fromisoformat(date_from))
    if date_to:
        q = q.filter(FunnelDaily.date <= date_type.fromisoformat(date_to))
    rows = q.order_by(FunnelDaily.date, FunnelDaily.nm_id).all()
    return [
        FunnelDayResponse(
            date=r.date.isoformat(),
            nm_id=r.nm_id,
            vendor_code=r.vendor_code,
            open_count=r.open_count,
            cart_count=r.cart_count,
            order_count=r.order_count,
            order_sum=_num(r.order_sum),
            buyout_percent=_num(r.buyout_percent),
            cr_to_cart=_num(r.cr_to_cart),
            cr_to_order=_num(r.cr_to_order),
        )
        for r in rows
    ]


@router.get("/sku", response_model=list[SkuDayResponse])
def get_sku_timeseries(
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
    nm_id: int | None = Query(None, description="Фильтр по артикулу"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Time-series по артикулам из витрины sku_daily. Опционально фильтр по датам и nm_id."""
    q = db.query(SkuDaily).filter(SkuDaily.user_id == current_user.id)
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Операционные расходы по дням (для вкладки управления)."""
    q = db.query(OperationalExpense).filter(OperationalExpense.user_id == current_user.id)
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Создать операционные расходы и вернуть созданную запись."""
    from app.models.base import uuid_gen  # локальный импорт: чтобы не засорять файл

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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Обновить операционные расходы."""
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
