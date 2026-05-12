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

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.ai_competitor_metric import AiCompetitorMetric
from app.models.ai_competitor_report import AiCompetitorComparisonReport
from app.models.user import User
from app.services.ai_daily_analytics_service import run_daily_analytics


def _social_map_for_report(
    *,
    db: Session,
    rep: AiCompetitorComparisonReport,
    reviews: int | None,
    rating: float | None,
) -> dict[int, dict[str, float | int]] | None:
    """Одинаковые reviews/rating для всех nm из последнего батча отчёта (для правила self_buyouts)."""
    if reviews is None and rating is None:
        return None
    q = db.query(AiCompetitorMetric.nm_id).filter(AiCompetitorMetric.report_id == rep.id)
    if rep.latest_import_batch_id:
        q = q.filter(AiCompetitorMetric.import_batch_id == rep.latest_import_batch_id)
    nms = sorted({int(r[0]) for r in q.distinct().all()})
    out: dict[int, dict[str, float | int]] = {}
    for nm in nms:
        cell: dict[str, float | int] = {}
        if reviews is not None:
            cell["reviews"] = int(reviews)
        if rating is not None:
            cell["rating"] = float(rating)
        if cell:
            out[nm] = cell
    return out or None


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
    p.add_argument(
        "--social-reviews",
        type=int,
        default=None,
        help="Число отзывов по nm (для всех артикулов отчёта); с --social-rating включает задачу self_buyouts при слабой воронке",
    )
    p.add_argument(
        "--social-rating",
        type=float,
        default=None,
        help="Рейтинг по nm; вместе с порогами в коде (<13 отзывов или <4.4) даёт self_buyouts",
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
        social = _social_map_for_report(
            db=db, rep=rep, reviews=args.social_reviews, rating=args.social_rating
        )
        res = run_daily_analytics(
            db=db, user_id=uid, report_id=str(rep.id), date_for=d_for, social=social
        )
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
