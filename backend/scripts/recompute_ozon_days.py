"""One-off: re-normalise stored Ozon days from their saved raw accruals after a normalizer change.

No provider calls are made. Each owner is processed under the same owner lock that syncs and cost edits use, so a
running sync is never raced; an owner whose lock is held is skipped and can be rerun.

Usage (from the backend directory, after deploying a normalizer change):
    python -m scripts.recompute_ozon_days                  # every owner with complete Ozon days
    python -m scripts.recompute_ozon_days --user-id UUID   # one owner
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.ozon import OzonSyncDay
from app.services.marketplace_finance_service import (
    _acquire_owner_lock,
    _release_owner_lock,
    recompute_saved_ozon_costs,
)

logger = logging.getLogger("recompute_ozon_days")


def owners_with_complete_days(db: Session) -> list[str]:
    rows = db.query(OzonSyncDay.user_id).filter(OzonSyncDay.status == "complete").distinct().order_by(OzonSyncDay.user_id).all()
    return [str(row[0]) for row in rows]


def recompute_owner(db: Session, user_id: str) -> int | None:
    """Return the number of re-normalised days, or None when the owner lock is held by another run."""
    lock = _acquire_owner_lock(db, user_id)
    if lock is None:
        logger.warning("skipped owner=%s: a sync or cost change holds the owner lock; rerun later", user_id)
        return None
    try:
        changed = recompute_saved_ozon_costs(db=db, user_id=user_id)
        db.commit()
        return changed
    except Exception:
        db.rollback()
        raise
    finally:
        _release_owner_lock(lock, user_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-normalise stored Ozon days from saved accruals (no provider calls).")
    parser.add_argument("--user-id", help="Process only this owner (store owner user id).")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    db = SessionLocal()
    try:
        user_ids = [args.user_id] if args.user_id else owners_with_complete_days(db)
        recomputed = 0
        skipped = 0
        failed = 0
        for user_id in user_ids:
            try:
                changed = recompute_owner(db, user_id)
            except Exception:
                failed += 1
                logger.exception("failed owner=%s", user_id)
                continue
            if changed is None:
                skipped += 1
                continue
            recomputed += changed
            logger.info("owner=%s recomputed_days=%s", user_id, changed)
        logger.info("done owners=%s recomputed_days=%s skipped=%s failed=%s", len(user_ids), recomputed, skipped, failed)
        return 1 if failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
