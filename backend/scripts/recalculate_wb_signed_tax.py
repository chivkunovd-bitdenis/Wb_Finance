"""One-off: apply the signed WB tax rule (2026-10-25) to stored pnl_daily and sku_daily rows.

Until 2026-10-25 recalculate_pnl / recalculate_sku_daily set the tax of a day (or SKU-day) with negative revenue to 0.
The rule is now tax = rate × revenue with its sign (celery_app.tasks.wb_tax), as for Ozon. Only such rows change: the
tax becomes round(wb_tax(revenue, rate), 2) and the margin drops by the same amount (old tax was 0). Revenue,
commission, logistics, COGS and every other column stay as stored, and no WB API call is made.

The recalculation tasks are not rerun for this: they rebuild every column from raw_sales and the current article costs,
which could move columns other than tax and margin. The script uses the same tax function instead.

A row is selected only while it still carries the old rule (revenue < 0 and tax = 0), so rerunning is harmless and a
row already rebuilt by the new tasks is left alone. Rows are processed per owner in batches, locked while updated,
with one commit per batch.

Usage (from the backend directory, after deploying the rule change):
    python -m scripts.recalculate_wb_signed_tax                  # dry run: counts and sums, nothing written
    python -m scripts.recalculate_wb_signed_tax --apply          # write
    python -m scripts.recalculate_wb_signed_tax --apply --user-id UUID
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.pnl_daily import PnlDaily
from app.models.sku_daily import SkuDaily
from app.models.user import User
from celery_app.tasks import TAX_RATE, wb_tax

logger = logging.getLogger("recalculate_wb_signed_tax")

BATCH_SIZE = 500
MODELS: tuple[Any, ...] = (PnlDaily, SkuDaily)


@dataclass
class TableResult:
    rows: int = 0
    tax_delta: Decimal = Decimal(0)


@dataclass
class OwnerResult:
    user_id: str
    tables: dict[str, TableResult] = field(default_factory=dict)


def _old_rule_rows(db: Session, model: Any, user_id: str):
    return db.query(model).filter(model.user_id == user_id, model.revenue < 0, model.tax == 0)


def owners_with_old_rule_rows(db: Session) -> list[str]:
    user_ids: set[str] = set()
    for model in MODELS:
        rows = db.query(model.user_id).filter(model.revenue < 0, model.tax == 0).distinct().all()
        user_ids.update(str(row[0]) for row in rows)
    return sorted(user_ids)


def signed_tax(revenue: Decimal, rate: float) -> Decimal:
    """The stored tax the recalculation tasks write for this revenue: round(wb_tax(revenue, rate), 2)."""
    return Decimal(str(round(wb_tax(float(revenue), rate), 2)))


def recalculate_owner(db: Session, user_id: str, *, apply: bool) -> OwnerResult:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError(f"user {user_id} not found")
    rate = float(user.tax_rate) if user.tax_rate is not None else TAX_RATE
    result = OwnerResult(user_id=user_id)
    for model in MODELS:
        table = result.tables.setdefault(model.__tablename__, TableResult())
        last_id: str | None = None
        while True:
            query = _old_rule_rows(db, model, user_id)
            if last_id is not None:
                query = query.filter(model.id > last_id)
            query = query.order_by(model.id.asc()).limit(BATCH_SIZE)
            if apply:
                query = query.with_for_update()
            batch = query.all()
            if not batch:
                break
            for row in batch:
                new_tax = signed_tax(row.revenue, rate)
                if new_tax == 0:
                    continue  # |revenue × rate| below half a kopeck: the stored zero is already the signed tax
                table.rows += 1
                table.tax_delta += new_tax
                if apply:
                    row.tax = new_tax
                    if row.margin is not None:
                        row.margin = Decimal(row.margin) - new_tax  # old tax was 0
            last_id = str(batch[-1].id)
            if apply:
                db.commit()  # one batch per transaction; a dry run changes nothing and keeps reading
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply the signed WB tax to stored WB days (no WB API calls).")
    parser.add_argument("--user-id", help="Process only this owner (store owner user id).")
    parser.add_argument("--apply", action="store_true", help="Write the changes; without it nothing is written.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    db = SessionLocal()
    try:
        user_ids = [args.user_id] if args.user_id else owners_with_old_rule_rows(db)
        failed = 0
        totals = {model.__tablename__: TableResult() for model in MODELS}
        for user_id in user_ids:
            try:
                owner = recalculate_owner(db, user_id, apply=args.apply)
            except Exception:
                db.rollback()
                failed += 1
                logger.exception("failed owner=%s", user_id)
                continue
            for name, table in owner.tables.items():
                totals[name].rows += table.rows
                totals[name].tax_delta += table.tax_delta
            logger.info(
                "owner=%s %s",
                user_id,
                " ".join(f"{name}: rows={t.rows} tax_delta={t.tax_delta}" for name, t in owner.tables.items()),
            )
        logger.info(
            "done mode=%s owners=%s failed=%s %s",
            "apply" if args.apply else "dry-run",
            len(user_ids),
            failed,
            " ".join(f"{name}: rows={t.rows} tax_delta={t.tax_delta}" for name, t in totals.items()),
        )
        return 1 if failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
