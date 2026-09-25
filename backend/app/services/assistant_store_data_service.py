"""
Компактный снэпшот данных магазина для ИИ-чата (tool get_store_data).

Не отдаём модели сырые ORM-строки за произвольный период — агент мог бы попросить
годы данных и разнести контекст. Поэтому: период капается 60 днями, top/bottom SKU —
20 штуками суммарно, числа округляются. Вся арифметика (суммы, округление) сделана в
Python, ИИ только интерпретирует — тот же принцип, что и в daily_brief_service.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.pnl_daily import PnlDaily
from app.models.sku_daily import SkuDaily

MAX_DAYS = 60
MAX_SKUS_EACH_SIDE = 20


@dataclass(frozen=True)
class ParsedRange:
    date_from: date
    date_to: date


def _safe_float(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def parse_date_range(date_from: str | None, date_to: str | None) -> ParsedRange:
    """
    Парсинг + капы периода. По умолчанию — последние 30 дней (то же окно, что и в
    daily_brief_service). Если период шире MAX_DAYS — обрезаем слева (оставляем самые
    свежие дни).
    """
    today = date.today()
    try:
        d_to = date.fromisoformat(date_to) if date_to else today - timedelta(days=1)
    except ValueError:
        d_to = today - timedelta(days=1)
    try:
        d_from = date.fromisoformat(date_from) if date_from else d_to - timedelta(days=29)
    except ValueError:
        d_from = d_to - timedelta(days=29)

    if d_from > d_to:
        d_from, d_to = d_to, d_from

    if (d_to - d_from).days + 1 > MAX_DAYS:
        d_from = d_to - timedelta(days=MAX_DAYS - 1)

    return ParsedRange(date_from=d_from, date_to=d_to)


def get_store_data(db: Session, *, store_owner_id: str, date_from: str | None, date_to: str | None) -> dict[str, Any]:
    rng = parse_date_range(date_from, date_to)

    pnl_rows: list[PnlDaily] = (
        db.query(PnlDaily)
        .filter(
            PnlDaily.user_id == store_owner_id,
            PnlDaily.date >= rng.date_from,
            PnlDaily.date <= rng.date_to,
        )
        .order_by(PnlDaily.date)
        .all()
    )

    max_date_row = db.query(PnlDaily).filter(PnlDaily.user_id == store_owner_id).order_by(PnlDaily.date.desc()).first()
    data_max_date = max_date_row.date.isoformat() if max_date_row else None

    pnl_daily = [
        {
            "date": r.date.isoformat(),
            "revenue": round(_safe_float(r.revenue), 0),
            "commission": round(_safe_float(r.commission), 0),
            "logistics": round(_safe_float(r.logistics), 0),
            "storage": round(_safe_float(r.storage), 0),
            "penalties": round(_safe_float(r.penalties), 0),
            "ads_spend": round(_safe_float(r.ads_spend), 0),
            "cogs": round(_safe_float(r.cogs), 0),
            "tax": round(_safe_float(r.tax), 0),
            "margin": round(_safe_float(r.margin), 0),
        }
        for r in pnl_rows
    ]

    totals_keys = ["revenue", "commission", "logistics", "storage", "penalties", "ads_spend", "cogs", "tax", "margin"]
    period_totals = {k: round(sum(row[k] for row in pnl_daily), 0) for k in totals_keys}

    articles = {
        int(a.nm_id): {"vendor_code": a.vendor_code or f"[SKU:{a.nm_id}]", "name": a.name}
        for a in db.query(Article).filter(Article.user_id == store_owner_id).all()
    }

    sku_rows: list[SkuDaily] = (
        db.query(SkuDaily)
        .filter(
            SkuDaily.user_id == store_owner_id,
            SkuDaily.date >= rng.date_from,
            SkuDaily.date <= rng.date_to,
        )
        .all()
    )

    by_sku: dict[int, dict[str, float]] = {}
    funnel_totals = {"views": 0, "cart": 0, "orders": 0}
    for r in sku_rows:
        nm = int(r.nm_id)
        agg = by_sku.setdefault(
            nm,
            {"revenue": 0.0, "margin": 0.0, "orders": 0.0, "ads_spend": 0.0},
        )
        agg["revenue"] += _safe_float(r.revenue)
        agg["margin"] += _safe_float(r.margin)
        agg["orders"] += float(r.order_count or 0)
        agg["ads_spend"] += _safe_float(r.ads_spend)
        funnel_totals["views"] += int(r.open_count or 0)
        funnel_totals["cart"] += int(r.cart_count or 0)
        funnel_totals["orders"] += int(r.order_count or 0)

    sku_list: list[dict[str, Any]] = []
    for nm, agg in by_sku.items():
        meta = articles.get(nm, {"vendor_code": f"[SKU:{nm}]", "name": None})
        sku_list.append(
            {
                "nm_id": nm,
                "vendor_code": meta["vendor_code"],
                "name": meta["name"],
                "revenue": round(agg["revenue"], 0),
                "margin": round(agg["margin"], 0),
                "orders": int(agg["orders"]),
                "ads_spend": round(agg["ads_spend"], 0),
            }
        )

    sku_list_sorted = sorted(sku_list, key=lambda s: float(s["margin"]), reverse=True)
    half = MAX_SKUS_EACH_SIDE
    top_skus = sku_list_sorted[:half]
    bottom_skus = list(reversed(sku_list_sorted[-half:])) if len(sku_list_sorted) > half else []
    # avoid duplicating the same SKUs in both lists when the portfolio is small
    top_ids = {s["nm_id"] for s in top_skus}
    bottom_skus = [s for s in bottom_skus if s["nm_id"] not in top_ids]

    return {
        "date_from": rng.date_from.isoformat(),
        "date_to": rng.date_to.isoformat(),
        "data_max_date": data_max_date,
        "pnl_daily": pnl_daily,
        "period_totals": period_totals,
        "top_skus_by_margin": top_skus,
        "bottom_skus_by_margin": bottom_skus,
        "funnel_totals": funnel_totals,
    }


def today_and_data_max_date_hint(db: Session, *, store_owner_id: str) -> str:
    max_row = db.query(PnlDaily).filter(PnlDaily.user_id == store_owner_id).order_by(PnlDaily.date.desc()).first()
    max_date = max_row.date.isoformat() if max_row else "нет данных"
    return f"Сегодня: {datetime.now().date().isoformat()}. Данные P&L есть максимум по: {max_date}."
