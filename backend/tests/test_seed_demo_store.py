"""
Tests for the demo-store seeder.

Split to match the module split:
- demo_store_data.py — pure Python, no DB/Celery imports: determinism, archetype mix, funnel
  math, dead-stock invariant. These run with zero fixtures.
- scripts/seed_demo_store.py — the DB-writing layer: only its safety guard is under test here
  (with a mocked `db.query(...)`, per the task spec), never a real database.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from scripts import demo_store_data as gen
from scripts import seed_demo_store as seeder

DATE_FROM = date(2025, 1, 1)
DATE_TO = date(2025, 6, 30)  # short range keeps the tests fast
SEED = 42


def _dataset():
    return gen.build_demo_dataset(seed=SEED, date_from=DATE_FROM, date_to=DATE_TO)


# ── demo_store_data: determinism ────────────────────────────────────────────


def test_same_seed_is_deterministic() -> None:
    a = _dataset()
    b = gen.build_demo_dataset(seed=SEED, date_from=DATE_FROM, date_to=DATE_TO)

    assert [p.nm_id for p in a.skus] == [p.nm_id for p in b.skus]
    assert [p.vendor_code for p in a.skus] == [p.vendor_code for p in b.skus]
    assert len(a.funnel_rows) == len(b.funnel_rows)
    assert len(a.sale_rows) == len(b.sale_rows)
    assert [(r.date, r.nm_id, r.open_count, r.cart_count, r.order_count) for r in a.funnel_rows] == [
        (r.date, r.nm_id, r.open_count, r.cart_count, r.order_count) for r in b.funnel_rows
    ]


def test_different_seed_gives_different_dataset() -> None:
    a = _dataset()
    c = gen.build_demo_dataset(seed=SEED + 1, date_from=DATE_FROM, date_to=DATE_TO)
    assert [p.nm_id for p in a.skus] != [p.nm_id for p in c.skus]


# ── demo_store_data: store profile ──────────────────────────────────────────


def test_archetype_counts_match_spec() -> None:
    ds = _dataset()
    counts = gen.archetype_counts(ds.skus)
    assert counts == {
        gen.ARCHETYPE_STAR: 5,
        gen.ARCHETYPE_STEADY: 15,
        gen.ARCHETYPE_DECLINER: 6,
        gen.ARCHETYPE_LAUNCH: 6,
        gen.ARCHETYPE_DEAD: 5,
        gen.ARCHETYPE_RETURN_SPIKE: 3,
        gen.ARCHETYPE_AD_BURN: 3,
        gen.ARCHETYPE_TOXIC_TRAFFIC: 1,
        gen.ARCHETYPE_PRICE_CUT: 2,
    }
    assert sum(counts.values()) == gen.TOTAL_SKUS == len(ds.skus)


def test_sku_nm_ids_unique_and_in_range() -> None:
    ds = _dataset()
    nm_ids = [p.nm_id for p in ds.skus]
    assert len(nm_ids) == len(set(nm_ids))
    assert all(gen.NM_ID_MIN <= nm <= gen.NM_ID_MAX for nm in nm_ids)


def test_sku_vendor_codes_unique() -> None:
    ds = _dataset()
    codes = [p.vendor_code for p in ds.skus]
    assert len(codes) == len(set(codes))


def test_cost_price_within_brief_range() -> None:
    ds = _dataset()
    assert all(300 <= p.cost_price <= 2500 for p in ds.skus)


def test_subjects_cover_all_six() -> None:
    ds = _dataset()
    subjects = {p.subject_name for p in ds.skus}
    assert subjects == set(gen.SUBJECTS)


# ── demo_store_data: funnel invariants ──────────────────────────────────────


def test_funnel_rows_are_monotonic_open_ge_cart_ge_order() -> None:
    ds = _dataset()
    assert ds.funnel_rows, "expected some funnel rows in a 6-month window"
    for r in ds.funnel_rows:
        assert r.open_count >= r.cart_count >= r.order_count >= 0


def test_funnel_rows_unique_per_date_and_sku() -> None:
    ds = _dataset()
    keys = [(r.date, r.nm_id) for r in ds.funnel_rows]
    assert len(keys) == len(set(keys))


def test_sales_never_exceed_cumulative_orders_per_sku() -> None:
    ds = _dataset()
    orders_by_nm: dict[int, int] = {}
    for r in ds.funnel_rows:
        orders_by_nm[r.nm_id] = orders_by_nm.get(r.nm_id, 0) + r.order_count

    sold_by_nm: dict[int, int] = {}
    for r in ds.sale_rows:
        if r.doc_type == "Продажа":
            sold_by_nm[r.nm_id] = sold_by_nm.get(r.nm_id, 0) + (r.quantity or 0)

    for nm_id, sold_qty in sold_by_nm.items():
        assert sold_qty <= orders_by_nm.get(nm_id, 0), f"nm_id={nm_id} sold {sold_qty} > ordered {orders_by_nm.get(nm_id, 0)}"


def test_dead_stock_skus_have_costs_without_sales() -> None:
    ds = _dataset()
    dead_nm_ids = {p.nm_id for p in ds.skus if p.archetype == gen.ARCHETYPE_DEAD}
    assert dead_nm_ids, "expected some dead-stock SKUs"

    dead_sale_rows = [r for r in ds.sale_rows if r.nm_id in dead_nm_ids and r.doc_type == "Продажа"]
    assert dead_sale_rows == [], "dead-stock SKUs must never generate a sale row"

    dead_cost_rows = [r for r in ds.sale_rows if r.nm_id in dead_nm_ids and (r.storage_fee or 0) > 0]
    assert dead_cost_rows, "dead-stock SKUs must still accrue storage costs"

    dead_funnel_orders = sum(r.order_count for r in ds.funnel_rows if r.nm_id in dead_nm_ids)
    assert dead_funnel_orders == 0


def test_returns_have_doc_type_vozvrat_and_positive_amount() -> None:
    ds = gen.build_demo_dataset(seed=SEED, date_from=date(2025, 1, 1), date_to=date(2026, 9, 24))
    returns = [r for r in ds.sale_rows if r.doc_type == "Возврат"]
    assert returns, "expected at least one return row over a full 21-month range"
    for r in returns:
        assert r.retail_price is not None and r.retail_price > 0
        assert r.quantity is not None and r.quantity > 0


def test_launch_skus_have_no_data_before_launch_day() -> None:
    ds = _dataset()
    for p in ds.skus:
        if p.archetype != gen.ARCHETYPE_LAUNCH or p.launch_day is None:
            continue
        rows_before = [r for r in ds.funnel_rows if r.nm_id == p.nm_id and r.date < p.launch_day]
        assert rows_before == []


# ── demo_store_data: safety-guard predicates (pure) ─────────────────────────


@pytest.mark.parametrize(
    "email,expected",
    [
        ("demo@sellerfocus.pro", True),
        ("Demo@Sellerfocus.pro", True),
        ("  demo2@sellerfocus.pro  ", True),
        ("seller@example.com", False),
        ("", False),
        ("demodog@x.com", True),
        ("notdemo@x.com", False),
    ],
)
def test_is_demo_email(email: str, expected: bool) -> None:
    assert gen.is_demo_email(email) is expected


@pytest.mark.parametrize(
    "key,expected",
    [(None, False), ("", False), ("   ", False), ("some-real-key", True)],
)
def test_user_has_wb_api_key(key: str | None, expected: bool) -> None:
    assert gen.user_has_wb_api_key(key) is expected


# ── seed_demo_store: safety guard against a mocked DB session ──────────────


def test_check_safety_guards_refuses_non_demo_email() -> None:
    db = MagicMock()
    with pytest.raises(SystemExit, match="must start with 'demo'"):
        seeder.check_safety_guards(db, "seller@example.com")
    db.query.assert_not_called()


def test_check_safety_guards_refuses_user_with_wb_api_key() -> None:
    db = MagicMock()
    existing_user = MagicMock()
    existing_user.wb_api_key = "real-wb-key-abc123"
    db.query.return_value.filter.return_value.first.return_value = existing_user

    with pytest.raises(SystemExit, match="already has a WB API key"):
        seeder.check_safety_guards(db, "demo@sellerfocus.pro")


def test_check_safety_guards_allows_new_demo_user() -> None:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    result = seeder.check_safety_guards(db, "demo@sellerfocus.pro")
    assert result is None


def test_check_safety_guards_allows_existing_demo_user_without_wb_key() -> None:
    db = MagicMock()
    existing_user = MagicMock()
    existing_user.wb_api_key = None
    db.query.return_value.filter.return_value.first.return_value = existing_user

    result = seeder.check_safety_guards(db, "demo@sellerfocus.pro")
    assert result is existing_user


# ── seed_demo_store: month-chunking helper (pure) ───────────────────────────


def test_iter_month_chunks_covers_range_without_gaps_or_overlap() -> None:
    chunks = seeder._iter_month_chunks(date(2025, 1, 15), date(2025, 3, 10))
    assert chunks == [
        (date(2025, 1, 15), date(2025, 1, 31)),
        (date(2025, 2, 1), date(2025, 2, 28)),
        (date(2025, 3, 1), date(2025, 3, 10)),
    ]


def test_iter_month_chunks_single_day() -> None:
    d = date(2025, 5, 5)
    assert seeder._iter_month_chunks(d, d) == [(d, d)]


def test_parse_args_defaults() -> None:
    args = seeder._parse_args(["--email", "demo@x.com"])
    assert args.email == "demo@x.com"
    assert args.date_from == "2025-01-01"
    assert args.date_to is None
    assert args.seed == 42
    assert args.reset is False
