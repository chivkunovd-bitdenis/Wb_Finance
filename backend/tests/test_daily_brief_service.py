"""
Unit-тесты daily_brief_service:
  - _process_established_sku: 5 сценариев ads_decision
  - _process_launch_sku: 3 сценария verdict_signal
  - _pct, _trend: вспомогательные функции

Без реальной БД и LLM-вызовов.
"""
from datetime import date, timedelta
from types import SimpleNamespace


from app.services.daily_brief_service import (
    _pct,
    _trend,
    _process_established_sku,
    _process_launch_sku,
)


# ─── Хелпер: мок SkuDaily (SimpleNamespace по аналогии с test_tasks.py) ──────

def _row(
    d: date,
    revenue: float = 0,
    margin: float = 0,
    ads_spend: float = 0,
    logistics: float = 0,
    orders: int = 0,
    views: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        date=d,
        revenue=revenue,
        margin=margin,
        ads_spend=ads_spend,
        logistics=logistics,
        order_count=orders,
        open_count=views,
    )


YESTERDAY = date(2026, 3, 26)
W7 = YESTERDAY - timedelta(days=6)  # window_start_7


def _make_last7(
    margin_per_day: float = 2000,
    ads_per_day: float = 1000,
    revenue_per_day: float = 15000,
    logistics_per_day: float = 2000,
    orders_per_day: int = 10,
    override_yesterday: dict | None = None,
) -> list[SimpleNamespace]:
    """7 одинаковых дней от W7 до YESTERDAY, опционально переопределяем вчера."""
    rows = []
    for i in range(7):
        d = W7 + timedelta(days=i)
        row = _row(
            d,
            revenue=revenue_per_day,
            margin=margin_per_day,
            ads_spend=ads_per_day,
            logistics=logistics_per_day,
            orders=orders_per_day,
            views=500,
        )
        rows.append(row)

    if override_yesterday:
        yesterday_row = rows[-1]  # последний элемент = YESTERDAY
        for k, v in override_yesterday.items():
            setattr(yesterday_row, k, v)

    return rows


# ─── _pct ─────────────────────────────────────────────────────────────────────

def test_pct_positive_deviation():
    assert _pct(120, 100) == 20.0


def test_pct_negative_deviation():
    assert _pct(80, 100) == -20.0


def test_pct_zero_ref_returns_none():
    assert _pct(100, 0) is None


def test_pct_no_change():
    assert _pct(100, 100) == 0.0


# ─── _trend ───────────────────────────────────────────────────────────────────

def test_trend_up():
    assert _trend([1.0, 2.0, 3.0, 4.0]) == "up"


def test_trend_down():
    assert _trend([4.0, 3.0, 2.0, 1.0]) == "down"


def test_trend_flat():
    assert _trend([5.0, 5.0, 5.0, 5.0]) == "flat"


def test_trend_too_short_returns_flat():
    assert _trend([5.0]) == "flat"


# ─── _process_established_sku: ads_decision ───────────────────────────────────

class TestProcessEstablishedSku:
    """Проверяем ads_decision — ключевой результат Python-логики по рекламе."""

    def _run(self, rows: list, yesterday_d: date = YESTERDAY) -> dict:
        out: list[dict] = []
        _process_established_sku(
            nm_id=123456,
            vendor_code="TEST-SKU",
            rows=rows,
            yesterday=yesterday_d,
            window_start_7=W7,
            out=out,
        )
        assert len(out) == 1, "ожидали ровно один результат в out"
        return out[0]

    def test_gross_margin_positive_ads_hurts__should_reduce_budget(self):
        """
        Сценарий: продукт прибылен без рекламы (gross_margin > 0),
        но реклама съедает прибыль (margin < 0).
        По неделе реклама «мешает» (avg_margin_with_ads < avg_margin_without_ads).
        Ожидаем: ads_decision = снизить_бюджет, amount = abs(margin_yesterday).
        """
        # 5 дней с рекламой: margin -200 (плохо), 2 дня без рекламы: margin 3000 (хорошо)
        rows = []
        for i in range(5):
            rows.append(_row(W7 + timedelta(days=i), margin=-200, ads_spend=2000, revenue=10000, logistics=2000, orders=8))
        rows.append(_row(W7 + timedelta(days=5), margin=3000, ads_spend=0, revenue=10000, logistics=2000, orders=8))
        # Вчера: gross_margin = (-500) + 2500 = 2000 > 0; margin = -500
        rows.append(_row(YESTERDAY, margin=-500, ads_spend=2500, revenue=10000, logistics=2000, orders=8))

        result = self._run(rows)
        assert result["ads_decision"] == "снизить_бюджет"
        assert result["ads_decision_amount"] == 500.0  # abs(margin_yesterday=-500)
        assert result["ads_days_analysis"]["ads_efficiency_signal"] == "мешает"
        assert result["yesterday"]["gross_margin"] == 2000.0  # -500 + 2500

    def test_gross_margin_negative__should_not_touch_ads(self):
        """
        Сценарий: продукт убыточен даже до вычета рекламы (gross_margin < 0).
        Реклама не виновата — проблема в цене/себестоимости.
        Ожидаем: ads_decision = не_трогать_продукт_убыточен.
        """
        rows = _make_last7(
            margin_per_day=-3000,
            ads_per_day=1000,
            revenue_per_day=5000,
            logistics_per_day=7000,  # высокая логистика → убыток до рекламы
            orders_per_day=5,
            override_yesterday={"margin": -4000, "ads_spend": 800},
        )
        # gross_margin_yesterday = -4000 + 800 = -3200 (отрицательный)
        result = self._run(rows)
        assert result["ads_decision"] == "не_трогать_продукт_убыточен"
        assert result["ads_decision_amount"] is None
        assert result["yesterday"]["gross_margin"] < 0

    def test_margin_positive_ads_profitable__should_not_touch(self):
        """
        Сценарий: margin > 0 вчера — реклама окупается.
        Ожидаем: ads_decision = не_трогать_окупается.
        """
        rows = _make_last7(
            margin_per_day=5000,
            ads_per_day=1000,
            revenue_per_day=20000,
            override_yesterday={"margin": 4500, "ads_spend": 1200},
        )
        result = self._run(rows)
        assert result["ads_decision"] == "не_трогать_окупается"
        assert result["ads_decision_amount"] is None

    def test_gross_margin_positive_but_ads_helps_weekly__should_watch(self):
        """
        Сценарий: gross_margin > 0, margin < 0 вчера,
        но по неделе реклама «помогает» (margin выше в дни с рекламой).
        Ожидаем: ads_decision = наблюдать_реклама_помогает (вчера разовый сбой).
        """
        # Дни с рекламой: margin высокий (3000), дни без: margin низкий (500)
        rows = []
        for i in range(5):
            rows.append(_row(W7 + timedelta(days=i), margin=3000, ads_spend=1500, revenue=12000, logistics=2000, orders=10))
        rows.append(_row(W7 + timedelta(days=5), margin=500, ads_spend=0, revenue=8000, logistics=2000, orders=4))
        # Вчера: margin -300, gross = -300 + 2000 = 1700 > 0
        rows.append(_row(YESTERDAY, margin=-300, ads_spend=2000, revenue=10000, logistics=2000, orders=7))

        result = self._run(rows)
        assert result["ads_decision"] == "наблюдать_реклама_помогает"
        assert result["ads_days_analysis"]["ads_efficiency_signal"] == "помогает"

    def test_no_yesterday_row__not_included_in_output(self):
        """
        Нет записи за вчера → SKU не попадает в выборку (нет данных = нет отчёта).
        """
        rows = _make_last7(
            margin_per_day=1000, ads_per_day=500, orders_per_day=5
        )
        # Удаляем вчерашнюю строку
        rows = [r for r in rows if r.date != YESTERDAY]

        out: list[dict] = []
        _process_established_sku(123, "X", rows, YESTERDAY, W7, out)
        assert len(out) == 0, "SKU без вчерашней записи не должен попадать в отчёт"


# ─── _process_launch_sku: verdict_signal ──────────────────────────────────────

class TestProcessLaunchSku:
    """Проверяем вердикт для новых товаров в режиме запуска."""

    def _run(self, rows: list) -> dict:
        out: list[dict] = []
        _process_launch_sku(nm_id=999, vendor_code="NEW-SKU", rows=rows, out=out)
        assert len(out) == 1
        return out[0]

    def _make_launch_rows(self, n_days: int, orders_per_day: int, margin_per_day: float, views: int = 200) -> list:
        base = date(2026, 3, 1)
        return [
            _row(base + timedelta(days=i), margin=margin_per_day, ads_spend=500, orders=orders_per_day, views=views + i * 10)
            for i in range(n_days)
        ]

    def test_profitable_and_views_up__should_continue(self):
        """
        2+ прибыльных дня + views растёт → ПРОДОЛЖАТЬ.
        """
        rows = self._make_launch_rows(n_days=8, orders_per_day=5, margin_per_day=500, views=100)
        result = self._run(rows)
        assert result["verdict_signal"] == "ПРОДОЛЖАТЬ"
        assert result["profitable_days"] >= 2

    def test_7days_no_profit_no_growth__should_stop(self):
        """
        7+ дней, 0 прибыльных дней, просмотры не растут → СТОП-СИГНАЛ.
        """
        rows = self._make_launch_rows(n_days=8, orders_per_day=1, margin_per_day=-2000, views=300)
        # Константные views = нет роста
        for r in rows:
            r.open_count = 300
        result = self._run(rows)
        assert result["verdict_signal"] == "СТОП-СИГНАЛ"
        assert result["profitable_days"] == 0

    def test_new_product_mixed_signals__should_watch(self):
        """
        1 прибыльный день, views растут — недостаточно для ПРОДОЛЖАТЬ, не критично для СТОП.
        Ожидаем: НАБЛЮДАТЬ.
        """
        rows = self._make_launch_rows(n_days=5, orders_per_day=3, margin_per_day=-500, views=100)
        rows[2].margin = 800  # один прибыльный день
        result = self._run(rows)
        assert result["verdict_signal"] == "НАБЛЮДАТЬ"

    def test_output_contains_required_fields(self):
        """Структура output содержит все обязательные поля."""
        rows = self._make_launch_rows(n_days=5, orders_per_day=2, margin_per_day=-300)
        result = self._run(rows)
        required = {
            "mode", "vendor_code", "days_total", "days_with_orders",
            "profitable_days", "views_trend", "cr_trend", "cost_per_order_trend",
            "verdict_signal", "latest_open_count", "latest_order_count",
            "latest_margin", "latest_ads_spend",
        }
        assert required.issubset(result.keys())
        assert result["mode"] == "launch"
