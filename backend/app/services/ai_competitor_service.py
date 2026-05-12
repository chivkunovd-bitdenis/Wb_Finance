from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy.orm import Session

from app.models.ai_competitor_metric import AiCompetitorMetric
from app.models.ai_competitor_report import AiCompetitorComparisonReport
from app.models.ai_competitor_report_action import AiCompetitorReportAction
from app.models.base import uuid_gen


@dataclass(frozen=True)
class InvalidPayloadError(Exception):
    message: str


@dataclass(frozen=True)
class NotFoundError(Exception):
    message: str


_ALLOWED_PERIODS = {"week", "month", "quarter", "unknown"}
_ALLOWED_SOURCES = {"manual", "playwright"}
_ALLOWED_CODES = {
    "ctr",
    "traffic",
    "funnel_cart",
    "funnel_order",
    "review_count",
    "review_rating",
}


def import_competitor_report(
    *,
    db: Session,
    user_id: str,
    report_date: date,
    period: str,
    source: str,
    raw_payload: dict | None,
    items: list[dict],
) -> AiCompetitorComparisonReport:
    period = (period or "unknown").strip().lower()
    source = (source or "manual").strip().lower()
    if period not in _ALLOWED_PERIODS:
        raise InvalidPayloadError("Invalid period")
    if source not in _ALLOWED_SOURCES:
        raise InvalidPayloadError("Invalid source")
    if report_date is None:
        raise InvalidPayloadError("report_date is required")

    existing = (
        db.query(AiCompetitorComparisonReport)
        .filter(
            AiCompetitorComparisonReport.user_id == user_id,
            AiCompetitorComparisonReport.report_date == report_date,
            AiCompetitorComparisonReport.period == period,
        )
        .first()
    )
    if existing is None:
        report = AiCompetitorComparisonReport(
            user_id=user_id,
            report_date=report_date,
            period=period,
            source=source,
            raw_payload=raw_payload,
            valid_until=(report_date + timedelta(days=3)),
            status="ready",
            cost_or_limit_spent=(source == "playwright"),
            last_error=None,
        )
        db.add(report)
        db.commit()
        db.refresh(report)
    else:
        report = existing
        report.source = source
        report.raw_payload = raw_payload
        report.valid_until = report_date + timedelta(days=3)
        report.status = "ready"
        report.cost_or_limit_spent = bool(report.cost_or_limit_spent) or (source == "playwright")
        report.last_error = None
        db.add(report)
        db.commit()
        db.refresh(report)

    import_batch_id = str(uuid_gen())
    metrics: list[AiCompetitorMetric] = []
    for i, it in enumerate(items or []):
        try:
            nm_id = int(it.get("nm_id"))
        except Exception as exc:  # noqa: BLE001
            raise InvalidPayloadError(f"items[{i}].nm_id must be int") from exc

        code = (it.get("metric_code") or "").strip().lower()
        if code not in _ALLOWED_CODES:
            raise InvalidPayloadError(f"items[{i}].metric_code is invalid")

        our_value = it.get("our_value")
        competitor_value = it.get("competitor_median_value")
        unit = (it.get("unit") or None) if isinstance(it.get("unit"), str) else None
        extra = it.get("extra") if isinstance(it.get("extra"), dict) else None

        metrics.append(
            AiCompetitorMetric(
                report_id=str(report.id),
                import_batch_id=import_batch_id,
                nm_id=nm_id,
                metric_code=code,
                our_value=_to_decimal_or_none(our_value),
                competitor_median_value=_to_decimal_or_none(competitor_value),
                unit=(unit.strip() if unit else None),
                extra=extra,
            )
        )

    if metrics:
        report.latest_import_batch_id = import_batch_id
        db.add(report)
        db.add_all(metrics)
        db.commit()
        db.refresh(report)
    return report


def list_reports(*, db: Session, user_id: str, limit: int = 20) -> list[AiCompetitorComparisonReport]:
    lim = max(1, min(int(limit or 20), 100))
    return (
        db.query(AiCompetitorComparisonReport)
        .filter(AiCompetitorComparisonReport.user_id == user_id)
        .order_by(AiCompetitorComparisonReport.report_date.desc(), AiCompetitorComparisonReport.created_at.desc())
        .limit(lim)
        .all()
    )


def get_latest_report(*, db: Session, user_id: str, period: str) -> AiCompetitorComparisonReport | None:
    period = (period or "unknown").strip().lower()
    return (
        db.query(AiCompetitorComparisonReport)
        .filter(AiCompetitorComparisonReport.user_id == user_id, AiCompetitorComparisonReport.period == period)
        .order_by(AiCompetitorComparisonReport.report_date.desc(), AiCompetitorComparisonReport.created_at.desc())
        .first()
    )


def get_report(*, db: Session, user_id: str, report_id: str) -> AiCompetitorComparisonReport:
    row = (
        db.query(AiCompetitorComparisonReport)
        .filter(AiCompetitorComparisonReport.id == report_id, AiCompetitorComparisonReport.user_id == user_id)
        .first()
    )
    if not row:
        raise NotFoundError("Report not found")
    return row


def list_report_metrics(
    *,
    db: Session,
    report_id: str,
    metrics_scope: Literal["latest", "all"] = "latest",
) -> list[AiCompetitorMetric]:
    q = db.query(AiCompetitorMetric).filter(AiCompetitorMetric.report_id == report_id)
    if metrics_scope == "all":
        return (
            q.order_by(
                AiCompetitorMetric.import_batch_id.asc(),
                AiCompetitorMetric.nm_id.asc(),
                AiCompetitorMetric.metric_code.asc(),
            ).all()
        )
    rep = (
        db.query(AiCompetitorComparisonReport)
        .filter(AiCompetitorComparisonReport.id == report_id)
        .first()
    )
    if rep is not None and rep.latest_import_batch_id:
        q = q.filter(AiCompetitorMetric.import_batch_id == rep.latest_import_batch_id)
    return q.order_by(AiCompetitorMetric.nm_id.asc(), AiCompetitorMetric.metric_code.asc()).all()


def list_report_actions(*, db: Session, user_id: str, limit: int = 50) -> list[AiCompetitorReportAction]:
    lim = max(1, min(int(limit or 50), 200))
    return (
        db.query(AiCompetitorReportAction)
        .filter(AiCompetitorReportAction.user_id == user_id)
        .order_by(AiCompetitorReportAction.requested_at.desc())
        .limit(lim)
        .all()
    )


def _to_decimal_or_none(v) -> Decimal | None:
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None

