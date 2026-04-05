"""
Ежедневная AI-сводка: pre-computation и вызов LLM.

Логика:
  1. Собрать данные из sku_daily за вчера + последние 30 дней.
  2. Разделить товары на "Запуск" (days_with_orders < 14) и "Рабочий".
  3. Вычислить агрегаты — Python делает всю арифметику, AI только интерпретирует.
  4. Вызвать LLM через OpenAI-совместимый API.
  5. Вернуть текст сводки.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.sku_daily import SkuDaily

logger = logging.getLogger(__name__)

# ─── Env-конфиг LLM ──────────────────────────────────────────────────────────
_AI_API_BASE = (os.getenv("AI_API_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
_AI_API_KEY = os.getenv("AI_API_KEY") or ""
_AI_MODEL = os.getenv("AI_MODEL") or "gpt-4o-mini"
_AI_TIMEOUT_SEC = float(os.getenv("AI_TIMEOUT_SEC") or "120")
_AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS") or "900")

# ─── Порог режима запуска ─────────────────────────────────────────────────────
LAUNCH_DAYS_THRESHOLD = 14   # < 14 дней с заказами → режим "Запуск"
ANOMALY_PCT_THRESHOLD = 15.0 # отклонение в % от avg7d, считаемое аномалией
TOP_ESTABLISHED_SKUS = 5     # сколько "рабочих" SKU передаём AI (AI выберет топ-3)


# ─── Вспомогательные функции ─────────────────────────────────────────────────

def _safe_float(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _pct(val: float, ref: float) -> float | None:
    """Дельта в % относительно ref. None если ref == 0."""
    if ref == 0.0:
        return None
    return round((val - ref) / abs(ref) * 100, 1)


def _trend(series: list[float]) -> str:
    """
    Сравниваем среднее первой половины и второй половины серии.
    Возвращает 'up' / 'flat' / 'down'.
    Нужно минимум 2 точки.
    """
    if len(series) < 2:
        return "flat"
    mid = len(series) // 2
    first_avg = mean(series[:mid]) if mid else series[0]
    second_avg = mean(series[mid:])
    delta_pct = (second_avg - first_avg) / (abs(first_avg) + 1e-9) * 100
    if delta_pct > 10:
        return "up"
    if delta_pct < -10:
        return "down"
    return "flat"


def _roi(margin: float, ads_spend: float) -> float | None:
    if ads_spend <= 0:
        return None
    return round(margin / ads_spend * 100, 1)


# ─── Основная функция pre-computation ────────────────────────────────────────

@dataclass
class DailyBriefPayload:
    date_for: date
    portfolio: dict[str, Any] = field(default_factory=dict)
    launch_skus: list[dict[str, Any]] = field(default_factory=list)
    established_skus: list[dict[str, Any]] = field(default_factory=list)


def build_daily_brief_payload(
    db: Session,
    user_id: str,
    date_for: date | None = None,
) -> DailyBriefPayload:
    """
    Собрать все предвычисленные агрегаты для AI.
    date_for по умолчанию = вчера.
    """
    yesterday = date_for or (date.today() - timedelta(days=1))
    window_start_30 = yesterday - timedelta(days=29)  # 30-дневное окно
    window_start_7 = yesterday - timedelta(days=6)    # 7-дневное окно

    # Артикулы пользователя (для vendor_code)
    articles = {
        int(a.nm_id): a.vendor_code or f"[SKU:{a.nm_id}]"
        for a in db.query(Article).filter(Article.user_id == user_id).all()
    }

    # Загрузить sku_daily за 30 дней
    rows: list[SkuDaily] = (
        db.query(SkuDaily)
        .filter(
            SkuDaily.user_id == user_id,
            SkuDaily.date >= window_start_30,
            SkuDaily.date <= yesterday,
        )
        .order_by(SkuDaily.date)
        .all()
    )

    if not rows:
        return DailyBriefPayload(date_for=yesterday)

    # Сгруппировать по nm_id → список строк по дням
    by_sku: dict[int, list[SkuDaily]] = {}
    for r in rows:
        nm = int(r.nm_id)
        by_sku.setdefault(nm, []).append(r)

    # ── Портфель: агрегаты по дням ──────────────────────────────────────────
    # Суммы по всем SKU за каждый день для портфельных показателей
    portfolio_by_day: dict[date, dict[str, float]] = {}
    for r in rows:
        from typing import cast as _cast
        d: date = _cast(date, r.date)
        if d not in portfolio_by_day:
            portfolio_by_day[d] = {
                "revenue": 0.0, "margin": 0.0,
                "ads_spend": 0.0, "logistics": 0.0,
            }
        portfolio_by_day[d]["revenue"] += _safe_float(r.revenue)
        portfolio_by_day[d]["margin"] += _safe_float(r.margin)
        portfolio_by_day[d]["ads_spend"] += _safe_float(r.ads_spend)
        portfolio_by_day[d]["logistics"] += _safe_float(r.logistics)

    # avg7d портфеля
    last7_days = [
        v for d, v in portfolio_by_day.items() if window_start_7 <= d <= yesterday
    ]
    avg7d_revenue = mean([d["revenue"] for d in last7_days]) if last7_days else 0.0
    avg7d_margin = mean([d["margin"] for d in last7_days]) if last7_days else 0.0
    avg7d_ads = mean([d["ads_spend"] for d in last7_days]) if last7_days else 0.0

    yest_portfolio = portfolio_by_day.get(yesterday, {
        "revenue": 0.0, "margin": 0.0, "ads_spend": 0.0, "logistics": 0.0,
    })

    portfolio: dict[str, Any] = {
        "date": yesterday.isoformat(),
        "revenue_yesterday": round(yest_portfolio["revenue"], 0),
        "revenue_avg7d": round(avg7d_revenue, 0),
        "revenue_delta_pct": _pct(yest_portfolio["revenue"], avg7d_revenue),
        "margin_yesterday": round(yest_portfolio["margin"], 0),
        "margin_avg7d": round(avg7d_margin, 0),
        "margin_delta_pct": _pct(yest_portfolio["margin"], avg7d_margin),
        "ads_spend_yesterday": round(yest_portfolio["ads_spend"], 0),
        "ads_spend_avg7d": round(avg7d_ads, 0),
        "ads_roi_yesterday": _roi(yest_portfolio["margin"], yest_portfolio["ads_spend"]),
    }

    # ── Классификация SKU ────────────────────────────────────────────────────
    launch_skus: list[dict[str, Any]] = []
    established_skus_candidates: list[dict[str, Any]] = []

    for nm_id, sku_rows in by_sku.items():
        vendor_code = articles.get(nm_id, f"[SKU:{nm_id}]")
        last_30 = sorted(sku_rows, key=lambda r: r.date)

        days_with_orders = sum(
            1 for r in last_30
            if (r.order_count or 0) > 0
        )
        launch_mode = days_with_orders < LAUNCH_DAYS_THRESHOLD

        if launch_mode:
            _process_launch_sku(nm_id, vendor_code, last_30, launch_skus)
        else:
            _process_established_sku(
                nm_id, vendor_code, last_30, yesterday, window_start_7,
                established_skus_candidates,
            )

    # Топ N рабочих SKU по рублёвому отклонению маржи
    established_skus_candidates.sort(
        key=lambda s: abs(s.get("margin_delta_rub", 0.0)), reverse=True
    )
    top_established = established_skus_candidates[:TOP_ESTABLISHED_SKUS]

    return DailyBriefPayload(
        date_for=yesterday,
        portfolio=portfolio,
        launch_skus=launch_skus,
        established_skus=top_established,
    )


def _process_launch_sku(
    nm_id: int,
    vendor_code: str,
    rows: list[SkuDaily],
    out: list[dict[str, Any]],
) -> None:
    days_total = len(rows)
    profitable_days = sum(1 for r in rows if _safe_float(r.margin) > 0)
    views_series = [float(r.open_count or 0) for r in rows]
    # CR в заказ = orders / views (только дни с трафиком)
    cr_series = [
        r.order_count / r.open_count
        for r in rows
        if (r.open_count or 0) > 0 and (r.order_count or 0) > 0
    ]
    # Cost per order = ads_spend / orders (только дни с заказами)
    cpo_series = [
        _safe_float(r.ads_spend) / r.order_count
        for r in rows
        if (r.order_count or 0) > 0
    ]

    views_trend = _trend(views_series)
    cr_trend = _trend(cr_series) if cr_series else "flat"
    cpo_trend = _trend(cpo_series) if cpo_series else "flat"

    # Вердикт-сигнал (AI может скорректировать, но получает подсказку)
    if profitable_days >= 2 and views_trend == "up":
        verdict_signal = "ПРОДОЛЖАТЬ"
    elif days_total >= 7 and profitable_days == 0 and views_trend != "up":
        verdict_signal = "СТОП-СИГНАЛ"
    else:
        verdict_signal = "НАБЛЮДАТЬ"

    out.append({
        "mode": "launch",
        "nm_id": nm_id,
        "vendor_code": vendor_code,
        "days_total": days_total,
        "days_with_orders": sum(1 for r in rows if (r.order_count or 0) > 0),
        "profitable_days": profitable_days,
        "views_trend": views_trend,
        "cr_trend": cr_trend,
        "cost_per_order_trend": cpo_trend,
        "verdict_signal": verdict_signal,
        # Последние значения для контекста
        "latest_open_count": int(rows[-1].open_count or 0),
        "latest_order_count": int(rows[-1].order_count or 0),
        "latest_margin": round(_safe_float(rows[-1].margin), 0),
        "latest_ads_spend": round(_safe_float(rows[-1].ads_spend), 0),
    })


def _process_established_sku(
    nm_id: int,
    vendor_code: str,
    rows: list[SkuDaily],
    yesterday: date,
    window_start_7: date,
    out: list[dict[str, Any]],
) -> None:
    last7 = [r for r in rows if r.date >= window_start_7]
    yest_row = next((r for r in rows if r.date == yesterday), None)

    if not yest_row or not last7:
        return  # нет данных за вчера — не включаем

    def avg7(attr: str) -> float:
        vals = [_safe_float(getattr(r, attr)) for r in last7]
        return mean(vals) if vals else 0.0

    yest_revenue = _safe_float(yest_row.revenue)
    yest_margin = _safe_float(yest_row.margin)
    yest_ads = _safe_float(yest_row.ads_spend)
    yest_logistics = _safe_float(yest_row.logistics)
    yest_orders = int(yest_row.order_count or 0)
    yest_views = int(yest_row.open_count or 0)

    avg7_revenue = avg7("revenue")
    avg7_margin = avg7("margin")
    avg7_ads = avg7("ads_spend")
    avg7_logistics = avg7("logistics")
    avg7_orders = avg7("order_count")
    avg7_views = avg7("open_count")

    revenue_delta_pct = _pct(yest_revenue, avg7_revenue)
    margin_delta_pct = _pct(yest_margin, avg7_margin)
    ads_delta_pct = _pct(yest_ads, avg7_ads)
    logistics_delta_pct = _pct(yest_logistics, avg7_logistics)
    orders_delta_pct = _pct(yest_orders, avg7_orders)
    views_delta_pct = _pct(yest_views, avg7_views)

    margin_delta_rub = round(yest_margin - avg7_margin, 0)

    # Проверка на значимое отклонение
    deltas = [
        abs(d) for d in [
            revenue_delta_pct, margin_delta_pct, ads_delta_pct, logistics_delta_pct,
        ]
        if d is not None
    ]
    if not deltas or max(deltas) < ANOMALY_PCT_THRESHOLD:
        return  # всё в норме — не включаем

    # Кросс-метрические аномалии (подсказки AI)
    cross_hints: list[str] = []
    if ads_delta_pct is not None and orders_delta_pct is not None:
        if ads_delta_pct > 20 and orders_delta_pct < -10:
            waste = round(yest_ads - avg7_ads, 0)
            cross_hints.append(
                f"реклама +{ads_delta_pct:.0f}% при заказах {orders_delta_pct:.0f}% "
                f"(возможно сожжено ₽{waste:.0f} впустую)"
            )
    if views_delta_pct is not None:
        avg7_cr = avg7_orders / (avg7_views + 1e-9)
        yest_cr = yest_orders / (yest_views + 1e-9) if yest_views else 0
        cr_delta_pct = _pct(yest_cr, avg7_cr)
        if (
            views_delta_pct is not None
            and views_delta_pct > 15
            and cr_delta_pct is not None
            and cr_delta_pct < -15
        ):
            cross_hints.append(
                f"трафик +{views_delta_pct:.0f}% при CR {cr_delta_pct:.0f}% "
                "(токсичный трафик)"
            )
    if logistics_delta_pct is not None and orders_delta_pct is not None:
        if logistics_delta_pct > 20 and orders_delta_pct < -10:
            cross_hints.append(
                f"логистика +{logistics_delta_pct:.0f}% при заказах {orders_delta_pct:.0f}%"
                " (возможно спираль возвратов)"
            )

    # ── Gross margin (прибыль ДО вычета рекламы) ────────────────────────────
    # Это честный показатель: работает ли продукт сам по себе, без рекламных трат.
    gross_margin_yesterday = round(yest_margin + yest_ads, 0)
    avg7d_gross_margin = round(avg7_margin + avg7_ads, 0)
    gross_margin_delta_pct = _pct(gross_margin_yesterday, avg7d_gross_margin)

    # ── Вариант Б: дни с рекламой vs без рекламы в 7-дневном окне ───────────
    # Минимальный порог ₽100 чтобы отсеять нулевые/технические записи.
    ADS_THRESHOLD = 100.0
    days_with_ads = [r for r in last7 if _safe_float(r.ads_spend) > ADS_THRESHOLD]
    days_without_ads = [r for r in last7 if _safe_float(r.ads_spend) <= ADS_THRESHOLD]

    avg_margin_with_ads: float | None = (
        round(mean([_safe_float(r.margin) for r in days_with_ads]), 0)
        if days_with_ads else None
    )
    avg_margin_without_ads: float | None = (
        round(mean([_safe_float(r.margin) for r in days_without_ads]), 0)
        if days_without_ads else None
    )

    # Сигнал эффективности рекламы: только если есть оба типа дней для сравнения
    ads_efficiency_signal: str | None = None
    if avg_margin_with_ads is not None and avg_margin_without_ads is not None:
        diff = avg_margin_with_ads - avg_margin_without_ads
        if diff > 500:
            ads_efficiency_signal = "помогает"       # маржа выше в дни с рекламой
        elif diff < -500:
            ads_efficiency_signal = "мешает"          # маржа ниже в дни с рекламой
        else:
            ads_efficiency_signal = "нейтрально"
    elif avg_margin_with_ads is not None and avg_margin_without_ads is None:
        ads_efficiency_signal = "только_с_рекламой"  # нет дней без рекламы для сравнения

    # ── Python принимает решение по рекламе — AI только озвучивает ──────────
    ads_decision: str
    ads_decision_amount: float | None = None
    ads_decision_reason: str

    if gross_margin_yesterday < 0:
        # Продукт убыточен даже без рекламы — реклама не виновата
        ads_decision = "не_трогать_продукт_убыточен"
        ads_decision_reason = (
            f"продукт в убытке ₽{abs(gross_margin_yesterday):.0f} даже без рекламы "
            "— проблема в цене/себестоимости/логистике, не в рекламе"
        )
    elif yest_margin < 0 and gross_margin_yesterday > 0:
        # Продукт прибылен без рекламы, но реклама съедает прибыль
        loss = round(abs(yest_margin), 0)
        if ads_efficiency_signal in ("мешает", "нейтрально"):
            ads_decision = "снизить_бюджет"
            ads_decision_amount = loss
            ads_decision_reason = (
                f"продукт прибылен без рекламы (gross_margin ₽{gross_margin_yesterday:.0f}), "
                f"но реклама создаёт убыток ₽{loss:.0f} — "
                f"снизить дневной бюджет рекламы на ₽{loss:.0f}"
            )
        elif ads_efficiency_signal == "помогает":
            ads_decision = "наблюдать_реклама_помогает"
            ads_decision_reason = (
                "реклама системно улучшает маржу по неделе — вчера разовый сбой, не трогать"
            )
        else:
            ads_decision = "наблюдать_нет_данных"
            ads_decision_reason = "нет дней без рекламы для сравнения — наблюдать"
    else:
        # margin > 0 — реклама окупается, всё нормально
        ads_decision = "не_трогать_окупается"
        ads_decision_reason = "реклама окупается — не трогать"

    out.append({
        "mode": "established",
        "nm_id": nm_id,
        "vendor_code": vendor_code,
        "margin_delta_rub": margin_delta_rub,
        "yesterday": {
            "revenue": round(yest_revenue, 0),
            "margin": round(yest_margin, 0),
            "gross_margin": gross_margin_yesterday,
            "ads_spend": round(yest_ads, 0),
            "logistics": round(yest_logistics, 0),
            "orders": yest_orders,
        },
        "avg7d": {
            "revenue": round(avg7_revenue, 0),
            "margin": round(avg7_margin, 0),
            "gross_margin": avg7d_gross_margin,
            "ads_spend": round(avg7_ads, 0),
            "logistics": round(avg7_logistics, 0),
            "orders": round(avg7_orders, 1),
        },
        "deltas_pct": {
            "revenue": revenue_delta_pct,
            "margin": margin_delta_pct,
            "gross_margin": gross_margin_delta_pct,
            "ads_spend": ads_delta_pct,
            "logistics": logistics_delta_pct,
        },
        "ads_days_analysis": {
            "days_with_ads": len(days_with_ads),
            "days_without_ads": len(days_without_ads),
            "avg_margin_with_ads": avg_margin_with_ads,
            "avg_margin_without_ads": avg_margin_without_ads,
            "ads_efficiency_signal": ads_efficiency_signal,
        },
        # Решение принято Python — AI только использует это в отчёте
        "ads_decision": ads_decision,
        "ads_decision_amount": ads_decision_amount,
        "ads_decision_reason": ads_decision_reason,
        "cross_metric_hints": cross_hints,
    })


# ─── Промпт ───────────────────────────────────────────────────────────────────

def _build_prompt(payload: DailyBriefPayload) -> str:
    import json

    p = payload.portfolio
    date_str = payload.date_for.isoformat()

    # Сокращённый сериализатор (None → "н/д")
    def _j(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, default=str)

    launch_count = len(payload.launch_skus)
    established_count = len(payload.established_skus)

    prompt = f"""Ты — AI аналитик ежедневной оперативной сводки для продавца на Wildberries.

ЗАДАЧА: Написать краткую оперативную сводку строго до 200 слов по данным за {date_str}.
Все расчёты уже выполнены — не пересчитывай, только интерпретируй.

СЛОВАРЬ:
- margin = чистая прибыль (после всех вычетов: себестоимость, логистика, реклама, комиссия, налог)
- gross_margin = прибыль ДО вычета рекламы (margin + ads_spend). Показывает: работает ли продукт сам по себе.
- revenue = выручка (до вычетов)
- avg7d = среднее значение за последние 7 дней (база для сравнения с вчерашним днём)
- ads_days_analysis = сравнение средней маржи в дни когда реклама работала vs когда не работала
- ads_efficiency_signal: "помогает" / "мешает" / "нейтрально" / "только_с_рекламой" (нет дней без рекламы)
- verdict_signal = предварительный сигнал для новинки, можешь скорректировать на основе данных

ДАННЫЕ ПОРТФЕЛЯ (итого):
{_j(p)}

НОВИНКИ В ЗАПУСКЕ ({launch_count} товаров):
{_j(payload.launch_skus)}

РАБОЧИЙ ПОРТФЕЛЬ — аномальные SKU ({established_count} позиций, уже отфильтрованы по >15% отклонению):
{_j(payload.established_skus)}

ПРАВИЛА ОТВЕТА:
1. Новинки: для каждой оцени траекторию (воронка + окупаемость). Итог — вердикт ПРОДОЛЖАТЬ / НАБЛЮДАТЬ / СТОП-СИГНАЛ.
2. Рабочий портфель: упомяни максимум 3 позиции с наибольшим рублёвым эффектом.
   Если cross_metric_hints не пустой — используй как основу для инсайта.
3. Не упоминай метрики, которые в норме (нет отклонения).
4. Для каждой аномалии: ФАКТ → ЧТО ОЗНАЧАЕТ → ₽-эффект.
5. Домыслы без цифр запрещены. Никаких ссылок на "сезонность" или "алгоритмы ВБ".
6. Строго до 200 слов в итоговом ответе.

ФОРМАТ ОТВЕТА (строго, без отступлений):

📊 ИТОГ ДНЯ — {date_str}
Выручка: ₽X (▲/▼Y% vs неделя) | Прибыль: ₽X (▲/▼Y%) | Реклама: ₽X, ROI: X%

---

🚀 НОВИНКИ В ЗАПУСКЕ [{launch_count} товаров]
[Артикул]: Воронка [тренд], прибыльных дней: X из Y → ВЕРДИКТ

---

📦 РАБОЧИЙ ПОРТФЕЛЬ
[Артикул]: [факт] → [что означает] → ₽[эффект]

---

⚡ СЕГОДНЯ
1. [АРТИКУЛ]: [глагол действия] — [что именно сделать, цифра]
2. [АРТИКУЛ]: [глагол действия] — [что именно сделать, цифра]

Правила ACTION PLAN (читай внимательно):

ПРАВИЛО 1 — НОВИНКИ (launch mode):
  - ЗАПРЕЩЕНО рекомендовать "остановить рекламу" только из-за отрицательной маржи — это норма на запуске.
  - "остановить рекламу" для новинки — ТОЛЬКО если: реклама идёт И orders == 0 за последние 5+ дней (нет ни одного заказа вообще).
  - Иначе: смотри cost_per_order_trend. Если "down" — продолжать. Если "up" — наблюдать.
  - Нельзя рекомендовать конкретную ставку или конкретный бюджет для новинки.

ПРАВИЛО 2 — РАБОЧИЕ ТОВАРЫ (established mode), решение по рекламе:
  Решение по рекламе УЖЕ ПРИНЯТО Python. В поле ads_decision / ads_decision_reason каждого SKU — готовый вывод.
  Твоя задача: прочитай ads_decision_reason и включи его в отчёт и ACTION PLAN.

  Коды решений:
    "снизить_бюджет" → В ACTION PLAN: "снизить дневной бюджет рекламы на ₽X" где X = поле ads_decision_amount (число из данных, не придумывай)
    "не_трогать_продукт_убыточен" → В ACTION PLAN: "проверить ценообразование/себестоимость" (реклама здесь не главная проблема)
    "не_трогать_реклама_помогает" → реклама окупается, не трогать
    "наблюдать_реклама_помогает" → разовый сбой, не менять рекламу
    "наблюдать_нет_данных" → не давать рекомендации по бюджету, написать "наблюдать"
    "не_трогать_окупается" → реклама окупается, не трогать

- ЗАПРЕЩЕНО: называть конкретную ставку в рублях (CPM/CPC) — этих данных нет, любая цифра будет выдумкой.
- ЗАПРЕЩЕНО: "обратить внимание", "рассмотреть", "оптимизировать", "улучшить".
- Каждый пункт начинается с [АРТИКУЛ].
- Только то, что можно сделать за 5 минут в личном кабинете WB сегодня.

Если новинок нет — блок 🚀 пропусти. Если нет аномалий в рабочем портфеле — напиши «Рабочий портфель в норме».
"""
    return prompt


# ─── Вызов LLM ────────────────────────────────────────────────────────────────

def call_ai(prompt: str) -> str:
    """
    Вызов OpenAI-совместимого API.
    Поддерживает любой провайдер с совместимым /chat/completions эндпоинтом.
    """
    if not _AI_API_KEY:
        raise ValueError(
            "AI_API_KEY не задан. Установите переменную окружения AI_API_KEY."
        )

    url = f"{_AI_API_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {_AI_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": _AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": _AI_MAX_TOKENS,
        "temperature": 0.3,
    }

    response = httpx.post(url, headers=headers, json=body, timeout=_AI_TIMEOUT_SEC)
    response.raise_for_status()
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise ValueError(f"Пустой ответ от LLM: {data}")
    text: str = choices[0].get("message", {}).get("content") or ""
    return text.strip()


# ─── Публичный интерфейс ──────────────────────────────────────────────────────

def generate_brief_text(
    db: Session,
    user_id: str,
    date_for: date | None = None,
) -> str:
    """
    Собрать payload, сформировать промпт, вызвать AI и вернуть текст.
    Исключения пробрасываются наверх — caller сохраняет статус error.
    """
    payload = build_daily_brief_payload(db, user_id, date_for)

    if not payload.launch_skus and not payload.established_skus and not payload.portfolio:
        return "Нет данных за вчера для формирования сводки."

    prompt = _build_prompt(payload)
    logger.info(
        "daily_brief: generating for user=%s date=%s "
        "launch_skus=%d established_skus=%d",
        user_id, payload.date_for, len(payload.launch_skus), len(payload.established_skus),
    )
    return call_ai(prompt)
