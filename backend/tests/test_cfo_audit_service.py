"""
Unit-тесты cfo_audit_service:
  - build_cfo_timeseries: маппинг полей, отбрасывание нулевых строк, fallback supplierArticle
  - cap на 3000+ строк -> топ-40 SKU + note
  - _strip_thinking: вырезание <thinking>...</thinking>
  - build_cfo_audit_prompt: чек-лист из 9 пунктов + JSON с данными внутри промпта
  - run_cfo_audit: пустые данные -> сообщение без вызова LLM; happy path вызывает модель

Без реальной БД и сетевых вызовов — вся SQLAlchemy Session замокана.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import app.services.cfo_audit_service as svc
from app.models.article import Article
from app.models.funnel_daily import FunnelDaily
from app.models.sku_daily import SkuDaily


def _sku_row(
    d: date,
    nm_id: int,
    *,
    revenue=0,
    margin=0,
    logistics=0,
    ads_spend=0,
    storage=0,
    penalties=0,
    cogs=0,
    open_count=0,
    cart_count=0,
    order_count=0,
) -> SimpleNamespace:
    return SimpleNamespace(
        date=d,
        nm_id=nm_id,
        revenue=revenue,
        margin=margin,
        logistics=logistics,
        ads_spend=ads_spend,
        storage=storage,
        penalties=penalties,
        cogs=cogs,
        open_count=open_count,
        cart_count=cart_count,
        order_count=order_count,
    )


def _article_row(nm_id: int, vendor_code: str | None) -> SimpleNamespace:
    return SimpleNamespace(nm_id=nm_id, vendor_code=vendor_code)


def _funnel_row(d: date, nm_id: int, vendor_code: str | None) -> SimpleNamespace:
    return SimpleNamespace(date=d, nm_id=nm_id, vendor_code=vendor_code)


class _FakeQuery:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def filter(self, *a, **kw) -> "_FakeQuery":
        return self

    def order_by(self, *a, **kw) -> "_FakeQuery":
        return self

    def all(self) -> list:
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    def __init__(self, sku_rows: list, article_rows: list | None = None, funnel_rows: list | None = None) -> None:
        self._sku_rows = sku_rows
        self._article_rows = article_rows or []
        self._funnel_rows = funnel_rows or []

    def query(self, model):
        if model is SkuDaily:
            return _FakeQuery(self._sku_rows)
        if model is Article:
            return _FakeQuery(self._article_rows)
        if model is FunnelDaily:
            return _FakeQuery(self._funnel_rows)
        raise AssertionError(f"unexpected query for {model!r}")


# ─── build_cfo_timeseries ─────────────────────────────────────────────────────

def test_build_cfo_timeseries_maps_fields_and_uses_vendor_code():
    d = date(2026, 9, 1)
    rows = [
        _sku_row(
            d, 111,
            revenue=1500.6, margin=300.4, logistics=100, ads_spend=50,
            storage=10, penalties=5, cogs=200, open_count=40, cart_count=8, order_count=3,
        )
    ]
    db = _FakeDB(rows, [_article_row(111, "LEGGINGS-BLK")])

    out = svc.build_cfo_timeseries(db, store_owner_id="u1", date_from=d, date_to=d)

    assert len(out) == 1
    row = out[0]
    assert row["date"] == "2026-09-01"
    assert row["nm_id"] == 111
    assert row["supplierArticle"] == "LEGGINGS-BLK"
    # money is rounded to whole rubles (int)
    assert row["sales"] == 1501
    assert row["margin"] == 300
    assert row["logSum"] == 100
    assert row["adsSum"] == 50
    assert row["storage"] == 10
    assert row["penalties"] == 5
    assert row["cogs"] == 200
    assert row["openCount"] == 40
    assert row["cartCount"] == 8
    assert row["orderCount"] == 3


def test_build_cfo_timeseries_falls_back_to_id_when_no_vendor_code_anywhere():
    d = date(2026, 9, 1)
    rows = [_sku_row(d, 222, revenue=100)]
    db = _FakeDB(rows, article_rows=[], funnel_rows=[])

    out = svc.build_cfo_timeseries(db, store_owner_id="u1", date_from=d, date_to=d)

    assert out[0]["supplierArticle"] == "ID: 222"


def test_build_cfo_timeseries_falls_back_to_funnel_daily_vendor_code():
    """
    articles.vendor_code бывает пустым (карточка не привязана), но тот же артикул часто
    приходит вместе с воронкой в funnel_daily — используем его как запасной источник,
    прежде чем показывать модели голый nm_id.
    """
    d = date(2026, 9, 1)
    rows = [_sku_row(d, 333, revenue=100)]
    db = _FakeDB(
        rows,
        article_rows=[_article_row(333, None)],  # articles знает про SKU, но код пустой
        # _FakeQuery.order_by() не сортирует по-настоящему (в отличие от реального ORDER BY
        # date DESC в build_cfo_timeseries) — фикстура уже в том порядке, в котором СУБД
        # вернула бы строки: сначала самая свежая дата.
        funnel_rows=[
            _funnel_row(date(2026, 8, 30), 333, "LATEST-CODE"),
            _funnel_row(date(2026, 8, 20), 333, "OLD-CODE"),
        ],
    )

    out = svc.build_cfo_timeseries(db, store_owner_id="u1", date_from=d, date_to=d)

    assert out[0]["supplierArticle"] == "LATEST-CODE"


def test_build_cfo_timeseries_prefers_articles_vendor_code_over_funnel_daily():
    d = date(2026, 9, 1)
    rows = [_sku_row(d, 444, revenue=100)]
    db = _FakeDB(
        rows,
        article_rows=[_article_row(444, "FROM-ARTICLES")],
        funnel_rows=[_funnel_row(d, 444, "FROM-FUNNEL")],
    )

    out = svc.build_cfo_timeseries(db, store_owner_id="u1", date_from=d, date_to=d)

    assert out[0]["supplierArticle"] == "FROM-ARTICLES"


def test_build_cfo_timeseries_drops_all_zero_rows():
    d = date(2026, 9, 1)
    rows = [
        _sku_row(d, 1),  # all-zero/None -> dropped
        _sku_row(d, 2, revenue=10),  # has a nonzero metric -> kept
    ]
    db = _FakeDB(rows)

    out = svc.build_cfo_timeseries(db, store_owner_id="u1", date_from=d, date_to=d)

    assert len(out) == 1
    assert out[0]["nm_id"] == 2


def test_build_cfo_timeseries_caps_to_top_40_skus_and_adds_note():
    d = date(2026, 9, 1)
    # 41 SKUs each contributing one nonzero row per day for 80 days -> 3280 rows > MAX_ROWS(3000)
    rows = []
    for nm in range(1, 42):
        for day in range(80):
            rows.append(_sku_row(date(2026, 1, 1 + day) if day < 31 else d, nm, revenue=100 + nm))
    db = _FakeDB(rows)

    out = svc.build_cfo_timeseries(db, store_owner_id="u1", date_from=d, date_to=d)

    assert len(rows) > svc.MAX_ROWS
    # last element is the note marker, not a data row
    assert set(out[-1].keys()) == {"note"}
    assert "топ-40" in out[-1]["note"] or "топ-" in out[-1]["note"]
    # SKU #1 (lowest revenue contribution) must have been dropped by the cap
    remaining_nm_ids = {r["nm_id"] for r in out[:-1]}
    assert len(remaining_nm_ids) <= svc.TOP_SKUS_ON_CAP
    assert 1 not in remaining_nm_ids


# ─── _strip_thinking ──────────────────────────────────────────────────────────

def test_strip_thinking_removes_block_case_insensitive_multiline():
    raw = "<THINKING>\nsome internal reasoning\nacross lines\n</THINKING>\n\n**Отчёт**\nтекст"
    out = svc._strip_thinking(raw)
    assert "reasoning" not in out
    assert out == "**Отчёт**\nтекст"


def test_strip_thinking_noop_when_no_block():
    raw = "**Отчёт**\nбез thinking-блока"
    assert svc._strip_thinking(raw) == raw


# ─── build_cfo_audit_prompt ────────────────────────────────────────────────────

def test_build_cfo_audit_prompt_contains_checklist_and_data_json():
    rows = [{"date": "2026-09-01", "nm_id": 1, "supplierArticle": "ID: 1", "sales": 100, "margin": 20,
             "openCount": 1, "cartCount": 1, "orderCount": 1, "logSum": 1, "adsSum": 1, "storage": 1,
             "penalties": 0, "cogs": 10}]
    prompt = svc.build_cfo_audit_prompt(date_from=date(2026, 9, 1), date_to=date(2026, 9, 5), rows=rows)

    for checklist_item in [
        "Выгорание рекламы",
        "Каннибализация логистикой",
        "Токсичный трафик",
        "Эластичность цены",
        "Дырявая корзина",
        "Донор-Реципиент",
        "Кассовый разрыв выкупа",
        "Спираль хранения",
        "Точка безубыточности",
    ]:
        assert checklist_item in prompt

    assert "01.09.2026" in prompt and "05.09.2026" in prompt
    assert "5 дней" in prompt  # explicit day count so the model doesn't misread the span
    assert '"nm_id":1' in prompt  # compact JSON (no spaces) is embedded in the prompt
    assert "storage" in prompt and "penalties" in prompt and "cogs" in prompt
    assert "Хранение" in prompt and "Штрафы" in prompt and "Себестоимость" in prompt


# ─── run_cfo_audit ─────────────────────────────────────────────────────────────

def test_run_cfo_audit_returns_message_when_no_rows(monkeypatch):
    db = _FakeDB(sku_rows=[])
    called = {"model": False}
    monkeypatch.setattr(svc, "_call_cfo_model", lambda prompt: called.__setitem__("model", True) or "unused")

    text = svc.run_cfo_audit(db, store_owner_id="u1", date_from=date(2026, 9, 1), date_to=date(2026, 9, 5))

    assert "Нет данных" in text
    assert called["model"] is False


def test_run_cfo_audit_calls_model_and_strips_thinking(monkeypatch):
    d = date(2026, 9, 1)
    db = _FakeDB(sku_rows=[_sku_row(d, 1, revenue=100, margin=20)])

    monkeypatch.setattr(
        svc, "_call_cfo_model", lambda prompt: "<thinking>размышления</thinking>\n**Итог**: всё хорошо"
    )

    text = svc.run_cfo_audit(db, store_owner_id="u1", date_from=d, date_to=d)

    assert "размышления" not in text
    assert text == "**Итог**: всё хорошо"
