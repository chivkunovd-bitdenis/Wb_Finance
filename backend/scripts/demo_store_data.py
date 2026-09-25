"""
Deterministic, DB-free generator for the demo Wildberries store dataset.

This module contains ONLY pure Python: no imports of `app.*`, `celery_app.*`, SQLAlchemy or
anything that touches a database, a running Postgres or Celery. That is intentional — it lets
`backend/tests/test_seed_demo_store.py` exercise the generator (archetype mix, funnel math,
determinism, safety-guard predicates) without a database.

`seed_demo_store.py` is the thin DB-writing layer on top: it calls `build_demo_dataset()` here
and then bulk-inserts the resulting rows, exactly mirroring what a real Wildberries apparel
seller's raw data would look like (see module docstring there for the full contract).

Everything is driven by `random.Random(seed)` only — no `datetime.now()`, no environment reads —
so the same `(seed, date_from, date_to)` always produces byte-identical output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from random import Random

# ── Store profile constants ──────────────────────────────────────────────────

SUBJECTS: list[str] = ["Леггинсы", "Футболки", "Худи", "Джинсы", "Платья", "Спортивные костюмы"]

ARCHETYPE_STAR = "star"
ARCHETYPE_STEADY = "steady"
ARCHETYPE_DECLINER = "decliner"
ARCHETYPE_LAUNCH = "launch"
ARCHETYPE_DEAD = "dead"
ARCHETYPE_RETURN_SPIKE = "return_spike"
ARCHETYPE_AD_BURN = "ad_burn"
ARCHETYPE_TOXIC_TRAFFIC = "toxic_traffic"
ARCHETYPE_PRICE_CUT = "price_cut"

# Order matters: determines the deterministic draw order for a given seed.
ARCHETYPE_COUNTS: list[tuple[str, int]] = [
    (ARCHETYPE_STAR, 5),
    (ARCHETYPE_STEADY, 15),
    (ARCHETYPE_DECLINER, 6),
    (ARCHETYPE_LAUNCH, 6),
    (ARCHETYPE_DEAD, 5),
    (ARCHETYPE_RETURN_SPIKE, 3),
    (ARCHETYPE_AD_BURN, 3),
    (ARCHETYPE_TOXIC_TRAFFIC, 1),
    (ARCHETYPE_PRICE_CUT, 2),
]
TOTAL_SKUS = sum(n for _, n in ARCHETYPE_COUNTS)

NM_ID_MIN = 700_000_000
NM_ID_MAX = 999_999_999

# cost_price range per subject (₽), kept inside the overall 300-2500 range from the brief.
SUBJECT_COST_RANGE: dict[str, tuple[int, int]] = {
    "Леггинсы": (350, 900),
    "Футболки": (300, 700),
    "Худи": (900, 1900),
    "Джинсы": (700, 1600),
    "Платья": (500, 1500),
    "Спортивные костюмы": (1000, 2500),
}

SUBJECT_STYLES_LAT: dict[str, list[str]] = {
    "Леггинсы": ["STIRRUP", "PUSHUP", "WAIST", "FLEECE", "BASIC"],
    "Футболки": ["OVERSIZE", "BASIC", "RIB", "CROP", "LONGSL"],
    "Худи": ["OVERSIZE", "ZIP", "BASIC", "CROP"],
    "Джинсы": ["MOM", "SLIM", "WIDE", "BAGGY", "SKINNY"],
    "Платья": ["MIDI", "MAXI", "SLIP", "WRAP", "MINI"],
    "Спортивные костюмы": ["TRACK", "ZIP", "OVERSIZE", "BASIC"],
}
SUBJECT_STYLES_RU: dict[str, list[str]] = {
    "Леггинсы": ["с утяжкой", "пуш-ап", "на флисе", "бесшовные", "высокая посадка"],
    "Футболки": ["оверсайз", "базовая", "в рубчик", "укороченная", "с длинным рукавом"],
    "Худи": ["оверсайз", "на молнии", "базовое", "укороченное"],
    "Джинсы": ["мом", "слим", "широкие", "багги", "скинни"],
    "Платья": ["миди", "макси", "комбинация", "на запах", "мини"],
    "Спортивные костюмы": ["трек", "на молнии", "оверсайз", "базовый"],
}
COLORS_LAT = ["BLK", "WHT", "GRY", "BLU", "BEIGE", "OLIVE", "PINK", "BORDO"]
COLORS_RU = ["чёрный", "белый", "серый", "синий", "бежевый", "хаки", "розовый", "бордовый"]

SUBJECT_PREFIX = {
    "Леггинсы": "LEG",
    "Футболки": "TSH",
    "Худи": "HUD",
    "Джинсы": "JNS",
    "Платья": "DRS",
    "Спортивные костюмы": "SUIT",
}


@dataclass(frozen=True)
class SkuProfile:
    nm_id: int
    vendor_code: str
    name: str
    subject_name: str
    archetype: str
    cost_price: float
    base_price: float
    commission_rate: float
    # Archetype-specific knobs (all optional, interpreted by the row generator below).
    launch_day: date | None = None
    launch_promising: bool = True
    cr_to_cart_base: float = 0.11
    cr_to_order_base: float = 0.33
    buyout_base: float = 0.72
    price_cut_from: date | None = None
    price_cut_pct: float = 0.0
    return_spike_days: tuple[date, ...] = field(default_factory=tuple)
    ad_burn_from: date | None = None
    toxic_from: date | None = None
    toxic_to: date | None = None


def _round_price(v: float) -> float:
    """Round to a plausible retail price ending in 0/9 (cosmetic only)."""
    v = max(100.0, v)
    tens = round(v / 10.0) * 10
    return float(tens - 1) if tens >= 100 else float(tens)


def generate_sku_profiles(rng: Random, *, date_from: date, date_to: date) -> list[SkuProfile]:
    """Build the deterministic ~45-SKU store profile (mixed apparel lifecycle archetypes)."""
    used_nm_ids: set[int] = set()
    used_codes: set[str] = set()
    profiles: list[SkuProfile] = []

    archetype_sequence: list[str] = []
    for archetype, count in ARCHETYPE_COUNTS:
        archetype_sequence.extend([archetype] * count)

    total_days = (date_to - date_from).days

    for idx, archetype in enumerate(archetype_sequence):
        subject = SUBJECTS[idx % len(SUBJECTS)]

        nm_id = rng.randint(NM_ID_MIN, NM_ID_MAX)
        while nm_id in used_nm_ids:
            nm_id = rng.randint(NM_ID_MIN, NM_ID_MAX)
        used_nm_ids.add(nm_id)

        style_lat = rng.choice(SUBJECT_STYLES_LAT[subject])
        style_ru = SUBJECT_STYLES_RU[subject][SUBJECT_STYLES_LAT[subject].index(style_lat)]
        color_idx = rng.randrange(len(COLORS_LAT))
        color_lat = COLORS_LAT[color_idx]
        color_ru = COLORS_RU[color_idx]

        if idx % 2 == 0:
            vendor_code = f"{SUBJECT_PREFIX[subject]}-{style_lat}-{color_lat}"
        else:
            vendor_code = f"{subject}_{style_ru}_{color_ru}".replace(" ", "-")
        suffix = 1
        base_code = vendor_code
        while vendor_code in used_codes:
            suffix += 1
            vendor_code = f"{base_code}-{suffix}"
        used_codes.add(vendor_code)

        name = f"{subject} {style_ru} {color_ru}"

        cost_lo, cost_hi = SUBJECT_COST_RANGE[subject]
        cost_price = float(rng.randint(cost_lo, cost_hi))
        markup = rng.uniform(2.2, 3.4)
        base_price = _round_price(cost_price * markup)
        commission_rate = rng.uniform(0.25, 0.35)

        cr_to_cart_base = rng.uniform(0.08, 0.15)
        cr_to_order_base = rng.uniform(0.25, 0.45)
        buyout_base = rng.uniform(0.60, 0.85)

        launch_day = None
        launch_promising = True
        price_cut_from = None
        price_cut_pct = 0.0
        return_spike_days: tuple[date, ...] = ()
        ad_burn_from = None
        toxic_from = None
        toxic_to = None

        if archetype == ARCHETYPE_LAUNCH:
            offset = rng.randint(1, 25)
            launch_day = date_to - timedelta(days=offset)
            launch_index = sum(1 for p in archetype_sequence[:idx] if p == ARCHETYPE_LAUNCH)
            launch_promising = launch_index % 3 != 2  # 4 promising, 2 dead out of 6
        elif archetype == ARCHETYPE_PRICE_CUT:
            cut_window = min(45, max(10, total_days // 4))
            price_cut_from = date_to - timedelta(days=cut_window)
            price_cut_pct = rng.uniform(0.15, 0.25)
        elif archetype == ARCHETYPE_RETURN_SPIKE:
            n_spikes = rng.randint(3, 6)
            days_pool = sorted(rng.sample(range(total_days + 1), k=min(n_spikes, total_days + 1)))
            return_spike_days = tuple(date_from + timedelta(days=d) for d in days_pool)
        elif archetype == ARCHETYPE_AD_BURN:
            window = min(60, max(14, total_days // 6))
            ad_burn_from = date_to - timedelta(days=window)
        elif archetype == ARCHETYPE_TOXIC_TRAFFIC:
            window = min(30, max(10, total_days // 10))
            toxic_to = date_to - timedelta(days=rng.randint(0, 10))
            toxic_from = toxic_to - timedelta(days=window)

        profiles.append(
            SkuProfile(
                nm_id=nm_id,
                vendor_code=vendor_code,
                name=name,
                subject_name=subject,
                archetype=archetype,
                cost_price=cost_price,
                base_price=base_price,
                commission_rate=commission_rate,
                launch_day=launch_day,
                launch_promising=launch_promising,
                cr_to_cart_base=cr_to_cart_base,
                cr_to_order_base=cr_to_order_base,
                buyout_base=buyout_base,
                price_cut_from=price_cut_from,
                price_cut_pct=price_cut_pct,
                return_spike_days=return_spike_days,
                ad_burn_from=ad_burn_from,
                toxic_from=toxic_from,
                toxic_to=toxic_to,
            )
        )

    return profiles


def archetype_counts(profiles: list[SkuProfile]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in profiles:
        out[p.archetype] = out.get(p.archetype, 0) + 1
    return out


# ── Seasonality ───────────────────────────────────────────────────────────────

def _weekly_multiplier(d: date) -> float:
    # Mon=0 ... Sun=6. Weekend peak.
    return 1.35 if d.weekday() >= 5 else 1.0


def _calendar_multiplier(d: date) -> float:
    if d.month == 11 and d.day >= 24:
        return 2.2  # Black Friday week
    if d.month == 12:
        return 1.5  # December gift season
    if d.month == 1:
        return 0.6  # post-holiday dip
    return 1.0


def _subject_seasonality(subject: str, d: date) -> float:
    if subject == "Худи":
        # Winter-heavy: peak Nov-Feb, low Jun-Aug.
        winter = {11: 1.5, 12: 1.6, 1: 1.5, 2: 1.3, 3: 1.0, 4: 0.85, 5: 0.7, 6: 0.55, 7: 0.55, 8: 0.65, 9: 0.9, 10: 1.2}
        return winter[d.month]
    if subject == "Платья":
        # Summer-heavy: peak May-Aug, low Dec-Feb.
        summer = {1: 0.55, 2: 0.6, 3: 0.8, 4: 1.05, 5: 1.4, 6: 1.6, 7: 1.6, 8: 1.45, 9: 1.05, 10: 0.85, 11: 0.65, 12: 0.6}
        return summer[d.month]
    return 1.0


def _season_multiplier(subject: str, d: date) -> float:
    return _weekly_multiplier(d) * _calendar_multiplier(d) * _subject_seasonality(subject, d)


# ── Per-SKU demand model ────────────────────────────────────────────────────

def _archetype_base_orders(profile: SkuProfile, d: date, *, date_from: date, date_to: date) -> float:
    total_days = max(1, (date_to - date_from).days)
    t = (d - date_from).days / total_days  # 0..1 progress through the range

    if profile.archetype == ARCHETYPE_STAR:
        return 6.0 * (1.0 + 0.6 * t)  # slow, stable growth
    if profile.archetype == ARCHETYPE_STEADY:
        return 3.0
    if profile.archetype == ARCHETYPE_DECLINER:
        decline_start = date_to - timedelta(days=60)
        if d < decline_start:
            return 5.0
        decline_t = (d - decline_start).days / 60.0
        return max(0.1, 5.0 * (1.0 - decline_t))
    if profile.archetype == ARCHETYPE_LAUNCH:
        if profile.launch_day is None or d < profile.launch_day:
            return 0.0
        days_live = (d - profile.launch_day).days
        if profile.launch_promising:
            return 0.5 + 0.35 * days_live
        return 0.4 if days_live < 3 else 0.1
    if profile.archetype == ARCHETYPE_DEAD:
        return 0.0
    if profile.archetype == ARCHETYPE_RETURN_SPIKE:
        return 3.5
    if profile.archetype == ARCHETYPE_AD_BURN:
        return 3.0  # flat demand — ads spend rises independently
    if profile.archetype == ARCHETYPE_TOXIC_TRAFFIC:
        return 3.0
    if profile.archetype == ARCHETYPE_PRICE_CUT:
        if profile.price_cut_from is not None and d >= profile.price_cut_from:
            return 3.0 * (1.0 + profile.price_cut_pct * 3.0)  # elasticity bump
        return 3.0
    return 2.0


def _current_price(profile: SkuProfile, d: date) -> float:
    if profile.price_cut_from is not None and d >= profile.price_cut_from:
        return _round_price(profile.base_price * (1.0 - profile.price_cut_pct))
    return profile.base_price


@dataclass
class DailyFunnelRow:
    date: date
    nm_id: int
    vendor_code: str
    open_count: int
    cart_count: int
    order_count: int
    order_sum: float
    buyout_percent: float
    cr_to_cart: float
    cr_to_order: float


@dataclass
class RawSaleRow:
    date: date
    nm_id: int
    doc_type: str | None
    retail_price: float | None
    ppvz_for_pay: float | None
    delivery_rub: float | None
    penalty: float | None
    additional_payment: float | None
    storage_fee: float | None
    quantity: int | None


@dataclass
class RawAdRow:
    date: date
    nm_id: int
    campaign_id: int
    spend: float


def generate_rows_for_sku(
    profile: SkuProfile, rng: Random, *, date_from: date, date_to: date
) -> tuple[list[DailyFunnelRow], list[RawSaleRow], list[RawAdRow]]:
    """Generate funnel/sales/ads rows for one SKU across [date_from, date_to]."""
    funnel_rows: list[DailyFunnelRow] = []
    sale_rows: list[RawSaleRow] = []
    ad_rows: list[RawAdRow] = []

    campaign_id = rng.randint(1_000_000, 9_999_999)

    listed_from = profile.launch_day if profile.archetype == ARCHETYPE_LAUNCH else date_from

    d = date_from
    while d <= date_to:
        if listed_from is not None and d < listed_from:
            d += timedelta(days=1)
            continue

        base = _archetype_base_orders(profile, d, date_from=date_from, date_to=date_to)
        season = _season_multiplier(profile.subject_name, d)
        noise = rng.uniform(0.75, 1.3)
        expected_orders = base * season * noise
        order_count = max(0, round(expected_orders + rng.uniform(-0.4, 0.4)))

        cr_to_order = min(0.95, max(0.05, profile.cr_to_order_base * rng.uniform(0.85, 1.15)))
        cr_to_cart = min(0.95, max(0.03, profile.cr_to_cart_base * rng.uniform(0.85, 1.15)))

        if order_count > 0:
            cart_count = max(order_count, round(order_count / cr_to_order))
        else:
            # Even with zero orders a listed SKU still gets some browsing traffic.
            cart_count = round(rng.uniform(0, 2)) if profile.archetype != ARCHETYPE_DEAD else 0
        if cart_count > 0:
            open_count = max(cart_count, round(cart_count / cr_to_cart))
        else:
            open_count = round(rng.uniform(0, 5)) if profile.archetype != ARCHETYPE_DEAD else 0

        # Toxic traffic: extra opens with no matching cart growth (traffic without genuine interest).
        if profile.toxic_from is not None and profile.toxic_to is not None and profile.toxic_from <= d <= profile.toxic_to:
            open_count += round(rng.uniform(80, 220))

        price = _current_price(profile, d)
        order_sum = round(order_count * price, 2)
        buyout_fraction = min(0.95, max(0.3, profile.buyout_base * rng.uniform(0.9, 1.1)))

        if open_count or cart_count or order_count:
            funnel_rows.append(
                DailyFunnelRow(
                    date=d,
                    nm_id=profile.nm_id,
                    vendor_code=profile.vendor_code,
                    open_count=open_count,
                    cart_count=cart_count,
                    order_count=order_count,
                    order_sum=order_sum,
                    buyout_percent=round(buyout_fraction * 100.0, 2),
                    cr_to_cart=round(cr_to_cart, 4),
                    cr_to_order=round(cr_to_order, 4),
                )
            )

        # ── Sales: orders convert into a "Продажа" row 2-6 days later, bounded by buyout%. ──
        if order_count > 0:
            buyout_qty = round(order_count * buyout_fraction)
            if buyout_qty > 0:
                lag = rng.randint(2, 6)
                sale_date = d + timedelta(days=lag)
                if sale_date <= date_to:
                    retail_total = round(buyout_qty * price, 2)
                    ppvz_total = round(retail_total * (1.0 - profile.commission_rate), 2)
                    delivery = round(buyout_qty * rng.uniform(45.0, 95.0), 2)
                    penalty = round(retail_total * 0.01, 2) if rng.random() < 0.03 else None
                    sale_rows.append(
                        RawSaleRow(
                            date=sale_date,
                            nm_id=profile.nm_id,
                            doc_type="Продажа",
                            retail_price=retail_total,
                            ppvz_for_pay=ppvz_total,
                            delivery_rub=delivery,
                            penalty=penalty,
                            additional_payment=None,
                            storage_fee=None,
                            quantity=buyout_qty,
                        )
                    )

        # ── Returns: extra negative-revenue rows on the archetype's spike days. ──
        if d in profile.return_spike_days:
            ret_qty = max(1, round(rng.uniform(2, 8)))
            ret_total = round(ret_qty * price, 2)
            sale_rows.append(
                RawSaleRow(
                    date=d,
                    nm_id=profile.nm_id,
                    doc_type="Возврат",
                    retail_price=ret_total,
                    ppvz_for_pay=round(ret_total * (1.0 - profile.commission_rate), 2),
                    delivery_rub=round(ret_qty * rng.uniform(45.0, 95.0), 2),
                    penalty=None,
                    additional_payment=None,
                    storage_fee=None,
                    quantity=ret_qty,
                )
            )

        # ── Storage: WB charges a daily paid-storage fee per listed SKU, sale or not. ──
        if profile.archetype == ARCHETYPE_DEAD:
            # "Storage spiral": backlog fee grows the longer the item sits unsold.
            days_dead = (d - date_from).days
            storage_fee = round(8.0 + 0.05 * days_dead + rng.uniform(0, 4), 2)
            sale_rows.append(
                RawSaleRow(
                    date=d,
                    nm_id=profile.nm_id,
                    doc_type=None,
                    retail_price=None,
                    ppvz_for_pay=None,
                    delivery_rub=(round(rng.uniform(40, 90), 2) if rng.random() < 0.05 else None),
                    penalty=None,
                    additional_payment=None,
                    storage_fee=storage_fee,
                    quantity=None,
                )
            )
        elif rng.random() < 0.9:
            storage_fee = round(rng.uniform(2.0, 12.0), 2)
            sale_rows.append(
                RawSaleRow(
                    date=d,
                    nm_id=profile.nm_id,
                    doc_type=None,
                    retail_price=None,
                    ppvz_for_pay=None,
                    delivery_rub=None,
                    penalty=None,
                    additional_payment=None,
                    storage_fee=storage_fee,
                    quantity=None,
                )
            )

        # ── Ads: ~12 SKUs get raw_ads rows with campaign on/off periods. ──
        if profile.archetype == ARCHETYPE_AD_BURN and profile.ad_burn_from is not None and d >= profile.ad_burn_from:
            days_in = (d - profile.ad_burn_from).days
            spend = round(60.0 + 4.5 * days_in + rng.uniform(-15, 15), 2)
            ad_rows.append(RawAdRow(date=d, nm_id=profile.nm_id, campaign_id=campaign_id, spend=max(0.0, spend)))
        elif profile.archetype in (ARCHETYPE_STAR, ARCHETYPE_STEADY, ARCHETYPE_TOXIC_TRAFFIC):
            # Campaign-like on/off: active roughly 10 days on, 8 days off.
            cycle = (d - date_from).days % 18
            if cycle < 10 and rng.random() < 0.8:
                spend = round(rng.uniform(80.0, 260.0), 2)
                ad_rows.append(RawAdRow(date=d, nm_id=profile.nm_id, campaign_id=campaign_id, spend=spend))

        d += timedelta(days=1)

    return funnel_rows, sale_rows, ad_rows


# ── Operational expenses & plans ────────────────────────────────────────────

@dataclass
class OperationalExpenseRow:
    date: date
    amount: float
    comment: str


def generate_operational_expenses(rng: Random, *, date_from: date, date_to: date) -> list[OperationalExpenseRow]:
    rows: list[OperationalExpenseRow] = []
    month = date(date_from.year, date_from.month, 1)
    while month <= date_to:
        if month >= date_from:
            rent = round(rng.uniform(45_000, 75_000), 2)
            salaries = round(rng.uniform(180_000, 320_000), 2)
            packaging = round(rng.uniform(15_000, 35_000), 2)
            fulfillment = round(rng.uniform(20_000, 45_000), 2)
            rows.append(OperationalExpenseRow(date=month, amount=rent, comment="Аренда склада"))
            rows.append(OperationalExpenseRow(date=month, amount=salaries, comment="Зарплата команды"))
            rows.append(OperationalExpenseRow(date=month, amount=packaging, comment="Упаковка и расходники"))
            rows.append(OperationalExpenseRow(date=month, amount=fulfillment, comment="Фулфилмент/сборка заказов"))
        if month.month == 12:
            month = date(month.year + 1, 1, 1)
        else:
            month = date(month.year, month.month + 1, 1)
    return rows


@dataclass
class MonthlyPlanRow:
    month: date
    metric_key: str
    value: float


def generate_monthly_plans(rng: Random) -> list[MonthlyPlanRow]:
    """Plans for all 12 months of 2026 (future months included — a plan precedes the fact),
    achievable with some misses once the month's fact is in."""
    rows: list[MonthlyPlanRow] = []
    for m in range(1, 13):
        month = date(2026, m, 1)
        revenue_plan = round(rng.uniform(1_600_000, 2_600_000), 2)
        commission_pct = round(rng.uniform(24, 32), 2)
        logistics_pct = round(rng.uniform(4, 8), 2)
        ads_pct = round(rng.uniform(5, 12), 2)
        storage_pct = round(rng.uniform(1, 4), 2)
        rows.append(MonthlyPlanRow(month=month, metric_key="revenue", value=revenue_plan))
        rows.append(MonthlyPlanRow(month=month, metric_key="orders_sum", value=round(revenue_plan * rng.uniform(1.02, 1.12), 2)))
        rows.append(MonthlyPlanRow(month=month, metric_key="commission_pct", value=commission_pct))
        rows.append(MonthlyPlanRow(month=month, metric_key="logistics_pct", value=logistics_pct))
        rows.append(MonthlyPlanRow(month=month, metric_key="ads_pct", value=ads_pct))
        rows.append(MonthlyPlanRow(month=month, metric_key="storage_pct", value=storage_pct))
        rows.append(MonthlyPlanRow(month=month, metric_key="commission", value=round(revenue_plan * commission_pct / 100.0, 2)))
        rows.append(MonthlyPlanRow(month=month, metric_key="logistics", value=round(revenue_plan * logistics_pct / 100.0, 2)))
        rows.append(MonthlyPlanRow(month=month, metric_key="ads_spend", value=round(revenue_plan * ads_pct / 100.0, 2)))
        rows.append(MonthlyPlanRow(month=month, metric_key="storage", value=round(revenue_plan * storage_pct / 100.0, 2)))
        rows.append(MonthlyPlanRow(month=month, metric_key="cogs", value=round(revenue_plan * rng.uniform(0.32, 0.42), 2)))
        rows.append(MonthlyPlanRow(month=month, metric_key="tax", value=round(revenue_plan * 0.06, 2)))
        rows.append(MonthlyPlanRow(month=month, metric_key="operation_expenses", value=round(rng.uniform(260_000, 420_000), 2)))
        rows.append(MonthlyPlanRow(month=month, metric_key="margin", value=round(revenue_plan * rng.uniform(0.08, 0.18), 2)))
    return rows


# ── Top-level dataset ────────────────────────────────────────────────────────

@dataclass
class DemoDataset:
    seed: int
    date_from: date
    date_to: date
    skus: list[SkuProfile]
    funnel_rows: list[DailyFunnelRow]
    sale_rows: list[RawSaleRow]
    ad_rows: list[RawAdRow]
    operational_expenses: list[OperationalExpenseRow]
    monthly_plans: list[MonthlyPlanRow]


def build_demo_dataset(*, seed: int, date_from: date, date_to: date) -> DemoDataset:
    if date_to < date_from:
        raise ValueError("date_to must be >= date_from")

    rng = Random(seed)
    skus = generate_sku_profiles(rng, date_from=date_from, date_to=date_to)

    funnel_rows: list[DailyFunnelRow] = []
    sale_rows: list[RawSaleRow] = []
    ad_rows: list[RawAdRow] = []
    for profile in skus:
        f_rows, s_rows, a_rows = generate_rows_for_sku(profile, rng, date_from=date_from, date_to=date_to)
        funnel_rows.extend(f_rows)
        sale_rows.extend(s_rows)
        ad_rows.extend(a_rows)

    operational_expenses = generate_operational_expenses(rng, date_from=date_from, date_to=date_to)
    monthly_plans = generate_monthly_plans(rng)

    return DemoDataset(
        seed=seed,
        date_from=date_from,
        date_to=date_to,
        skus=skus,
        funnel_rows=funnel_rows,
        sale_rows=sale_rows,
        ad_rows=ad_rows,
        operational_expenses=operational_expenses,
        monthly_plans=monthly_plans,
    )


# ── Safety-guard predicates (pure; used by both the CLI script and tests) ──────

def is_demo_email(email: str) -> bool:
    return (email or "").strip().lower().startswith("demo")


def user_has_wb_api_key(wb_api_key: str | None) -> bool:
    return bool(wb_api_key and wb_api_key.strip())
