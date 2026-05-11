from __future__ import annotations

from datetime import date
from io import BytesIO

import pytest
from openpyxl import Workbook

from app.services.ai_competitor_excel_parser import parse_wb_competitor_excel


def test_parse_cards_comparison_explicit_conversion_rows_not_scaled_by_100() -> None:
    """WB: проценты в ячейках без «%» — не умножаем на 100 (8 = 8%, не 800%)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Показатели"
    ws.append(["Отчёт"])
    ws.append(["Показатели", "Артикул WB 10", "Артикул WB 20"])
    ws.append(["Показы", 1000, 2000])
    ws.append(["CTR", 3.0, 4.0])
    ws.append(["Конверсия в корзину", 8.0, 12.0])
    ws.append(["Конверсия в заказ", 2.5, 3.0])
    buf = BytesIO()
    wb.save(buf)

    out = parse_wb_competitor_excel(content=buf.getvalue(), report_date=date(2026, 5, 11), period="week")
    cart_10 = next(i for i in out["items"] if i["nm_id"] == 10 and i["metric_code"] == "funnel_cart")
    assert cart_10["our_value"] == 8.0
    assert cart_10["competitor_median_value"] == 12.0
    ord_20 = next(i for i in out["items"] if i["nm_id"] == 20 and i["metric_code"] == "funnel_order")
    assert ord_20["our_value"] == 3.0
    assert ord_20["competitor_median_value"] == 2.5


def test_parse_cards_comparison_pokazateli_sheet() -> None:
    """
    New WB export: sheet 'Показатели' with nm_id columns and metric rows.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Показатели"
    ws.append(["Отчёт «Сравнение карточек»: Показатели"])
    ws.append(["Показатели", "Артикул WB 111", "Артикул WB 222", "Разница артикул 222 - артикул 111"])
    ws.append(["Показы", 100, 300, 200])
    ws.append(["CTR", 5, 15, 10])
    ws.append(["Добавления в корзину, шт", 10, 20, 10])
    ws.append(["Заказы, шт", 2, 8, 6])
    buf = BytesIO()
    wb.save(buf)

    out = parse_wb_competitor_excel(content=buf.getvalue(), report_date=date(2026, 5, 11), period="week")
    items = out["items"]
    # our values
    ctr_111 = next(i for i in items if i["nm_id"] == 111 and i["metric_code"] == "ctr")
    assert ctr_111["our_value"] == 5.0
    assert ctr_111["competitor_median_value"] == 15.0
    traffic_222 = next(i for i in items if i["nm_id"] == 222 and i["metric_code"] == "traffic")
    assert traffic_222["our_value"] == 300.0
    assert traffic_222["competitor_median_value"] == 100.0

    # Конверсии: из «…, шт» / «Показы» × 100 (процентные пункты, как в UI WB).
    cart_111 = next(i for i in items if i["nm_id"] == 111 and i["metric_code"] == "funnel_cart")
    assert cart_111["our_value"] == pytest.approx(10.0)
    assert cart_111["competitor_median_value"] == pytest.approx(100.0 * 20.0 / 300.0)
    assert cart_111["unit"] == "%"
    ord_222 = next(i for i in items if i["nm_id"] == 222 and i["metric_code"] == "funnel_order")
    assert ord_222["our_value"] == pytest.approx(100.0 * 8.0 / 300.0)
    assert ord_222["competitor_median_value"] == pytest.approx(100.0 * 2.0 / 100.0)

