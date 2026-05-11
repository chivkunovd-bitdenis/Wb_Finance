"""
Вручную запустить ежедневную аналитику ИИ по последнему готовому отчёту сравнения пользователя.

Использование (Docker):
  docker compose exec api python scripts/run_ai_analytics_latest_report.py --email user@example.com

После успешного Playwright-импорта аналитика теперь дергается автоматически из воркера;
этот скрипт — для догона/теста без повторного скачивания.
"""
from __future__ import annotations

import argparse
from datetime import date

from app.db import SessionLocal
from app.models.ai_competitor_report import AiCompetitorComparisonReport
from app.models.user import User
from app.services.ai_daily_analytics_service import run_daily_analytics


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run AI daily analytics on latest ready competitor report.")
    p.add_argument("--email", required=True, help="Store owner email (users.email)")
    p.add_argument(
        "--period",
        default="week",
        help="Report period filter: week|month|quarter (default week)",
    )
    p.add_argument(
        "--date-for",
        default=None,
        help="date_for passed to analytics (YYYY-MM-DD). Default: report's report_date",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    period = (args.period or "week").strip().lower()
    if period not in {"week", "month", "quarter"}:
        raise SystemExit("period must be week|month|quarter")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == args.email).first()
        if user is None:
            raise SystemExit(f"User not found: {args.email}")
        uid = str(user.id)

        rep = (
            db.query(AiCompetitorComparisonReport)
            .filter(
                AiCompetitorComparisonReport.user_id == uid,
                AiCompetitorComparisonReport.period == period,
                AiCompetitorComparisonReport.status == "ready",
            )
            .order_by(AiCompetitorComparisonReport.report_date.desc(), AiCompetitorComparisonReport.created_at.desc())
            .first()
        )
        if rep is None:
            raise SystemExit(f"No ready competitor report for period={period!r}")

        d_for = date.fromisoformat(args.date_for) if args.date_for else None
        res = run_daily_analytics(db=db, user_id=uid, report_id=str(rep.id), date_for=d_for)
        print(
            {
                "report_id": res.report_id,
                "date_for": res.date_for.isoformat(),
                "created_task_ids": res.created_task_ids,
                "created_hypothesis_ids": res.created_hypothesis_ids,
            }
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
