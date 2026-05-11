"""
Для теста ИИ-модуля: подкрутить данные так, чтобы сработали правила аналитики по одному nm_id.

Делает:
1) sku_daily.logistics — 7 дней до date_for стабильно ~100, в date_for скачок ~150 (+50% к среднему) → задачи «обмеры/КТР».
2) ai_competitor_metrics (последний import_batch отчёта) для nm_id — CTR и воронки «хуже медианы» → гипотезы A/B + контент.
3) Удаляет draft-гипотезы и открытые product-задачи по этому nm (чтобы пересоздались).
4) Вызывает run_daily_analytics с stock_days_left и social (самовыкупы + дозакуп не берутся из sku_daily в текущем коде).

Пример (Docker):
  docker compose exec api python scripts/seed_ai_full_demo_for_nm.py \\
    --email test1@test.ru --nm-id 161873380 --period month
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, delete, update

from app.db import SessionLocal
from app.models.ai_competitor_metric import AiCompetitorMetric
from app.models.ai_competitor_report import AiCompetitorComparisonReport
from app.models.ai_hypothesis import AiHypothesis
from app.models.ai_hypothesis_daily_log import AiHypothesisDailyLog
from app.models.ai_task import AiTask
from app.models.sku_daily import SkuDaily
from app.models.user import User
from app.services.ai_daily_analytics_service import run_daily_analytics


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Patch sku_daily + competitor metrics, then run AI analytics (demo).")
    p.add_argument("--email", required=True)
    p.add_argument("--nm-id", type=int, required=True)
    p.add_argument("--period", default="month", help="week|month|quarter")
    p.add_argument(
        "--date-for",
        default=None,
        help="День «аналитики» (YYYY-MM-DD). По умолчанию: вчера (локальная дата).",
    )
    p.add_argument("--stock-days-left", type=int, default=10, help="Передать в run_daily_analytics для дозакупа")
    p.add_argument("--reviews", type=int, default=5)
    p.add_argument("--rating", type=float, default=4.0)
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    period = (args.period or "month").strip().lower()
    if period not in {"week", "month", "quarter"}:
        raise SystemExit("period must be week|month|quarter")

    d_for = date.fromisoformat(args.date_for) if args.date_for else (date.today() - timedelta(days=1))
    nm = int(args.nm_id)

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
        if rep is None or not rep.latest_import_batch_id:
            raise SystemExit(f"No ready competitor report with latest_import_batch_id for period={period!r}")

        bid = str(rep.latest_import_batch_id)
        rid = str(rep.id)

        # --- sku_daily: logistics spike on d_for vs stable week before ---
        baseline = Decimal("100.00")
        spike = Decimal("155.00")  # +55% vs avg7d baseline
        for i in range(1, 8):
            d = d_for - timedelta(days=i)
            db.execute(
                update(SkuDaily)
                .where(
                    and_(
                        SkuDaily.user_id == uid,
                        SkuDaily.nm_id == nm,
                        SkuDaily.date == d,
                    )
                )
                .values(logistics=baseline)
            )
        row_today = (
            db.query(SkuDaily)
            .filter(SkuDaily.user_id == uid, SkuDaily.nm_id == nm, SkuDaily.date == d_for)
            .first()
        )
        if row_today is None:
            raise SystemExit(
                f"No sku_daily row for user+nm_id on {d_for.isoformat()}. "
                f"Сначала прогоните scripts/seed_test_article_data.py на этот nm_id."
            )
        db.execute(
            update(SkuDaily)
            .where(and_(SkuDaily.user_id == uid, SkuDaily.nm_id == nm, SkuDaily.date == d_for))
            .values(logistics=spike)
        )

        # --- competitor metrics: CTR + funnels below median 20%+ ---
        patches: list[tuple[str, Decimal, Decimal]] = [
            ("ctr", Decimal("3.0"), Decimal("10.0")),
            # funnel_* в БД — процентные пункты (как в Excel без «%»), не «шт».
            ("funnel_cart", Decimal("8.0"), Decimal("14.0")),
            ("funnel_order", Decimal("2.5"), Decimal("5.0")),
        ]
        for code, ours, med in patches:
            db.execute(
                update(AiCompetitorMetric)
                .where(
                    and_(
                        AiCompetitorMetric.report_id == rid,
                        AiCompetitorMetric.import_batch_id == bid,
                        AiCompetitorMetric.nm_id == nm,
                        AiCompetitorMetric.metric_code == code,
                    )
                )
                .values(our_value=ours, competitor_median_value=med)
            )

        # --- remove active blocks for re-demo ---
        hyp_ids = [
            str(r[0])
            for r in db.query(AiHypothesis.id)
            .filter(
                and_(
                    AiHypothesis.user_id == uid,
                    AiHypothesis.nm_id == nm,
                    AiHypothesis.status == "draft",
                    AiHypothesis.hypothesis_type.in_(["ab_test", "content_change"]),
                )
            )
            .all()
        ]
        if hyp_ids:
            db.execute(delete(AiHypothesisDailyLog).where(AiHypothesisDailyLog.hypothesis_id.in_(hyp_ids)))
        db.execute(
            delete(AiHypothesis).where(
                and_(
                    AiHypothesis.user_id == uid,
                    AiHypothesis.nm_id == nm,
                    AiHypothesis.status == "draft",
                    AiHypothesis.hypothesis_type.in_(["ab_test", "content_change"]),
                )
            )
        )
        db.execute(
            delete(AiTask).where(
                and_(
                    AiTask.user_id == uid,
                    AiTask.nm_id == nm,
                    AiTask.status.in_(["new", "in_progress"]),
                    AiTask.task_type.in_(
                        ["restock", "self_buyouts", "check_measurements", "check_ktr"],
                    ),
                )
            )
        )
        db.commit()

        res = run_daily_analytics(
            db=db,
            user_id=uid,
            report_id=rid,
            date_for=d_for,
            stock_days_left={nm: int(args.stock_days_left)},
            social={nm: {"reviews": int(args.reviews), "rating": float(args.rating)}},
        )
        print(
            {
                "report_id": res.report_id,
                "date_for": res.date_for.isoformat(),
                "nm_id": nm,
                "created_task_ids": res.created_task_ids,
                "created_hypothesis_ids": res.created_hypothesis_ids,
            }
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
