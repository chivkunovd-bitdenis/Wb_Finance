from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from typing import Any, cast
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
# Конверсии в отчёте WB — процентные пункты; значения > 100 почти всегда означают «не та строка» (шт и т.п.).
_FUNNEL_CONVERSION_PP_MAX = 100.0


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
    - **WB «Сравнение карточек»** (импортированный отчёт): CTR, трафик (абсолют), конверсии в корзину/заказ
      (в Excel часто без «%», в БД — процентные пункты); для каждого nm_id «наши» vs медиана по другим карточкам
      в сравнении (одна из колонок — наш артикул).
    - **Наши финансы / операционка**: `sku_daily` — логистика и правила роста логистики vs база (не из Excel WB).
    - optional stock/social maps for rules that depend on data not stored in DB yet.
    """
    rep = _get_report(db=db, user_id=user_id, report_id=report_id)
    d_for = date_for or rep.report_date

    mq = db.query(AiCompetitorMetric).filter(AiCompetitorMetric.report_id == rep.id)
    if rep.latest_import_batch_id:
        mq = mq.filter(AiCompetitorMetric.import_batch_id == rep.latest_import_batch_id)
    metrics = mq.order_by(AiCompetitorMetric.nm_id.asc()).all()
    by_nm: dict[int, dict[str, AiCompetitorMetric]] = {}
    for m in metrics:
        by_nm.setdefault(int(m.nm_id), {})[str(m.metric_code)] = m

    created_tasks: list[str] = []
    created_hyps: list[str] = []

    # 1) Competitor-based rules (ctr/traffic/funnels)
    for nm_id, mm in by_nm.items():
        # Funnels -> hypothesis "content_change" (только если числа похожи на процентные пункты, не «шт»)
        fc_m, fo_m = mm.get("funnel_cart"), mm.get("funnel_order")
        funnel_hit = (
            (_funnel_conversion_plausible(fc_m) and _below_competitor_threshold(fc_m))
            or (_funnel_conversion_plausible(fo_m) and _below_competitor_threshold(fo_m))
        )
        if funnel_hit:
            h = _upsert_hypothesis(
                db=db,
                user_id=user_id,
                fingerprint=f"hyp:content_change:{nm_id}:{rep.report_date}:{rep.period}",
                nm_id=nm_id,
                hypothesis_type="content_change",
                title="Обновить текст и медиа внутри карточки",
                description="Конверсии в корзину или в заказ заметно слабее, чем у карточек в сравнении — имеет смысл переработать описание, характеристики и внутренний контент.",
                trigger_reason=_human_funnel_trigger(mm),
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
                    reason="Низкие конверсии при малом числе отзывов или низком рейтинге",
                    competitor_median_value=_competitor_median_value_payload(mm),
                )
                if t is not None:
                    created_tasks.append(str(t.id))

        # CTR -> hypothesis "ab_test"
        if _ctr_plausible(mm.get("ctr")) and _below_competitor_threshold(mm.get("ctr")):
            h = _upsert_hypothesis(
                db=db,
                user_id=user_id,
                fingerprint=f"hyp:ab_test:{nm_id}:{rep.report_date}:{rep.period}",
                nm_id=nm_id,
                hypothesis_type="ab_test",
                title="Проверить главное фото и инфографику (A/B)",
                description="CTR заметно слабее, чем у карточек в сравнении — логично начать с визуала в поиске и карточке.",
                trigger_reason=_human_ctr_trigger(mm.get("ctr")),
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
                    description=f"Остатка хватит меньше чем на {_RESTOCK_DAYS_THRESHOLD} дней продаж — пора дозакупить.",
                    reason="Запас по оборачиваемости ниже безопасного порога для этого товара.",
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


def _funnel_conversion_plausible(m: object | None) -> bool:
    if m is None:
        return False
    ours = _to_float(getattr(m, "our_value", None))
    med = _to_float(getattr(m, "competitor_median_value", None))
    if med <= 0.0:
        return False
    return ours <= _FUNNEL_CONVERSION_PP_MAX and med <= _FUNNEL_CONVERSION_PP_MAX


def _ctr_plausible(m: object | None) -> bool:
    if m is None:
        return False
    ours = _to_float(getattr(m, "our_value", None))
    med = _to_float(getattr(m, "competitor_median_value", None))
    if med <= 0.0:
        return False
    return ours <= 100.0 and med <= 100.0


def _below_competitor_threshold(m: object | None) -> bool:
    if m is None:
        return False
    ours = _to_float(getattr(m, "our_value", None))
    med = _to_float(getattr(m, "competitor_median_value", None))
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


def _human_ctr_trigger(m: object | None) -> str | None:
    """Понятное пользователю объяснение по CTR (без кодов метрик)."""
    if m is None:
        return None
    ours = _to_float(getattr(m, "our_value", None))
    med = _to_float(getattr(m, "competitor_median_value", None))
    if med == 0.0:
        return None
    delta_pct = round((ours - med) / abs(med) * 100.0, 1)
    worse = abs(delta_pct)
    return (
        "Рекомендуется начать с главного фото и инфографики: CTR вашей карточки ниже, "
        f"чем у конкурентов в этом сравнении примерно на {worse:.0f}% "
        f"(у вас {ours:.1f}, у конкурентов по медиане {med:.1f}). "
        "Имеет смысл прогнать A/B-тест по визиту."
    )


def _human_funnel_trigger(mm: dict[str, Any]) -> str | None:
    """Понятное объяснение по конверсиям в корзину/заказ (только сработавшие правила)."""
    chunks: list[str] = []
    for code, label in (
        ("funnel_cart", "конверсия в корзину"),
        ("funnel_order", "конверсия в заказ"),
    ):
        m = mm.get(code)
        if m is None or not _funnel_conversion_plausible(m) or not _below_competitor_threshold(m):
            continue
        ours = _to_float(getattr(m, "our_value", None))
        med = _to_float(getattr(m, "competitor_median_value", None))
        if med == 0.0:
            continue
        delta_pct = round((ours - med) / abs(med) * 100.0, 1)
        worse = abs(delta_pct)
        chunks.append(
            f"{label} — ниже, чем у конкурентов в сравнении примерно на {worse:.0f}% "
            f"(у вас {ours:.1f}, по медиане конкурентов {med:.1f})"
        )
    if not chunks:
        return None
    return (
        "Рекомендуется обновить наполнение карточки (описание, характеристики, внутренние фото и видео), потому что "
        + "; ".join(chunks)
        + "."
    )


class _JsonMetricView:
    """Снимок метрики из JSON `competitor_median_metrics` (как в `_competitor_metrics_payload`)."""

    __slots__ = ("our_value", "competitor_median_value", "unit")

    def __init__(self, payload: dict[str, Any]) -> None:
        self.our_value = payload.get("our_value")
        self.competitor_median_value = payload.get("competitor_median_value")
        u = payload.get("unit")
        self.unit = u if isinstance(u, str) else None


def _mm_json_to_views(cm: dict[str, Any] | None) -> dict[str, _JsonMetricView]:
    out: dict[str, _JsonMetricView] = {}
    for code, raw in (cm or {}).items():
        if isinstance(raw, dict):
            out[str(code)] = _JsonMetricView(raw)
    return out


def hypothesis_api_copy_needs_repair(
    *,
    hypothesis_type: str,
    title: str,
    description: str | None,
    trigger_reason: str | None,
) -> bool:
    tr_l = (trigger_reason or "").lower()
    if any(
        x in tr_l
        for x in (
            "funnel_cart",
            "funnel_order:",
            "vs median",
            "отклонение",
            "metric_code",
            "ctr:",
        )
    ):
        return True
    if title.strip() in {"Поменять внутренний контент карточки", "Провести A/B-тест"}:
        return True
    desc = description or ""
    if "ниже медианы конкурентов на 20" in desc or "CTR ниже медианы" in desc:
        return True
    return False


def presentable_hypothesis_fields(
    *,
    hypothesis_type: str,
    title: str,
    description: str | None,
    trigger_reason: str | None,
    competitor_median_metrics: dict[str, Any] | None,
) -> tuple[str, str | None, str | None]:
    """Убирает из ответа API устаревший «технический» текст (без UPDATE в БД)."""
    if not hypothesis_api_copy_needs_repair(
        hypothesis_type=hypothesis_type,
        title=title,
        description=description,
        trigger_reason=trigger_reason,
    ):
        return title, description, trigger_reason

    mm = _mm_json_to_views(competitor_median_metrics)

    if hypothesis_type == "content_change":
        cart_ok = _funnel_conversion_plausible(mm.get("funnel_cart")) and _below_competitor_threshold(
            mm.get("funnel_cart")
        )
        order_ok = _funnel_conversion_plausible(mm.get("funnel_order")) and _below_competitor_threshold(
            mm.get("funnel_order")
        )
        if not (cart_ok or order_ok):
            return (
                "Обновить текст и медиа внутри карточки",
                "В сохранённом отчёте конверсии выглядят недостоверно: для строк «Конверсия …, %» ожидаются процентные пункты (обычно 0–100). "
                "Число вроде «медиана 200» не означает 200% — чаще всего в Excel попали штуки или не та строка. Обновите выгрузку «Сравнение карточек» из WB и снова запустите аналитику.",
                "Система сравнивает только процентные конверсии из отчёта WB. Перезагрузите сравнение и импорт — после этого подпись пересчитается.",
            )
        tr = _human_funnel_trigger(mm)
        return (
            "Обновить текст и медиа внутри карточки",
            "Конверсии в корзину или в заказ заметно слабее, чем у карточек в сравнении — имеет смысл переработать описание, характеристики и внутренний контент.",
            tr,
        )

    if hypothesis_type == "ab_test":
        ctr_m = mm.get("ctr")
        if not (_ctr_plausible(ctr_m) and _below_competitor_threshold(ctr_m)):
            return (
                "Проверить главное фото и инфографику (A/B)",
                "CTR в сохранённых метриках выглядит некорректно для сравнения в процентах — обновите отчёт из WB и перезапустите аналитику.",
                "После обновления импорта отчёта сравнения рекомендация по CTR пересчитается автоматически.",
            )
        tr = _human_ctr_trigger(ctr_m)
        return (
            "Проверить главное фото и инфографику (A/B)",
            "CTR заметно слабее, чем у карточек в сравнении — логично начать с визуала в поиске и карточке.",
            tr,
        )

    return title, description, trigger_reason


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
    Rule: logistics increase 20%+ vs avg7d (fallback: day-to-day if history is insufficient) -> tasks:
    - check_measurements
    - check_ktr
    """
    d_prev = date_for - timedelta(days=1)
    d_from = date_for - timedelta(days=7)
    rows: list[SkuDaily] = (
        db.query(SkuDaily)
        .filter(SkuDaily.user_id == user_id, SkuDaily.date >= d_from, SkuDaily.date <= date_for)
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
        if cur is None:
            continue
        cur_log = _to_float(cur.logistics)

        # Prefer avg7d (previous 7 days excluding current), fallback to prev-day.
        history_vals: list[float] = []
        for i in range(1, 8):
            d = date_for - timedelta(days=i)
            row = rr.get(d)
            if row is None:
                continue
            v = _to_float(row.logistics)
            if v > 0.0:
                history_vals.append(v)

        baseline_kind: str
        baseline_value: float | None
        if len(history_vals) >= 3:
            baseline_kind = "avg7d"
            baseline_value = sum(history_vals) / len(history_vals)
        else:
            prev = rr.get(d_prev)
            if prev is None:
                continue
            baseline_kind = "prev_day"
            baseline_value = _to_float(prev.logistics)

        if not baseline_value or baseline_value == 0.0:
            continue

        delta_pct = (cur_log - baseline_value) / abs(baseline_value) * 100.0
        if delta_pct < _LOGISTICS_DELTA_THRESHOLD_PCT:
            continue

        current = {
            "logistics_today": round(cur_log, 2),
            "logistics_baseline_kind": baseline_kind,
            "logistics_baseline_value": round(float(baseline_value), 2),
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
                description="Логистика заметно выше обычного уровня за последние дни — проверьте обмеры и КТР.",
                reason="Рост логистики более чем на 20% к базе",
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

