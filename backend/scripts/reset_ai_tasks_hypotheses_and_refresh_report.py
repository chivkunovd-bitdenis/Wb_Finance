"""
Сброс ИИ-модуля для пользователя: удалить все гипотезы и задачи, заново скачать отчёт WB и импортировать метрики
(внутри воркера/таска уже вызывается run_daily_analytics).

Пример (Docker):
  docker compose exec api python scripts/reset_ai_tasks_hypotheses_and_refresh_report.py --email you@example.com --period week

Требуется: WB storage_state или сохранённые креды, иначе fetch вернёт ошибку.
"""
from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.ai_hypothesis import AiHypothesis
from app.models.ai_task import AiTask
from app.models.user import User


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Delete all AI tasks/hypotheses for user, then run Playwright competitor report fetch + analytics.",
    )
    p.add_argument("--email", required=True, help="Email владельца магазина (users.email)")
    p.add_argument(
        "--period",
        default="week",
        help="Период отчёта WB: week|month|quarter (по умолчанию week)",
    )
    p.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Только удалить гипотезы/задачи и выйти (без скачивания отчёта)",
    )
    return p.parse_args()


def _purge_ai_rows(*, db: Session, user_id: str) -> tuple[int, int]:
    h_deleted = db.query(AiHypothesis).filter(AiHypothesis.user_id == user_id).delete(synchronize_session=False)
    t_deleted = db.query(AiTask).filter(AiTask.user_id == user_id).delete(synchronize_session=False)
    db.commit()
    return int(h_deleted), int(t_deleted)


def main() -> None:
    args = _parse_args()
    period = (args.period or "week").strip().lower()
    if period not in {"week", "month", "quarter"}:
        print("period must be week|month|quarter", file=sys.stderr)
        raise SystemExit(2)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == args.email).first()
        if user is None:
            print(f"User not found: {args.email}", file=sys.stderr)
            raise SystemExit(1)
        uid = str(user.id)
    finally:
        db.close()

    db2 = SessionLocal()
    try:
        hc, tc = _purge_ai_rows(db=db2, user_id=uid)
        print({"deleted_hypotheses": hc, "deleted_tasks": tc, "user_id": uid})
    finally:
        db2.close()

    if args.skip_fetch:
        print({"fetch": "skipped"})
        return

    # Импорт здесь, чтобы скрипт работал и без celery-брокера при --skip-fetch
    from celery_app.tasks import ai_competitor_report_fetch_playwright

    if os.getenv("AI_COMPETITOR_PLAYWRIGHT_ENABLED", "").strip().lower() in {"0", "false", "no"}:
        print(
            "AI_COMPETITOR_PLAYWRIGHT_ENABLED is off: set to 1 in api env or use --skip-fetch and import manually.",
            file=sys.stderr,
        )
        raise SystemExit(3)

    out = ai_competitor_report_fetch_playwright(uid, period)
    print(out)
    if not out.get("ok"):
        raise SystemExit(4)


if __name__ == "__main__":
    main()
