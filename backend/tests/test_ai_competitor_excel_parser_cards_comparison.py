from __future__ import annotations

from datetime import date
from io import BytesIO

from openpyxl import Workbook

from app.services.ai_competitor_excel_parser import parse_wb_competitor_excel


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

