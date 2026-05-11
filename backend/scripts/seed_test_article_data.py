from __future__ import annotations

import argparse
import os
from datetime import date

from app.db import SessionLocal
from app.models.user import User
from app.services.test_data_seed_service import seed_test_article_timeseries


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed deterministic test timeseries for one WB article (nm_id).")
    p.add_argument("--email", required=True, help="User email in `users` table to attach seeded data to.")
    p.add_argument(
        "--nm-id",
        type=int,
        default=int((os.getenv("WB_COMPETITOR_ROW_NM_ID") or "0").strip() or 0),
        help="WB nm_id. Default: env WB_COMPETITOR_ROW_NM_ID",
    )
    p.add_argument("--vendor-code", default="ТЕСТ", help="Article vendor_code (seller article). Default: ТЕСТ")
    p.add_argument("--days", type=int, default=14, help="How many days to seed. Default: 14")
    p.add_argument("--date-to", default=None, help="End date YYYY-MM-DD (inclusive). Default: today")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    if not args.nm_id:
        raise SystemExit("nm_id is required: pass --nm-id or set WB_COMPETITOR_ROW_NM_ID")

    dt = date.fromisoformat(args.date_to) if args.date_to else None

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == args.email).first()
        if user is None:
            raise SystemExit(f"User not found by email: {args.email}")

        res = seed_test_article_timeseries(
            db,
            user_id=str(user.id),
            nm_id=int(args.nm_id),
            vendor_code=str(args.vendor_code),
            days=int(args.days),
            date_to=dt,
        )

        print(
            "Seeded:",
            {
                "user_id": res.user_id,
                "nm_id": res.nm_id,
                "date_from": res.date_from.isoformat(),
                "date_to": res.date_to.isoformat(),
                "days": res.days,
                "reference_nm_id": res.used_reference_nm_id,
            },
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()

