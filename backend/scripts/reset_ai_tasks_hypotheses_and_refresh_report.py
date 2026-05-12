"""
Сброс ИИ-модуля для пользователя: удалить все гипотезы и задачи, заново скачать отчёт WB и импортировать метрики
(внутри воркера/таска уже вызывается run_daily_analytics).

Пример (Docker):
  docker compose exec api python scripts/reset_ai_tasks_hypotheses_and_refresh_report.py --email you@example.com --period week

Второй проход аналитики с «социалкой» (иначе задача self_buyouts не создаётся — в Celery после импорта social=None):
  ... --social-reviews 10 --social-rating 4.2

Требуется: WB storage_state или сохранённые креды, иначе fetch вернёт ошибку.
"""
from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.ai_competitor_metric import AiCompetitorMetric
from app.models.ai_competitor_report import AiCompetitorComparisonReport
from app.models.ai_hypothesis import AiHypothesis
from app.models.ai_task import AiTask
from app.models.user import User
from app.services.ai_daily_analytics_service import run_daily_analytics


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
    p.add_argument(
        "--social-reviews",
        type=int,
        default=None,
        help="После успешного fetch: второй run_daily_analytics с отзывами по всем nm отчёта (для self_buyouts)",
    )
    p.add_argument(
        "--social-rating",
        type=float,
        default=None,
        help="После успешного fetch: рейтинг по всем nm (пороги: <13 отзывов или <4.4)",
    )
    return p.parse_args()


def _social_map_for_report_uid(
    *, db: Session, user_id: str, period: str, reviews: int | None, rating: float | None
) -> tuple[dict[int, dict[str, float | int]] | None, str | None]:
    if reviews is None and rating is None:
        return None, None
    rep = (
        db.query(AiCompetitorComparisonReport)
        .filter(
            AiCompetitorComparisonReport.user_id == user_id,
            AiCompetitorComparisonReport.period == period,
            AiCompetitorComparisonReport.status == "ready",
        )
        .order_by(AiCompetitorComparisonReport.report_date.desc(), AiCompetitorComparisonReport.created_at.desc())
        .first()
    )
    if rep is None:
        return None, None
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
    return (out or None, str(rep.id))


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
        if args.social_reviews is not None or args.social_rating is not None:
            db3 = SessionLocal()
            try:
                social, rid = _social_map_for_report_uid(
                    db=db3,
                    user_id=uid,
                    period=period,
                    reviews=args.social_reviews,
                    rating=args.social_rating,
                )
                if social and rid:
                    rep_row = (
                        db3.query(AiCompetitorComparisonReport)
                        .filter(AiCompetitorComparisonReport.id == rid)
                        .first()
                    )
                    ar = run_daily_analytics(
                        db=db3,
                        user_id=uid,
                        report_id=rid,
                        date_for=rep_row.report_date if rep_row else None,
                        social=social,
                    )
                    print(
                        {
                            "analytics_only": {
                                "report_id": ar.report_id,
                                "date_for": ar.date_for.isoformat(),
                                "created_task_ids": ar.created_task_ids,
                                "created_hypothesis_ids": ar.created_hypothesis_ids,
                            }
                        }
                    )
                else:
                    print(
                        {"analytics_only": "skipped", "reason": "no ready report or no nm_ids"},
                        file=sys.stderr,
                    )
            finally:
                db3.close()
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

    if args.social_reviews is not None or args.social_rating is not None:
        db3 = SessionLocal()
        try:
            social, rid = _social_map_for_report_uid(
                db=db3,
                user_id=uid,
                period=period,
                reviews=args.social_reviews,
                rating=args.social_rating,
            )
            if not social or not rid:
                print(
                    {"second_pass_analytics": "skipped", "reason": "no ready report or no metrics nm_ids"},
                    file=sys.stderr,
                )
            else:
                rep_row = (
                    db3.query(AiCompetitorComparisonReport)
                    .filter(AiCompetitorComparisonReport.id == rid)
                    .first()
                )
                d_for = rep_row.report_date if rep_row else None
                ar = run_daily_analytics(
                    db=db3, user_id=uid, report_id=rid, date_for=d_for, social=social
                )
                print(
                    {
                        "second_pass_analytics": {
                            "report_id": ar.report_id,
                            "date_for": ar.date_for.isoformat(),
                            "created_task_ids": ar.created_task_ids,
                            "created_hypothesis_ids": ar.created_hypothesis_ids,
                        }
                    }
                )
        finally:
            db3.close()


if __name__ == "__main__":
    main()
