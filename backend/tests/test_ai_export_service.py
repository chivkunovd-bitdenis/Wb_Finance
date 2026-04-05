from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.ai_export import ExportConfig, build_ai_products_payload


@dataclass
class _ArticleRow:
    user_id: str
    nm_id: int
    vendor_code: str | None
    name: str | None
    subject_name: str | None


@dataclass
class _SkuRow:
    user_id: str
    date: date
    nm_id: int
    revenue: Decimal | None
    commission: Decimal | None
    logistics: Decimal | None
    penalties: Decimal | None
    cogs: Decimal | None
    ads_spend: Decimal | None
    margin: Decimal | None
    order_sum: Decimal | None


@dataclass
class _FunnelRow:
    user_id: str
    date: date
    nm_id: int
    open_count: int | None
    cart_count: int | None
    order_count: int | None
    order_sum: Decimal | None
    buyout_percent: Decimal | None


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, article_rows, sku_rows, funnel_rows):
        self._article_rows = article_rows
        self._sku_rows = sku_rows
        self._funnel_rows = funnel_rows

    def query(self, model):
        name = getattr(model, "__name__", "")
        if name == "Article":
            return _FakeQuery(self._article_rows)
        if name == "SkuDaily":
            return _FakeQuery(self._sku_rows)
        if name == "FunnelDaily":
            return _FakeQuery(self._funnel_rows)
        raise AssertionError(f"Unexpected model requested: {name}")


def test_build_ai_products_payload_contains_daily_funnel_and_finance():
    user_id = "u1"
    article_rows = [
        _ArticleRow(
            user_id=user_id,
            nm_id=1001,
            vendor_code="ART-1001",
            name="Test Product",
            subject_name="Shoes",
        )
    ]
    sku_rows = [
        _SkuRow(
            user_id=user_id,
            date=date(2026, 3, 1),
            nm_id=1001,
            revenue=Decimal("1000"),
            commission=Decimal("100"),
            logistics=Decimal("150"),
            penalties=Decimal("10"),
            cogs=Decimal("400"),
            ads_spend=Decimal("50"),
            margin=Decimal("290"),
            order_sum=Decimal("1200"),
        )
    ]
    funnel_rows = [
        _FunnelRow(
            user_id=user_id,
            date=date(2026, 3, 1),
            nm_id=1001,
            open_count=100,
            cart_count=20,
            order_count=5,
            order_sum=Decimal("1300"),
            buyout_percent=Decimal("72.5"),
        )
    ]
    db = _FakeDB(article_rows=article_rows, sku_rows=sku_rows, funnel_rows=funnel_rows)
    payload = build_ai_products_payload(
        db,
        ExportConfig(user_id=user_id, date_from=date(2026, 3, 1), date_to=date(2026, 3, 2)),
    )

    assert payload["meta"]["array_root"] == "products"
    assert len(payload["products"]) == 1

    product = payload["products"][0]
    assert product["nm_id"] == 1001
    assert product["vendor_code"] == "ART-1001"
    assert len(product["daily"]) == 2

    d1 = product["daily"][0]
    assert d1["date"] == "2026-03-01"
    assert d1["funnel"]["views"] == 100
    assert d1["funnel"]["cr_basket_percent"] == 20.0
    assert d1["funnel"]["cr_order_percent"] == 25.0
    assert d1["funnel"]["cr_total_percent"] == 5.0
    assert d1["finance"]["commission_percent"] == 10.0
    assert d1["finance"]["logistics_percent"] == 15.0
    assert d1["finance"]["ads_percent"] == 5.0
    assert d1["finance"]["roi_percent"] == 72.5

    d2 = product["daily"][1]
    assert d2["date"] == "2026-03-02"
    assert d2["funnel"]["views"] == 0
    assert d2["finance"]["revenue"] == 0.0
