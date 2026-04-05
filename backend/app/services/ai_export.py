from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.funnel_daily import FunnelDaily
from app.models.sku_daily import SkuDaily
from app.models.user import User


@dataclass(frozen=True)
class ExportConfig:
    user_id: str
    date_from: date
    date_to: date


def _d(v: Decimal | float | int | None) -> float:
    if v is None:
        return 0.0
    return float(v)


def _round2(v: float) -> float:
    return round(v, 2)


def _pct(numerator: float, denominator: float, digits: int = 2) -> float:
    if denominator == 0:
        return 0.0
    return round((numerator / denominator) * 100, digits)


def _daterange(start: date, end: date) -> list[date]:
    out: list[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def _day_nonzero_signature(day: dict[str, Any]) -> float:
    """
    Для фильтрации "полностью нулевых" товаров:
    суммируем основные числовые метрики funnel+finance.
    """
    funnel = cast(dict[str, Any], day.get("funnel") or {})
    finance = cast(dict[str, Any], day.get("finance") or {})
    vals: list[float] = []
    vals.extend(
        [
            float(funnel.get("views", 0) or 0),
            float(funnel.get("basket", 0) or 0),
            float(funnel.get("orders", 0) or 0),
            float(funnel.get("order_sum", 0.0) or 0.0),
            float(funnel.get("sales_sum", 0.0) or 0.0),
        ]
    )
    vals.extend(
        [
            float(finance.get("revenue", 0.0) or 0.0),
            float(finance.get("order_sum", 0.0) or 0.0),
            float(finance.get("commission", 0.0) or 0.0),
            float(finance.get("logistics", 0.0) or 0.0),
            float(finance.get("penalties", 0.0) or 0.0),
            float(finance.get("cogs", 0.0) or 0.0),
            float(finance.get("ads_spend", 0.0) or 0.0),
            float(finance.get("margin", 0.0) or 0.0),
        ]
    )
    return sum(abs(v) for v in vals)


def resolve_export_user(db: Session, user_id: str | None) -> str:
    if user_id:
        row = db.get(User, user_id)
        if row is None:
            raise ValueError(f"user_id not found: {user_id}")
        return str(row.id)

    rows = db.query(User.id).order_by(User.created_at.asc()).all()
    if len(rows) == 1:
        return str(rows[0][0])
    if not rows:
        raise ValueError("No users found in DB")
    raise ValueError(
        "Multiple users found. Pass --user-id explicitly."
    )


def resolve_date_to(db: Session, user_id: str, date_from: date) -> date:
    sku_max = (
        db.query(func.max(SkuDaily.date))
        .filter(SkuDaily.user_id == user_id, SkuDaily.date >= date_from)
        .scalar()
    )
    funnel_max = (
        db.query(func.max(FunnelDaily.date))
        .filter(FunnelDaily.user_id == user_id, FunnelDaily.date >= date_from)
        .scalar()
    )
    candidates = [d for d in (sku_max, funnel_max) if d is not None]
    if not candidates:
        raise ValueError(f"No SKU/Funnel rows for user {user_id} since {date_from.isoformat()}")
    return max(candidates)


def build_ai_products_payload(db: Session, config: ExportConfig) -> dict[str, Any]:
    article_rows = db.query(Article).filter(Article.user_id == config.user_id).all()
    sku_rows = (
        db.query(SkuDaily)
        .filter(
            SkuDaily.user_id == config.user_id,
            SkuDaily.date >= config.date_from,
            SkuDaily.date <= config.date_to,
        )
        .all()
    )
    funnel_rows = (
        db.query(FunnelDaily)
        .filter(
            FunnelDaily.user_id == config.user_id,
            FunnelDaily.date >= config.date_from,
            FunnelDaily.date <= config.date_to,
        )
        .all()
    )

    article_map: dict[int, dict[str, Any]] = {}
    all_nm_ids: set[int] = set()
    for a in article_rows:
        nm = int(a.nm_id)
        all_nm_ids.add(nm)
        article_map[nm] = {
            "vendor_code": a.vendor_code,
            "name": a.name,
            "subject_name": a.subject_name,
        }

    sku_map: dict[tuple[int, date], SkuDaily] = {}
    for r in sku_rows:
        nm = int(r.nm_id)
        all_nm_ids.add(nm)
        row_date = cast(date, r.date)
        sku_map[(nm, row_date)] = r

    funnel_map: dict[tuple[int, date], FunnelDaily] = {}
    for r in funnel_rows:
        nm = int(r.nm_id)
        all_nm_ids.add(nm)
        row_date = cast(date, r.date)
        funnel_map[(nm, row_date)] = r

    days = _daterange(config.date_from, config.date_to)
    products: list[dict[str, Any]] = []

    for nm_id in sorted(all_nm_ids):
        a = article_map.get(nm_id, {})
        item: dict[str, Any] = {
            "nm_id": nm_id,
            "vendor_code": a.get("vendor_code"),
            "name": a.get("name"),
            "subject_name": a.get("subject_name"),
            "daily": [],
        }

        nonzero_total = 0.0
        for d in days:
            sku = sku_map.get((nm_id, d))
            funnel = funnel_map.get((nm_id, d))

            revenue = _d(sku.revenue) if sku else 0.0
            commission = _d(sku.commission) if sku else 0.0
            logistics = _d(sku.logistics) if sku else 0.0
            penalties = _d(sku.penalties) if sku else 0.0
            cogs = _d(sku.cogs) if sku else 0.0
            ads_spend = _d(sku.ads_spend) if sku else 0.0
            margin = _d(sku.margin) if sku else 0.0
            fin_order_sum = _d(sku.order_sum) if sku else 0.0

            views = int(funnel.open_count or 0) if funnel else 0
            basket = int(funnel.cart_count or 0) if funnel else 0
            orders = int(funnel.order_count or 0) if funnel else 0
            buyout_percent = _d(funnel.buyout_percent) if funnel else 0.0
            funnel_order_sum = _d(funnel.order_sum) if funnel else 0.0

            cr_basket = basket / views if views > 0 else 0.0
            cr_order = orders / basket if basket > 0 else 0.0
            cr_total = cr_basket * cr_order

            day = {
                "date": d.isoformat(),
                "funnel": {
                    "views": views,
                    "basket": basket,
                    "orders": orders,
                    "buyout_percent": _round2(buyout_percent),
                    "cr_basket_percent": _pct(basket, views),
                    "cr_order_percent": _pct(orders, basket),
                    "cr_total_percent": _round2(cr_total * 100),
                    "order_sum": _round2(funnel_order_sum),
                    "sales_sum": _round2(revenue),
                },
                "finance": {
                    "revenue": _round2(revenue),
                    "order_sum": _round2(fin_order_sum),
                    "commission": _round2(commission),
                    "commission_percent": _pct(commission, revenue),
                    "logistics": _round2(logistics),
                    "logistics_percent": _pct(logistics, revenue),
                    "penalties": _round2(penalties),
                    "cogs": _round2(cogs),
                    "cogs_percent": _pct(cogs, revenue),
                    "ads_spend": _round2(ads_spend),
                    "ads_percent": _pct(ads_spend, revenue),
                    "margin": _round2(margin),
                    "margin_percent": _pct(margin, revenue),
                    "roi_percent": _pct(margin, cogs),
                },
            }
            item["daily"].append(day)
            nonzero_total += _day_nonzero_signature(day)

        # Оптимизация для GPT: выкидываем товары, у которых по всем дням всё нули.
        if nonzero_total == 0.0:
            continue
        products.append(item)

    return {
        "meta": {
            "purpose": "LLM-ready product analytics payload",
            "date_from": config.date_from.isoformat(),
            "date_to": config.date_to.isoformat(),
            "array_root": "products",
            "daily_shape": {
                "funnel": [
                    "views",
                    "basket",
                    "orders",
                    "buyout_percent",
                    "cr_basket_percent",
                    "cr_order_percent",
                    "cr_total_percent",
                    "order_sum",
                    "sales_sum",
                ],
                "finance": [
                    "revenue",
                    "order_sum",
                    "commission",
                    "commission_percent",
                    "logistics",
                    "logistics_percent",
                    "penalties",
                    "cogs",
                    "cogs_percent",
                    "ads_spend",
                    "ads_percent",
                    "margin",
                    "margin_percent",
                    "roi_percent",
                ],
            },
            "optimization_notes": [
                "Only tab-visible metrics are included to reduce noise.",
                "All percentages are precomputed to reduce model arithmetic errors.",
                "Consistent numeric types and 2-digit rounding improve parsing quality.",
            ],
        },
        "products": products,
    }
