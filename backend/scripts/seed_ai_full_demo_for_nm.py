"""
Для теста ИИ-модуля: подкрутить данные так, чтобы по одному nm_id сработали **все** правила аналитики.

Создаются:
- гипотезы: **content_change** (воронка), **ab_test** (CTR);
- задачи: **self_buyouts** (воронка + слабые отзывы/рейтинг из метрик или social),
  **restock** (stock_days_left), **check_measurements** + **check_ktr** (логистика +20% к базе).

Делает:
1) sku_daily: 7 дней до date_for с логистикой baseline, в date_for — скачок → логистические задачи (нужно ≥3 дня истории >0).
2) ai_competitor_metrics: upsert CTR, воронок, review_count, review_rating в последнем батче отчёта.
3) Удаляет гипотезы ab_test/content_change и product-задачи по nm (все статусы задач перечисленных типов).
4) run_daily_analytics(date_for, stock_days_left, social — social дублирует отзывы если метрик нет).

Пример (Docker), «вчера» по умолчанию:
  docker compose exec api python scripts/seed_ai_full_demo_for_nm.py \\
    --email test1@test.ru --nm-id 161873380 --period month
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, delete
from sqlalchemy.orm import Session

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
    p = argparse.ArgumentParser(description="Patch sku_daily + competitor metrics, then run AI analytics (full demo).")
    p.add_argument("--email", required=True)
    p.add_argument("--nm-id", type=int, required=True)
    p.add_argument("--period", default="month", help="week|month|quarter")
    p.add_argument(
        "--date-for",
        default=None,
        help="День аналитики (YYYY-MM-DD). По умолчанию: вчера (локальная дата).",
    )
    p.add_argument("--stock-days-left", type=int, default=10, help="Для restock (<14 даёт задачу)")
    p.add_argument("--reviews", type=int, default=5, help="И в social, и в review_count при upsert")
    p.add_argument("--rating", type=float, default=4.0, help="И в social, и в review_rating при upsert")
    return p.parse_args()


def _ensure_metric(
    *,
    db: Session,
    report_id: str,
    batch_id: str,
    nm_id: int,
    metric_code: str,
    ours: Decimal,
    med: Decimal | None,
    unit: str | None,
) -> None:
    row = (
        db.query(AiCompetitorMetric)
        .filter(
            AiCompetitorMetric.report_id == report_id,
            AiCompetitorMetric.import_batch_id == batch_id,
            AiCompetitorMetric.nm_id == nm_id,
            AiCompetitorMetric.metric_code == metric_code,
        )
        .first()
    )
    if row is None:
        db.add(
            AiCompetitorMetric(
                report_id=report_id,
                import_batch_id=batch_id,
                nm_id=nm_id,
                metric_code=metric_code,
                our_value=ours,
                competitor_median_value=med,
                unit=unit,
            )
        )
    else:
        row.our_value = ours
        row.competitor_median_value = med
        if unit is not None:
            row.unit = unit


def _ensure_sku_logistics_window(
    *,
    db: Session,
    user_id: str,
    nm_id: int,
    d_for: date,
    baseline: Decimal,
    spike: Decimal,
) -> None:
    """8 дней: d_for и 7 предыдущих — чтобы avg7d и текущий день были в выборке."""
    for i in range(0, 8):
        d = d_for - timedelta(days=i)
        logistics = spike if i == 0 else baseline
        row = (
            db.query(SkuDaily)
            .filter(SkuDaily.user_id == user_id, SkuDaily.nm_id == nm_id, SkuDaily.date == d)
            .first()
        )
        if row is None:
            db.add(
                SkuDaily(
                    user_id=user_id,
                    date=d,
                    nm_id=nm_id,
                    logistics=logistics,
                    revenue=Decimal("1000.00"),
                    margin=Decimal("100.00"),
                    ads_spend=Decimal("50.00"),
                    open_count=100,
                    order_count=5,
                )
            )
        else:
            row.logistics = logistics
            db.add(row)


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

        baseline = Decimal("100.00")
        spike = Decimal("155.00")

        _ensure_sku_logistics_window(db=db, user_id=uid, nm_id=nm, d_for=d_for, baseline=baseline, spike=spike)

        # CTR и воронки: отклонение от медианы ≥20%; все значения строго <100 п.п.
        patches: list[tuple[str, Decimal, Decimal, str | None]] = [
            ("ctr", Decimal("2.0"), Decimal("12.0"), "%"),
            ("funnel_cart", Decimal("5.0"), Decimal("18.0"), "%"),
            ("funnel_order", Decimal("1.5"), Decimal("8.0"), "%"),
            ("review_count", Decimal(str(int(args.reviews))), Decimal("200"), "шт"),
            ("review_rating", Decimal(str(float(args.rating))), Decimal("4.9"), None),
        ]
        for code, ours, med, unit in patches:
            _ensure_metric(
                db=db,
                report_id=rid,
                batch_id=bid,
                nm_id=nm,
                metric_code=code,
                ours=ours,
                med=med,
                unit=unit,
            )

        hyp_ids = [
            str(r[0])
            for r in db.query(AiHypothesis.id)
            .filter(
                and_(
                    AiHypothesis.user_id == uid,
                    AiHypothesis.nm_id == nm,
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
                    AiHypothesis.hypothesis_type.in_(["ab_test", "content_change"]),
                )
            )
        )
        db.execute(
            delete(AiTask).where(
                and_(
                    AiTask.user_id == uid,
                    AiTask.nm_id == nm,
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
                "report_date": rep.report_date.isoformat(),
                "date_for": res.date_for.isoformat(),
                "nm_id": nm,
                "created_task_ids": res.created_task_ids,
                "created_hypothesis_ids": res.created_hypothesis_ids,
                "expected": {
                    "hypotheses": ["content_change", "ab_test"],
                    "tasks": ["self_buyouts", "restock", "check_measurements", "check_ktr"],
                },
            }
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
