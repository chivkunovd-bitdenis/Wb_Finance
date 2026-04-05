from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
import sys

from sqlalchemy import func

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.ai_export import (  # noqa: E402
    ExportConfig,
    build_ai_products_payload,
    resolve_date_to,
    resolve_export_user,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export products->daily funnel+finance JSON for LLM analysis."
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="UUID user id. If omitted and DB has exactly one user, it is auto-selected.",
    )
    parser.add_argument(
        "--user-email",
        type=str,
        default=None,
        help="User email (alternative to --user-id).",
    )
    parser.add_argument(
        "--date-from",
        type=str,
        default="2026-03-01",
        help="Start date (YYYY-MM-DD). Default: 2026-03-01",
    )
    parser.add_argument(
        "--date-to",
        type=str,
        default=None,
        help="End date (YYYY-MM-DD). Default: max date found in sku_daily/funnel_daily.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="ai_products_2026-03-01.json",
        help="Output file path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db = SessionLocal()
    try:
        if args.user_email:
            user_row = (
                db.query(User.id)
                .filter(func.lower(User.email) == args.user_email.strip().lower())
                .first()
            )
            if user_row is None:
                raise ValueError(f"user_email not found: {args.user_email}")
            user_id = str(user_row[0])
        else:
            user_id = resolve_export_user(db, args.user_id)
        date_from = date.fromisoformat(args.date_from)
        date_to = date.fromisoformat(args.date_to) if args.date_to else resolve_date_to(db, user_id, date_from)

        payload = build_ai_products_payload(
            db,
            ExportConfig(user_id=user_id, date_from=date_from, date_to=date_to),
        )
    finally:
        db.close()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Exported {len(payload['products'])} products to {out_path}")
    print(f"Range: {payload['meta']['date_from']} .. {payload['meta']['date_to']}")


if __name__ == "__main__":
    main()
