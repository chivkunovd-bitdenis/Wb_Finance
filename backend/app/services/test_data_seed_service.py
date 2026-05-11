from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.funnel_daily import FunnelDaily
from app.models.pnl_daily import PnlDaily
from app.models.sku_daily import SkuDaily


@dataclass(frozen=True)
class SeedResult:
    user_id: str
    nm_id: int
    date_from: date
    date_to: date
    days: int
    used_reference_nm_id: int | None


def _stable_rng_seed(*parts: str) -> int:
    raw = "|".join(parts).encode("utf-8")
    h = hashlib.sha256(raw).hexdigest()
    # fit into Random seed range
    return int(h[:16], 16)


def _d(x: float | int | Decimal) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def _pick_reference_nm_id(db: Session, *, user_id: str, target_nm_id: int, date_from: date, date_to: date) -> int | None:
    row = (
        db.query(SkuDaily.nm_id)
        .filter(
            SkuDaily.user_id == user_id,
            SkuDaily.date >= date_from,
            SkuDaily.date <= date_to,
            SkuDaily.nm_id != target_nm_id,
        )
        .group_by(SkuDaily.nm_id)
        .order_by(SkuDaily.nm_id.asc())
        .first()
    )
    return int(row[0]) if row else None


def seed_test_article_timeseries(
    db: Session,
    *,
    user_id: str,
    nm_id: int,
    vendor_code: str = "ТЕСТ",
    name: str = "Тестовый товар (autogen)",
    days: int = 14,
    date_to: date | None = None,
) -> SeedResult:
    """
    Idempotently seed a single article and its daily data to make UI tabs testable.

    Writes:
    - articles (vendor_code)
    - funnel_daily (WB funnel tab)
    - sku_daily (SKU time-series: includes logistics + funnel columns)
    - pnl_daily (P&L time-series)
    """
    if days <= 0:
        raise ValueError("days must be positive")

    dt = date_to or date.today()
    df = dt - timedelta(days=days - 1)

    # Reference is best-effort: use any other nm_id with data in same window.
    ref_nm_id = _pick_reference_nm_id(db, user_id=user_id, target_nm_id=nm_id, date_from=df, date_to=dt)

    # Deterministic randomness for reproducibility between runs.
    rng = random.Random(_stable_rng_seed(user_id, str(nm_id), df.isoformat(), dt.isoformat()))

    # Baselines (either derived from reference SKU rows or safe defaults).
    base_price = _d(2500)
    base_orders = 6
    base_open = 260
    base_cart = 28

    if ref_nm_id is not None:
        ref_rows = (
            db.query(SkuDaily)
            .filter(SkuDaily.user_id == user_id, SkuDaily.nm_id == ref_nm_id, SkuDaily.date >= df, SkuDaily.date <= dt)
            .all()
        )
        if ref_rows:
            prices: list[Decimal] = []
            orders: list[int] = []
            opens: list[int] = []
            carts: list[int] = []
            for r in ref_rows:
                if r.revenue is not None and r.order_count and r.order_count > 0:
                    prices.append(_d(r.revenue) / _d(r.order_count))
                if r.order_count is not None:
                    orders.append(int(r.order_count))
                if r.open_count is not None:
                    opens.append(int(r.open_count))
                if r.cart_count is not None:
                    carts.append(int(r.cart_count))
            if prices:
                base_price = sum(prices) / _d(len(prices))
            if orders:
                base_orders = max(1, round(sum(orders) / len(orders)))
            if opens:
                base_open = max(50, round(sum(opens) / len(opens)))
            if carts:
                base_cart = max(5, round(sum(carts) / len(carts)))

    # Upsert Article.
    art_stmt = insert(Article).values(
        user_id=user_id,
        nm_id=nm_id,
        vendor_code=vendor_code,
        name=name,
        cost_price=_d(0),
    )
    art_stmt = art_stmt.on_conflict_do_update(
        constraint="uq_articles_user_nm",
        set_={
            "vendor_code": art_stmt.excluded.vendor_code,
            "name": art_stmt.excluded.name,
        },
    )
    db.execute(art_stmt)

    # Per-day upserts.
    for i in range(days):
        day = df + timedelta(days=i)

        # Gentle seasonality + noise, but deterministic.
        weekday_boost = 1.10 if day.weekday() in {4, 5} else 1.0  # Fri/Sat slightly higher
        noise = 0.85 + rng.random() * 0.35  # 0.85..1.20

        order_count = max(0, int(round(base_orders * weekday_boost * noise)))
        open_count = max(order_count * 15, int(round(base_open * weekday_boost * (0.9 + rng.random() * 0.3))))
        cart_count = max(order_count, int(round(base_cart * weekday_boost * (0.9 + rng.random() * 0.3))))

        # Funnel sums
        order_sum = (_d(order_count) * base_price * _d(0.98 + rng.random() * 0.08)).quantize(Decimal("0.01"))
        cr_to_cart = (Decimal(cart_count) / Decimal(open_count)).quantize(Decimal("0.0001")) if open_count else _d(0)
        cr_to_order = (Decimal(order_count) / Decimal(open_count)).quantize(Decimal("0.0001")) if open_count else _d(0)
        buyout_percent = _d(84 + rng.random() * 10).quantize(Decimal("0.01")) if order_count else _d(0)

        # P&L-ish
        revenue = order_sum
        commission = (revenue * _d(0.17)).quantize(Decimal("0.01"))
        logistics = (revenue * _d(0.06)).quantize(Decimal("0.01"))
        penalties = (revenue * _d(0.002) * _d(0 if rng.random() < 0.75 else 1)).quantize(Decimal("0.01"))
        storage = (revenue * _d(0.01)).quantize(Decimal("0.01"))
        ads_spend = (revenue * _d(0.08)).quantize(Decimal("0.01"))
        cogs = (revenue * _d(0.35)).quantize(Decimal("0.01"))
        tax = (revenue * _d(0.06)).quantize(Decimal("0.01"))
        margin = (revenue - commission - logistics - penalties - storage - ads_spend - cogs - tax).quantize(Decimal("0.01"))

        funnel_stmt = insert(FunnelDaily).values(
            user_id=user_id,
            date=day,
            nm_id=nm_id,
            vendor_code=vendor_code,
            open_count=open_count,
            cart_count=cart_count,
            order_count=order_count,
            order_sum=order_sum,
            buyout_percent=buyout_percent,
            cr_to_cart=cr_to_cart,
            cr_to_order=cr_to_order,
        )
        funnel_stmt = funnel_stmt.on_conflict_do_update(
            constraint="uq_funnel_daily_user_date_nm",
            set_={
                "vendor_code": funnel_stmt.excluded.vendor_code,
                "open_count": funnel_stmt.excluded.open_count,
                "cart_count": funnel_stmt.excluded.cart_count,
                "order_count": funnel_stmt.excluded.order_count,
                "order_sum": funnel_stmt.excluded.order_sum,
                "buyout_percent": funnel_stmt.excluded.buyout_percent,
                "cr_to_cart": funnel_stmt.excluded.cr_to_cart,
                "cr_to_order": funnel_stmt.excluded.cr_to_order,
            },
        )
        db.execute(funnel_stmt)

        sku_stmt = insert(SkuDaily).values(
            user_id=user_id,
            date=day,
            nm_id=nm_id,
            revenue=revenue,
            commission=commission,
            logistics=logistics,
            penalties=penalties,
            storage=storage,
            ads_spend=ads_spend,
            cogs=cogs,
            tax=tax,
            margin=margin,
            open_count=open_count,
            cart_count=cart_count,
            order_count=order_count,
            order_sum=order_sum,
        )
        sku_stmt = sku_stmt.on_conflict_do_update(
            constraint="uq_sku_daily_user_date_nm",
            set_={
                "revenue": sku_stmt.excluded.revenue,
                "commission": sku_stmt.excluded.commission,
                "logistics": sku_stmt.excluded.logistics,
                "penalties": sku_stmt.excluded.penalties,
                "storage": sku_stmt.excluded.storage,
                "ads_spend": sku_stmt.excluded.ads_spend,
                "cogs": sku_stmt.excluded.cogs,
                "tax": sku_stmt.excluded.tax,
                "margin": sku_stmt.excluded.margin,
                "open_count": sku_stmt.excluded.open_count,
                "cart_count": sku_stmt.excluded.cart_count,
                "order_count": sku_stmt.excluded.order_count,
                "order_sum": sku_stmt.excluded.order_sum,
            },
        )
        db.execute(sku_stmt)

        pnl_stmt = insert(PnlDaily).values(
            user_id=user_id,
            date=day,
            revenue=revenue,
            commission=commission,
            logistics=logistics,
            penalties=penalties,
            storage=storage,
            ads_spend=ads_spend,
            cogs=cogs,
            tax=tax,
            operation_expenses=_d(0),
            margin=margin,
        )
        pnl_stmt = pnl_stmt.on_conflict_do_update(
            constraint="uq_pnl_daily_user_date",
            set_={
                "revenue": pnl_stmt.excluded.revenue,
                "commission": pnl_stmt.excluded.commission,
                "logistics": pnl_stmt.excluded.logistics,
                "penalties": pnl_stmt.excluded.penalties,
                "storage": pnl_stmt.excluded.storage,
                "ads_spend": pnl_stmt.excluded.ads_spend,
                "cogs": pnl_stmt.excluded.cogs,
                "tax": pnl_stmt.excluded.tax,
                "operation_expenses": pnl_stmt.excluded.operation_expenses,
                "margin": pnl_stmt.excluded.margin,
            },
        )
        db.execute(pnl_stmt)

    db.commit()

    return SeedResult(
        user_id=user_id,
        nm_id=nm_id,
        date_from=df,
        date_to=dt,
        days=days,
        used_reference_nm_id=ref_nm_id,
    )

