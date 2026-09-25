"""
Seed a DEMO Wildberries store account (no real WB API key) with a large, internally consistent
synthetic dataset, so the whole product — Dashboard/P&L, Articles, Funnel, Costs, Operational
expenses, Plan-fact, AI chat + "Анализ AI CFO" — looks like a real mid-size WB apparel seller.

Run on prod:

    docker compose exec -T api python /app/scripts/seed_demo_store.py \\
        [--email demo@sellerfocus.pro] [--date-from 2025-01-01] [--date-to 2026-09-24] \\
        [--seed 42] [--reset]

Run locally (needs DATABASE_URL / backend/.env pointing at a real Postgres — there is none in
this dev sandbox, so this script has only been exercised against the pure generator, never
against a live database; see TASKLOG for what was and wasn't verified):

    cd backend && python scripts/seed_demo_store.py --email demo@sellerfocus.pro

Safety (hard refusals — no override flag; never touches another user's rows):
- the email must start with "demo" (case-insensitive);
- refuses if the target user already exists AND has a non-empty wb_api_key — this script is only
  for demo accounts that never talk to the real WB API, never a real seller's account;
- every delete/insert is scoped to this one user's user_id.

Data layering (see demo_store_data.py's docstring for the full generator contract):
1. `demo_store_data.build_demo_dataset()` — pure Python, deterministic via `random.Random(seed)`,
   no DB/Celery imports — builds the SKU profiles and every raw_sales/raw_ads/funnel_daily row.
2. This script bulk-inserts those raw rows (chunks of 5000, SQLAlchemy Core `insert()`).
3. `recalculate_pnl` / `recalculate_sku_daily` — the SAME functions Celery calls for real
   sellers (backend/celery_app/tasks.py) — are called synchronously (plain function calls, not
   `.delay`), month by month, so pnl_daily/sku_daily are produced by production logic rather than
   re-derived here. Neither function enqueues further Celery tasks, so this is safe to call from
   a script with no worker running.
4. Sync/orchestrator state rows (finance_backfill_state, funnel_backfill_state,
   funnel_rolling_sync_state, wb_orchestrator_state) are seeded as "complete"/"idle" so
   `/dashboard/state` shows has_2025/has_2026/has_funnel=true and no "Догружаем..." banners.
   Note: since the demo user has no wb_api_key, every autostart path in
   `app/routers/dashboard.py` (`_maybe_start_finance_backfill`, `_maybe_start_funnel_tail_repair`,
   `_maybe_start_funnel_ytd_backfill`) already short-circuits before touching these tables — the
   state rows are seeded anyway per spec, as defense in depth.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

_env = ROOT_DIR / ".env"
if _env.is_file():
    from dotenv import load_dotenv

    load_dotenv(_env, override=False)

import demo_store_data as gen  # noqa: E402 (pure generator; scripts/ is on sys.path above)
from sqlalchemy import delete, func, insert  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models.article import Article  # noqa: E402
from app.models.base import uuid_gen  # noqa: E402
from app.models.finance_backfill_state import FinanceBackfillState  # noqa: E402
from app.models.finance_missing_sync_state import FinanceMissingSyncState  # noqa: E402
from app.models.funnel_backfill_state import FunnelBackfillState  # noqa: E402
from app.models.funnel_daily import FunnelDaily  # noqa: E402
from app.models.funnel_rolling_sync_state import FunnelRollingSyncState  # noqa: E402
from app.models.monthly_plan import MonthlyPlan  # noqa: E402
from app.models.operational_expense import OperationalExpense  # noqa: E402
from app.models.pnl_daily import PnlDaily  # noqa: E402
from app.models.raw_ads import RawAd  # noqa: E402
from app.models.raw_sales import RawSale  # noqa: E402
from app.models.sku_daily import SkuDaily  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.wb_orchestrator_state import WbOrchestratorState  # noqa: E402
from app.services.billing_service import grant_lifetime  # noqa: E402
from celery_app.tasks import recalculate_pnl, recalculate_sku_daily  # noqa: E402

FUNNEL_BACKFILL_START_DATE = date(2026, 1, 1)
CHUNK_SIZE = 5000

# Tables this script owns end-to-end: --reset wipes exactly these rows for the target user_id,
# nothing else, and never rows belonging to any other user.
SEEDED_TABLES = [
    RawSale,
    RawAd,
    FunnelDaily,
    SkuDaily,
    PnlDaily,
    OperationalExpense,
    MonthlyPlan,
    Article,
    FinanceBackfillState,
    FunnelBackfillState,
    FinanceMissingSyncState,
    FunnelRollingSyncState,
    WbOrchestratorState,
]


def check_safety_guards(db: Session, email: str) -> User | None:
    """
    Hard-refuse unsafe targets (raises SystemExit — no override flag exists on purpose).
    Returns the existing user row for `email`, or None if no such user exists yet.
    """
    if not gen.is_demo_email(email):
        raise SystemExit(
            f"refusing: email must start with 'demo' (got {email!r}) — this script only ever "
            "seeds demo accounts, never a real seller."
        )
    existing = db.query(User).filter(User.email == email).first()
    if existing is not None and gen.user_has_wb_api_key(getattr(existing, "wb_api_key", None)):
        raise SystemExit(
            f"refusing: user {email!r} already has a WB API key set — this script must never "
            "touch an account connected to the real WB API."
        )
    return existing


def _get_or_create_user(db: Session, *, email: str, existing: User | None) -> tuple[User, str | None]:
    """Returns (user, plaintext_password) — password is non-None only when the user was just created."""
    if existing is not None:
        return existing, None
    password = secrets.token_urlsafe(12)
    user = User(
        email=email,
        password_hash=hash_password(password),
        wb_api_key=None,
        is_active=True,
        is_admin=False,
        tax_rate=0.06,
    )
    db.add(user)
    db.flush()
    return user, password


def _delete_existing_data(db: Session, user_id: str) -> None:
    for model in SEEDED_TABLES:
        db.execute(delete(model).where(model.user_id == user_id))
    db.commit()


def _bulk_insert(db: Session, table: type, rows: list[dict], *, chunk_size: int = CHUNK_SIZE) -> int:
    total = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        if not chunk:
            continue
        db.execute(insert(table), chunk)
        total += len(chunk)
    db.commit()
    return total


def _article_rows(user_id: str, skus: list[gen.SkuProfile]) -> list[dict]:
    return [
        {
            "user_id": user_id,
            "nm_id": p.nm_id,
            "vendor_code": p.vendor_code,
            "name": p.name,
            "subject_name": p.subject_name,
            "cost_price": p.cost_price,
        }
        for p in skus
    ]


def _funnel_rows(user_id: str, rows: list[gen.DailyFunnelRow]) -> list[dict]:
    return [
        {
            "user_id": user_id,
            "date": r.date,
            "nm_id": r.nm_id,
            "vendor_code": r.vendor_code,
            "open_count": r.open_count,
            "cart_count": r.cart_count,
            "order_count": r.order_count,
            "order_sum": r.order_sum,
            "buyout_percent": r.buyout_percent,
            "cr_to_cart": r.cr_to_cart,
            "cr_to_order": r.cr_to_order,
        }
        for r in rows
    ]


def _sale_rows(user_id: str, rows: list[gen.RawSaleRow]) -> list[dict]:
    return [
        {
            "user_id": user_id,
            "date": r.date,
            "nm_id": r.nm_id,
            "doc_type": r.doc_type,
            "retail_price": r.retail_price,
            "ppvz_for_pay": r.ppvz_for_pay,
            "delivery_rub": r.delivery_rub,
            "penalty": r.penalty,
            "additional_payment": r.additional_payment,
            "storage_fee": r.storage_fee,
            "quantity": r.quantity,
        }
        for r in rows
    ]


def _ad_rows(user_id: str, rows: list[gen.RawAdRow]) -> list[dict]:
    return [
        {"user_id": user_id, "date": r.date, "nm_id": r.nm_id, "campaign_id": r.campaign_id, "spend": r.spend}
        for r in rows
    ]


def _expense_rows(user_id: str, rows: list[gen.OperationalExpenseRow]) -> list[dict]:
    return [{"user_id": user_id, "date": r.date, "amount": r.amount, "comment": r.comment} for r in rows]


def _plan_rows(user_id: str, rows: list[gen.MonthlyPlanRow]) -> list[dict]:
    return [
        {"id": uuid_gen(), "user_id": user_id, "month": r.month, "metric_key": r.metric_key, "value": r.value}
        for r in rows
    ]


def _iter_month_chunks(date_from: date, date_to: date) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cur = date(date_from.year, date_from.month, 1)
    while cur <= date_to:
        nxt = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
        chunk_from = max(cur, date_from)
        chunk_to = min(nxt - timedelta(days=1), date_to)
        chunks.append((chunk_from, chunk_to))
        cur = nxt
    return chunks


def _recalculate(user_id: str, date_from: date, date_to: date) -> None:
    for chunk_from, chunk_to in _iter_month_chunks(date_from, date_to):
        r1 = recalculate_pnl(user_id, chunk_from.isoformat(), chunk_to.isoformat())
        if not r1.get("ok"):
            raise RuntimeError(f"recalculate_pnl failed for {chunk_from}..{chunk_to}: {r1}")
        r2 = recalculate_sku_daily(user_id, chunk_from.isoformat(), chunk_to.isoformat())
        if not r2.get("ok"):
            raise RuntimeError(f"recalculate_sku_daily failed for {chunk_from}..{chunk_to}: {r2}")
        print(f"  {chunk_from}..{chunk_to}: pnl_daily rows={r1.get('count')} sku_daily rows={r2.get('count')}")


def _seed_state_rows(db: Session, user_id: str, *, date_from: date, date_to: date) -> None:
    """
    Mark finance/funnel backfill as complete and the orchestrator idle, so /dashboard/state
    shows has_2025/has_2026/has_funnel=true with no pending-sync banners. See module docstring:
    with no wb_api_key these autostart paths never fire anyway, but we seed the rows regardless.
    """
    rows: list[object] = []
    if date_from.year <= 2025 <= date_to.year:
        rows.append(
            FinanceBackfillState(
                user_id=user_id,
                calendar_year=2025,
                status="complete",
                last_completed_date=date(2025, 1, 1),
                error_message=None,
            )
        )
    if date_from.year <= 2026 <= date_to.year:
        rows.append(
            FinanceBackfillState(
                user_id=user_id,
                calendar_year=2026,
                status="complete",
                last_completed_date=date(2026, 1, 1),
                error_message=None,
            )
        )
        rows.append(
            FunnelBackfillState(
                user_id=user_id,
                calendar_year=2026,
                status="complete",
                last_completed_date=min(date_to, date(2026, 12, 31)),
                error_message=None,
            )
        )
    rows.append(
        FunnelRollingSyncState(user_id=user_id, status="idle", last_completed_date=date_to, error_message=None)
    )
    rows.append(
        WbOrchestratorState(user_id=user_id, status="idle", cooldown_until=None, last_step="demo_seed", intents={})
    )
    db.add_all(rows)
    db.commit()


def _print_summary(db: Session, user_id: str, *, date_from: date, date_to: date) -> None:
    def _count(model: type) -> int:
        return db.query(model).filter(model.user_id == user_id).count()

    print("\n=== Demo store seed summary ===")
    print(f"user_id={user_id} date_from={date_from} date_to={date_to}")
    for label, model in [
        ("articles", Article),
        ("raw_sales", RawSale),
        ("raw_ads", RawAd),
        ("funnel_daily", FunnelDaily),
        ("sku_daily", SkuDaily),
        ("pnl_daily", PnlDaily),
        ("operational_expenses", OperationalExpense),
        ("monthly_plan", MonthlyPlan),
    ]:
        print(f"  {label}: {_count(model)}")

    last30_from = date_to - timedelta(days=29)
    agg = (
        db.query(
            func.coalesce(func.sum(PnlDaily.revenue), 0.0),
            func.coalesce(func.sum(PnlDaily.margin), 0.0),
        )
        .filter(PnlDaily.user_id == user_id, PnlDaily.date >= last30_from, PnlDaily.date <= date_to)
        .one()
    )
    print(f"  last 30 days ({last30_from}..{date_to}): revenue={float(agg[0]):.2f} margin={float(agg[1]):.2f}")

    top = (
        db.query(SkuDaily.nm_id, func.sum(SkuDaily.margin).label("total_margin"))
        .filter(SkuDaily.user_id == user_id, SkuDaily.date >= date_from, SkuDaily.date <= date_to)
        .group_by(SkuDaily.nm_id)
        .order_by(func.sum(SkuDaily.margin).desc())
        .limit(3)
        .all()
    )
    vendor_by_nm = {a.nm_id: a.vendor_code for a in db.query(Article).filter(Article.user_id == user_id).all()}
    print("  top-3 SKUs by margin (full range):")
    for nm_id, total_margin in top:
        label = vendor_by_nm.get(nm_id) or f"nm_id={nm_id}"
        print(f"    {label}: margin={float(total_margin):.2f}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed a demo WB store (no real WB API key) with a large, internally consistent dataset."
    )
    parser.add_argument("--email", default="demo@sellerfocus.pro", help="Demo account email (must start with 'demo').")
    parser.add_argument("--date-from", default="2025-01-01", help="YYYY-MM-DD. Default: 2025-01-01.")
    parser.add_argument("--date-to", default=None, help="YYYY-MM-DD. Default: yesterday.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic RNG seed. Default: 42.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete this user's previously seeded rows first, then reseed (idempotent re-run).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    email = str(args.email).strip().lower()
    date_from = date.fromisoformat(args.date_from)
    date_to = date.fromisoformat(args.date_to) if args.date_to else (date.today() - timedelta(days=1))
    if date_to < date_from:
        print("error: --date-to must be >= --date-from", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        existing = check_safety_guards(db, email)

        if existing is not None:
            has_existing_data = db.query(RawSale).filter(RawSale.user_id == existing.id).first() is not None
            if has_existing_data and not args.reset:
                print(
                    f"error: user {email!r} already has seeded data — pass --reset to wipe and reseed.",
                    file=sys.stderr,
                )
                return 2
            if args.reset:
                print(f"--reset: deleting previously seeded rows for {email} ...")
                _delete_existing_data(db, str(existing.id))

        user, new_password = _get_or_create_user(db, email=email, existing=existing)
        db.commit()

        grant_lifetime(db, str(user.id))
        db.commit()

        if new_password:
            print(f"created user {email} (user_id={user.id})")
            print(f"password (shown once — store it now): {new_password}")
        else:
            print(f"reusing existing user {email} (user_id={user.id})")

        print(f"generating dataset: seed={args.seed} date_from={date_from} date_to={date_to} ...")
        dataset = gen.build_demo_dataset(seed=args.seed, date_from=date_from, date_to=date_to)
        print(f"  {len(dataset.skus)} SKUs, archetypes={gen.archetype_counts(dataset.skus)}")

        user_id = str(user.id)
        n_articles = _bulk_insert(db, Article, _article_rows(user_id, dataset.skus))
        n_funnel = _bulk_insert(db, FunnelDaily, _funnel_rows(user_id, dataset.funnel_rows))
        n_sales = _bulk_insert(db, RawSale, _sale_rows(user_id, dataset.sale_rows))
        n_ads = _bulk_insert(db, RawAd, _ad_rows(user_id, dataset.ad_rows))
        n_expenses = _bulk_insert(db, OperationalExpense, _expense_rows(user_id, dataset.operational_expenses))
        n_plans = _bulk_insert(db, MonthlyPlan, _plan_rows(user_id, dataset.monthly_plans))
        print(
            f"  inserted: articles={n_articles} funnel_daily={n_funnel} raw_sales={n_sales} "
            f"raw_ads={n_ads} operational_expenses={n_expenses} monthly_plan={n_plans}"
        )

        print("recalculating pnl_daily / sku_daily via production logic, month by month ...")
        _recalculate(user_id, date_from, date_to)

        print("seeding sync/orchestrator state rows ...")
        _seed_state_rows(db, user_id, date_from=date_from, date_to=date_to)

        _print_summary(db, user_id, date_from=date_from, date_to=date_to)
        print("\nDone.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
