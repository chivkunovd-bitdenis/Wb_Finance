from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from typing import cast
from uuid import uuid4
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ai_competitor_metric import AiCompetitorMetric
from app.models.ai_competitor_report import AiCompetitorComparisonReport
from app.models.ai_hypothesis import AiHypothesis
from app.models.ai_task import AiTask
from app.models.sku_daily import SkuDaily


@dataclass(frozen=True)
class InvalidPayloadError(Exception):
    message: str


@dataclass(frozen=True)
class NotFoundError(Exception):
    message: str


@dataclass(frozen=True)
class AnalyticsResult:
    date_for: date
    report_id: str
    created_task_ids: list[str]
    created_hypothesis_ids: list[str]


_COMP_DELTA_THRESHOLD_PCT = 20.0
_LOGISTICS_DELTA_THRESHOLD_PCT = 20.0
_RESTOCK_DAYS_THRESHOLD = 14


def run_daily_analytics(
    *,
    db: Session,
    user_id: str,
    report_id: str,
    date_for: date | None = None,
    stock_days_left: dict[int, int] | None = None,
    social: dict[int, dict] | None = None,
) -> AnalyticsResult:
    """
    AI-MVP3: daily analytics -> tasks/hypotheses.

    Inputs:
    - competitor report (AI-MVP2): deltas vs median on ctr/traffic/funnel_cart/funnel_order.
    - sku_daily: logistics delta vs avg7d (if available).
    - optional stock/social maps for rules that depend on data not stored in DB yet.
    """
    rep = _get_report(db=db, user_id=user_id, report_id=report_id)
    d_for = date_for or rep.report_date

    metrics = (
        db.query(AiCompetitorMetric)
        .filter(AiCompetitorMetric.report_id == rep.id)
        .order_by(AiCompetitorMetric.nm_id.asc())
        .all()
    )
    by_nm: dict[int, dict[str, AiCompetitorMetric]] = {}
    for m in metrics:
        by_nm.setdefault(int(m.nm_id), {})[str(m.metric_code)] = m

    created_tasks: list[str] = []
    created_hyps: list[str] = []

    # 1) Competitor-based rules (ctr/traffic/funnels)
    for nm_id, mm in by_nm.items():
        # Funnels -> hypothesis "content_change"
        if _below_competitor_threshold(mm.get("funnel_cart")) or _below_competitor_threshold(mm.get("funnel_order")):
            h = _upsert_hypothesis(
                db=db,
                user_id=user_id,
                fingerprint=f"hyp:content_change:{nm_id}:{rep.report_date}:{rep.period}",
                nm_id=nm_id,
                hypothesis_type="content_change",
                title="Поменять внутренний контент карточки",
                description="Воронка ниже медианы конкурентов на 20%+",
                trigger_reason=_trigger_reason_for_funnels(mm),
                competitor_median_metrics=_competitor_metrics_payload(mm),
            )
            if h is not None:
                created_hyps.append(str(h.id))

            # Self-buyouts task requires social proof (reviews/rating) -> optional map
            if _needs_self_buyouts(social=social, nm_id=nm_id):
                t = _upsert_task(
                    db=db,
                    user_id=user_id,
                    fingerprint=f"task:self_buyouts:{nm_id}:{rep.report_date}:{rep.period}",
                    nm_id=nm_id,
                    task_type="self_buyouts",
                    title="Сделать самовыкупы",
                    description="Воронка ниже конкурентов и социальная сила карточки недостаточна",
                    reason="funnel_below_median && (reviews<13 || rating<4.4)",
                    competitor_median_value=_competitor_median_value_payload(mm),
                )
                if t is not None:
                    created_tasks.append(str(t.id))

        # CTR -> hypothesis "ab_test"
        if _below_competitor_threshold(mm.get("ctr")):
            h = _upsert_hypothesis(
                db=db,
                user_id=user_id,
                fingerprint=f"hyp:ab_test:{nm_id}:{rep.report_date}:{rep.period}",
                nm_id=nm_id,
                hypothesis_type="ab_test",
                title="Провести A/B-тест",
                description="CTR ниже медианы конкурентов на 20%+",
                trigger_reason=_trigger_reason_for_metric(mm.get("ctr")),
                competitor_median_metrics=_competitor_metrics_payload(mm),
            )
            if h is not None:
                created_hyps.append(str(h.id))

        # Traffic + promo logic (экономика/акции) пока не реализована: нет данных о promo/price/margin here.

    # 2) Logistics-based rules (sku_daily)
    created_tasks.extend(
        _run_logistics_rules(db=db, user_id=user_id, date_for=d_for, report_date=rep.report_date)
    )

    # 3) Stock-based rules (optional payload)
    if stock_days_left:
        for nm_id, days_left in stock_days_left.items():
            if int(days_left) < _RESTOCK_DAYS_THRESHOLD:
                t = _upsert_task(
                    db=db,
                    user_id=user_id,
                    fingerprint=f"task:restock:{nm_id}:{d_for}",
                    nm_id=int(nm_id),
                    task_type="restock",
                    title="Дозакупить товар",
                    description=f"Остатка хватит < {_RESTOCK_DAYS_THRESHOLD} дней",
                    reason="stock_days_left < 14",
                    current_value={"stock_days_left": int(days_left)},
                    threshold={"stock_days_left": _RESTOCK_DAYS_THRESHOLD},
                    priority=10,
                )
                if t is not None:
                    created_tasks.append(str(t.id))

    return AnalyticsResult(
        date_for=d_for,
        report_id=str(rep.id),
        created_task_ids=created_tasks,
        created_hypothesis_ids=created_hyps,
    )


def _get_report(*, db: Session, user_id: str, report_id: str) -> AiCompetitorComparisonReport:
    rep = (
        db.query(AiCompetitorComparisonReport)
        .filter(AiCompetitorComparisonReport.id == report_id, AiCompetitorComparisonReport.user_id == user_id)
        .first()
    )
    if not rep:
        raise NotFoundError("Report not found")
    return rep


def _below_competitor_threshold(m: AiCompetitorMetric | None) -> bool:
    if m is None:
        return False
    ours = _to_float(m.our_value)
    med = _to_float(m.competitor_median_value)
    if med == 0.0:
        return False
    delta_pct = (ours - med) / abs(med) * 100.0
    return delta_pct <= -_COMP_DELTA_THRESHOLD_PCT


def _to_float(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _competitor_metrics_payload(mm: dict[str, AiCompetitorMetric]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for code, m in mm.items():
        out[code] = {
            "our_value": _to_float(m.our_value) if m.our_value is not None else None,
            "competitor_median_value": _to_float(m.competitor_median_value) if m.competitor_median_value is not None else None,
            "unit": m.unit,
        }
    return out


def _competitor_median_value_payload(mm: dict[str, AiCompetitorMetric]) -> dict[str, float | None]:
    return {code: (_to_float(m.competitor_median_value) if m.competitor_median_value is not None else None) for code, m in mm.items()}


def _trigger_reason_for_metric(m: AiCompetitorMetric | None) -> str | None:
    if m is None:
        return None
    ours = _to_float(m.our_value)
    med = _to_float(m.competitor_median_value)
    if med == 0.0:
        return None
    delta_pct = round((ours - med) / abs(med) * 100.0, 1)
    return f"{m.metric_code}: {ours} vs median {med} ({delta_pct}%)"


def _trigger_reason_for_funnels(mm: dict[str, AiCompetitorMetric]) -> str | None:
    parts: list[str] = []
    for code in ("funnel_cart", "funnel_order"):
        if code in mm:
            tr = _trigger_reason_for_metric(mm[code])
            if tr:
                parts.append(tr)
    return "; ".join(parts) if parts else None


def _needs_self_buyouts(*, social: dict[int, dict] | None, nm_id: int) -> bool:
    if not social:
        return False
    s = social.get(int(nm_id)) or {}
    reviews = s.get("reviews")
    rating = s.get("rating")
    try:
        if reviews is not None and int(reviews) < 13:
            return True
    except (TypeError, ValueError):
        pass
    try:
        if rating is not None and float(rating) < 4.4:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _upsert_task(
    *,
    db: Session,
    user_id: str,
    fingerprint: str,
    nm_id: int | None,
    task_type: str,
    title: str,
    description: str | None,
    reason: str | None,
    source_metrics: dict | None = None,
    threshold: dict | None = None,
    current_value: dict | None = None,
    competitor_median_value: dict | None = None,
    priority: int = 0,
) -> AiTask | None:
    dedupe_key = _task_dedupe_key(nm_id=nm_id, task_type=task_type)

    # Rule: if there is an OPEN task (new|in_progress) for (user_id, dedupe_key) -> update it.
    if dedupe_key is not None:
        open_row = (
            db.query(AiTask)
            .filter(
                AiTask.user_id == user_id,
                AiTask.dedupe_key == dedupe_key,
                AiTask.status.in_(["new", "in_progress"]),
            )
            .order_by(AiTask.created_at.desc())
            .first()
        )
        if open_row is not None:
            open_row.title = title
            open_row.description = description
            open_row.reason = reason
            open_row.source_metrics = source_metrics
            open_row.threshold = threshold
            open_row.current_value = current_value
            open_row.competitor_median_value = competitor_median_value
            open_row.priority = priority
            # Keep status/user action timestamps intact.
            db.add(open_row)
            db.commit()
            db.refresh(open_row)
            return None

    row = AiTask(
        user_id=user_id,
        nm_id=nm_id,
        task_type=task_type,
        title=title,
        description=description,
        reason=reason,
        source_metrics=source_metrics,
        threshold=threshold,
        current_value=current_value,
        competitor_median_value=competitor_median_value,
        priority=priority,
        status="new",
        fingerprint=fingerprint,
        dedupe_key=dedupe_key,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Another transaction created the OPEN row first. Update it (do not create duplicates).
        if dedupe_key is None:
            return None
        open_row = (
            db.query(AiTask)
            .filter(
                AiTask.user_id == user_id,
                AiTask.dedupe_key == dedupe_key,
                AiTask.status.in_(["new", "in_progress"]),
            )
            .order_by(AiTask.created_at.desc())
            .first()
        )
        if open_row is not None:
            open_row.title = title
            open_row.description = description
            open_row.reason = reason
            open_row.source_metrics = source_metrics
            open_row.threshold = threshold
            open_row.current_value = current_value
            open_row.competitor_median_value = competitor_median_value
            open_row.priority = priority
            db.add(open_row)
            db.commit()
            db.refresh(open_row)
            return None

        # If there's no open row, the IntegrityError is likely due to fingerprint collision with a CLOSED task.
        # In that case we still must create a new task (your rule: closed -> create new).
        retry = AiTask(
            user_id=user_id,
            nm_id=nm_id,
            task_type=task_type,
            title=title,
            description=description,
            reason=reason,
            source_metrics=source_metrics,
            threshold=threshold,
            current_value=current_value,
            competitor_median_value=competitor_median_value,
            priority=priority,
            status="new",
            fingerprint=f"{fingerprint}:{uuid4()}" if fingerprint else None,
            dedupe_key=dedupe_key,
        )
        db.add(retry)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return None
        db.refresh(retry)
        return retry
    db.refresh(row)
    return row


def _upsert_hypothesis(
    *,
    db: Session,
    user_id: str,
    fingerprint: str,
    nm_id: int | None,
    hypothesis_type: str,
    title: str,
    description: str | None,
    goal: str | None = None,
    trigger_reason: str | None = None,
    baseline_metrics: dict | None = None,
    competitor_median_metrics: dict | None = None,
    expected_effect: dict | None = None,
    test_period_days: int | None = None,
) -> AiHypothesis | None:
    dedupe_key = _hypothesis_dedupe_key(nm_id=nm_id, hypothesis_type=hypothesis_type)

    # Rule: do not create a new hypothesis if there is an ACTIVE one (draft|running).
    if dedupe_key is not None:
        active = (
            db.query(AiHypothesis)
            .filter(
                AiHypothesis.user_id == user_id,
                AiHypothesis.dedupe_key == dedupe_key,
                AiHypothesis.status.in_(["draft", "running"]),
            )
            .order_by(AiHypothesis.created_at.desc())
            .first()
        )
        if active is not None:
            # Keep status/user actions; refresh payload to keep it relevant.
            active.title = title
            active.description = description
            active.goal = goal
            active.trigger_reason = trigger_reason
            active.baseline_metrics = baseline_metrics
            active.competitor_median_metrics = competitor_median_metrics
            active.expected_effect = expected_effect
            active.test_period_days = test_period_days
            db.add(active)
            db.commit()
            db.refresh(active)
            return None

    row = AiHypothesis(
        user_id=user_id,
        nm_id=nm_id,
        hypothesis_type=hypothesis_type,
        title=title,
        description=description,
        goal=goal,
        trigger_reason=trigger_reason,
        baseline_metrics=baseline_metrics,
        competitor_median_metrics=competitor_median_metrics,
        expected_effect=expected_effect,
        test_period_days=test_period_days,
        status="draft",
        started_at=None,
        ended_at=None,
        fingerprint=fingerprint,
        dedupe_key=dedupe_key,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    db.refresh(row)
    return row


def _run_logistics_rules(*, db: Session, user_id: str, date_for: date, report_date: date) -> list[str]:
    """
    Rule: logistics increase 20%+ day-to-day -> tasks:
    - check_measurements
    - check_ktr
    """
    d_prev = date_for - timedelta(days=1)
    rows: list[SkuDaily] = (
        db.query(SkuDaily)
        .filter(SkuDaily.user_id == user_id, SkuDaily.date.in_([d_prev, date_for]))
        .order_by(SkuDaily.date.asc())
        .all()
    )
    if not rows:
        return []

    by_nm: dict[int, dict[date, SkuDaily]] = {}
    for r in rows:
        by_nm.setdefault(int(r.nm_id), {})[cast(date, r.date)] = r

    created: list[str] = []
    for nm_id, rr in by_nm.items():
        cur = rr.get(date_for)
        prev = rr.get(d_prev)
        if cur is None or prev is None:
            continue
        cur_log = _to_float(cur.logistics)
        prev_log = _to_float(prev.logistics)
        if prev_log == 0.0:
            continue
        delta_pct = (cur_log - prev_log) / abs(prev_log) * 100.0
        if delta_pct < _LOGISTICS_DELTA_THRESHOLD_PCT:
            continue

        current = {
            "logistics_today": round(cur_log, 2),
            "logistics_prev_day": round(prev_log, 2),
            "delta_pct": round(delta_pct, 1),
        }
        thr = {"logistics_delta_pct": _LOGISTICS_DELTA_THRESHOLD_PCT}

        for task_type, title in (
            ("check_measurements", "Проверить обмеры и габариты"),
            ("check_ktr", "Проверить КТР"),
        ):
            t = _upsert_task(
                db=db,
                user_id=user_id,
                fingerprint=f"task:{task_type}:{nm_id}:{report_date}:{date_for}",
                nm_id=nm_id,
                task_type=task_type,
                title=title,
                description="Затраты на логистику выросли на 20%+ относительно предыдущего дня",
                reason="logistics_delta_pct >= 20",
                current_value=current,
                threshold=thr,
                priority=5,
            )
            if t is not None:
                created.append(str(t.id))

    return created


def _task_dedupe_key(*, nm_id: int | None, task_type: str) -> str | None:
    if nm_id is None:
        return None
    return f"task:{task_type}:{int(nm_id)}"


def _hypothesis_dedupe_key(*, nm_id: int | None, hypothesis_type: str) -> str | None:
    if nm_id is None:
        return None
    return f"hyp:{hypothesis_type}:{int(nm_id)}"

