"""PDF счёта на оплату в форме, которую используют счета этого поставщика.

Все реквизиты поставщика, банка и счетов собраны в словаре SUPPLIER: при смене реквизитов
меняется только он. Счёт собирается из данных, сохранённых при выпуске, поэтому повторная
выгрузка даёт тот же документ. Координаты в этом модуле отсчитываются от верхнего края листа
(так проще сверять с образцом); reportlab работает от нижнего края, это переводит _Sheet.
"""

import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import segno
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.services.rub_amount_words import amount_in_words_rub

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_REGULAR = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"

# Реквизиты поставщика и банка: единственное место, где они заданы.
SUPPLIER: dict[str, str] = {
    "name": "ИП ЧИВКУНОВ ДЕНИС СЕРГЕЕВИЧ",
    "inn": "525406220193",
    "address": "607186, РОССИЯ, НИЖЕГОРОДСКАЯ ОБЛ, Г САРОВ, ПР-КТ ЛЕНИНА, Д 31, КВ 9",
    "bank_name": "АО «ТБанк»",
    "bik": "044525974",
    "corr_account": "30101810145250000974",
    "account": "40802810200005161598",
}

MARKETPLACE_TITLES: dict[str, str] = {"wb": "Wildberries", "ozon": "Ozon"}

_MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

PAGE_W, PAGE_H = A4
MARGIN = 22.0
QR_SIZE = 120.0
_QR_QUIET_ZONE_MODULES = 4
_TABLE_LEFT_COLUMNS = (22.0, 240.0, 50.0, 35.0, 60.0, 70.0)
_TABLE_HEADERS = ("№", "Товары (работы, услуги)", "кол-во", "Ед.", "НДС", "Цена", "Сумма")


@dataclass(frozen=True)
class InvoiceBuyer:
    name: str
    inn: str
    kpp: str | None
    address: str


@dataclass(frozen=True)
class InvoicePdfData:
    invoice_id: str
    number: int
    issued_on: date
    period_start: date
    period_end: date
    marketplaces: tuple[str, ...]
    unit_price: Decimal
    buyer: InvoiceBuyer


def invoice_number_display(number: int) -> str:
    return f"ФП-{number}"


def invoice_total(unit_price: Decimal, marketplaces: tuple[str, ...]) -> Decimal:
    return (unit_price * len(marketplaces)).quantize(Decimal("0.01"))


def format_rub(amount: Decimal) -> str:
    """2980 -> «2 980,00»: пробел между разрядами, запятая перед копейками."""
    text = format(amount.quantize(Decimal("0.01")), ",.2f")
    return text.replace(",", " ").replace(".", ",")


def supplier_line() -> str:
    return f"{SUPPLIER['name']}, ИНН {SUPPLIER['inn']}, {SUPPLIER['address']}"


def buyer_line(buyer: InvoiceBuyer) -> str:
    parts = [buyer.name, f"ИНН {buyer.inn}"]
    if buyer.kpp:
        parts.append(f"КПП {buyer.kpp}")
    parts.append(buyer.address)
    return ", ".join(parts)


def build_payment_qr_payload(*, invoice_id: str, number: int, issued_on: date, amount: Decimal) -> str:
    """Строка для QR по формату ST00012 (ГОСТ Р 56042), кодировка UTF-8."""
    kopecks = int((amount * 100).to_integral_value())
    purpose = (
        f"Платеж по счету № ФП-{number} от {issued_on:%d.%m.%y}. "
        f"НДС не облагается - {amount:.2f} руб."
    )
    return (
        "ST00012"
        f"|Name={SUPPLIER['name']}"
        f"|PersonalAcc={SUPPLIER['account']}"
        f"|BankName={SUPPLIER['bank_name']}"
        f"|BIC={SUPPLIER['bik']}"
        f"|CorrespAcc={SUPPLIER['corr_account']}"
        f"|PayeeINN={SUPPLIER['inn']}"
        f"|Sum={kopecks}"
        f"|Purpose={purpose}"
        f"|Id={invoice_id}"
    )


def invoice_pdf_filename(number: int, issued_on: date) -> str:
    return f"Счет_ФП-{number}_от_{issued_on:%d.%m.%y}.pdf"


def _register_fonts() -> None:
    registered = set(pdfmetrics.getRegisteredFontNames())
    if FONT_REGULAR not in registered:
        pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(FONTS_DIR / "DejaVuSans.ttf")))
    if FONT_BOLD not in registered:
        pdfmetrics.registerFont(TTFont(FONT_BOLD, str(FONTS_DIR / "DejaVuSans-Bold.ttf")))


class _Sheet:
    """Тонкая обёртка над canvas: все координаты в точках, отсчёт сверху вниз."""

    def __init__(self, canvas_obj: canvas.Canvas) -> None:
        self.c = canvas_obj

    def _y(self, top: float) -> float:
        return PAGE_H - top

    def text(
        self,
        x: float,
        baseline: float,
        value: str,
        *,
        font: str = FONT_REGULAR,
        size: float = 8.0,
        align: str = "left",
    ) -> None:
        self.c.setFont(font, size)
        y = self._y(baseline)
        if align == "right":
            self.c.drawRightString(x, y, value)
        elif align == "center":
            self.c.drawCentredString(x, y, value)
        else:
            self.c.drawString(x, y, value)

    def hline(self, x0: float, x1: float, top: float, width: float = 0.5) -> None:
        self.c.setLineWidth(width)
        self.c.line(x0, self._y(top), x1, self._y(top))

    def vline(self, x: float, top0: float, top1: float, width: float = 0.5) -> None:
        self.c.setLineWidth(width)
        self.c.line(x, self._y(top0), x, self._y(top1))

    def rect(self, x0: float, top0: float, x1: float, top1: float, width: float = 0.5) -> None:
        self.c.setLineWidth(width)
        self.c.rect(x0, self._y(top1), x1 - x0, top1 - top0, stroke=1, fill=0)

    def qr(self, payload: str, *, x: float, top: float, size: float) -> None:
        code = segno.make(payload, error="m", encoding="utf-8")
        rows = [list(row) for row in code.matrix_iter(scale=1, border=0)]
        cell = size / (len(rows) + 2 * _QR_QUIET_ZONE_MODULES)
        path = self.c.beginPath()
        for row_index, row in enumerate(rows):
            for col_index, dark in enumerate(row):
                if not dark:
                    continue
                cell_x = x + (col_index + _QR_QUIET_ZONE_MODULES) * cell
                cell_top = top + (row_index + _QR_QUIET_ZONE_MODULES) * cell
                path.rect(cell_x, self._y(cell_top + cell), cell, cell)
        self.c.setFillColorRGB(0, 0, 0)
        self.c.drawPath(path, stroke=0, fill=1)


def _draw_bank_box(sheet: _Sheet, *, left: float, right: float, top: float) -> float:
    """Банковский блок: получатель, БИК, корр. счёт и расчётный счёт. Возвращает нижнюю линию."""
    split_x = left + 330.0  # левая колонка: банк и получатель
    label_x = split_x + 62.0  # подписи «БИК» / «Сч. №» и их значения
    row1_bottom = top + 36.0
    bottom = row1_bottom + 44.0

    sheet.rect(left, top, right, bottom)
    sheet.hline(left, right, row1_bottom)
    sheet.vline(split_x, top, bottom)
    sheet.vline(label_x, top, bottom)
    sheet.hline(split_x, label_x, top + 18.0)  # «БИК» и «Сч. №» делят только колонку подписей; значение одно
    sheet.hline(left, split_x, row1_bottom + 14.0)  # маленькая ячейка ИНН над названием получателя

    sheet.text(left + 5, top + 15, SUPPLIER["bank_name"], size=9)
    sheet.text(left + 5, row1_bottom - 5, "Банк получателя", size=6.5)
    sheet.text(split_x + 5, top + 12, "БИК", size=6.5)
    sheet.text(label_x + 5, top + 12, SUPPLIER["bik"], size=8)
    sheet.text(split_x + 5, top + 30, "Сч. №", size=6.5)
    sheet.text(label_x + 5, top + 30, SUPPLIER["corr_account"], size=8)

    sheet.text(left + 5, row1_bottom + 10, f"ИНН   {SUPPLIER['inn']}", size=7.5)
    sheet.text(left + 5, row1_bottom + 24, SUPPLIER["name"], size=8)
    sheet.text(left + 5, bottom - 5, "Получатель", size=6.5)
    sheet.text(split_x + 5, row1_bottom + 12, "Сч. №", size=6.5)
    sheet.text(label_x + 5, row1_bottom + 12, SUPPLIER["account"], size=8)
    return bottom


def _draw_labeled_block(sheet: _Sheet, *, left: float, right: float, top: float, label: str, value: str) -> float:
    """Подпись «Поставщик:» и жирное значение, которое переносится по ширине. Возвращает последнюю строку."""
    label_width = 62.0
    sheet.text(left, top, label, size=8)
    lines = simpleSplit(value, FONT_BOLD, 8.5, right - left - label_width)
    for index, line in enumerate(lines):
        sheet.text(left + label_width, top + index * 11.0, line, font=FONT_BOLD, size=8.5)
    return top + (len(lines) - 1) * 11.0


def _draw_items_table(sheet: _Sheet, *, left: float, right: float, top: float, data: InvoicePdfData) -> float:
    widths = [*_TABLE_LEFT_COLUMNS, right - left - sum(_TABLE_LEFT_COLUMNS)]
    xs = [left]
    for width in widths:
        xs.append(xs[-1] + width)

    header_height = 22.0
    sheet.hline(left, right, top)
    sheet.hline(left, right, top + header_height)
    for index, header in enumerate(_TABLE_HEADERS):
        sheet.text(
            (xs[index] + xs[index + 1]) / 2,
            top + header_height / 2 + 2.5,
            header,
            font=FONT_BOLD,
            size=7,
            align="center",
        )

    unit_price = format_rub(data.unit_price)
    period = f"с {data.period_start:%d.%m.%Y} по {data.period_end:%d.%m.%Y}"
    cursor = top + header_height
    for position, marketplace in enumerate(data.marketplaces, start=1):
        title = MARKETPLACE_TITLES[marketplace]
        product = f"Предоставление доступа к системе WB Finance Pro ({title}) и её использование за период {period}"
        lines = simpleSplit(product, FONT_REGULAR, 7, widths[1] - 6)
        row_height = max(22.0, len(lines) * 9.0 + 8.0)
        row_bottom = cursor + row_height
        middle = cursor + row_height / 2
        first_baseline = middle - (len(lines) - 1) * 4.5 + 2.5
        sheet.hline(left, right, row_bottom)
        for line_index, line in enumerate(lines):
            sheet.text(xs[1] + 3, first_baseline + line_index * 9.0, line, size=7)
        baseline = middle + 2.5
        sheet.text((xs[0] + xs[1]) / 2, baseline, str(position), size=7, align="center")
        sheet.text((xs[2] + xs[3]) / 2, baseline, "1", size=7, align="center")
        sheet.text((xs[3] + xs[4]) / 2, baseline, "-", size=7, align="center")
        sheet.text((xs[4] + xs[5]) / 2, baseline, "Без НДС", size=7, align="center")
        sheet.text(xs[6] - 3, baseline, unit_price, size=7, align="right")
        sheet.text(right - 3, baseline, unit_price, size=7, align="right")
        cursor = row_bottom

    for x in xs:
        sheet.vline(x, top, cursor)
    return cursor


def render_invoice_pdf(data: InvoicePdfData) -> bytes:
    """Одностраничный PDF счёта (A4, книжная ориентация)."""
    _register_fonts()
    total = invoice_total(data.unit_price, data.marketplaces)
    payload = build_payment_qr_payload(
        invoice_id=data.invoice_id,
        number=data.number,
        issued_on=data.issued_on,
        amount=total,
    )

    buffer = io.BytesIO()
    canvas_obj = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    canvas_obj.setTitle(f"Счет на оплату {invoice_number_display(data.number)}")
    sheet = _Sheet(canvas_obj)
    left = MARGIN
    right = PAGE_W - MARGIN

    sheet.qr(payload, x=right - QR_SIZE, top=MARGIN, size=QR_SIZE)
    box_bottom = _draw_bank_box(sheet, left=left, right=right, top=MARGIN + QR_SIZE + 8)

    title_baseline = box_bottom + 32
    month = _MONTHS_GENITIVE[data.issued_on.month - 1]
    sheet.text(
        left,
        title_baseline,
        f"Счет на оплату № {invoice_number_display(data.number)} от "
        f"{data.issued_on.day} {month} {data.issued_on.year} г.",
        font=FONT_BOLD,
        size=15,
    )
    sheet.hline(left, right, title_baseline + 10, width=2)

    supplier_baseline = _draw_labeled_block(
        sheet, left=left, right=right, top=title_baseline + 30, label="Поставщик:", value=supplier_line()
    )
    buyer_baseline = _draw_labeled_block(
        sheet,
        left=left,
        right=right,
        top=supplier_baseline + 18,
        label="Покупатель:",
        value=buyer_line(data.buyer),
    )

    table_bottom = _draw_items_table(sheet, left=left, right=right, top=buyer_baseline + 14, data=data)

    totals_baseline = table_bottom + 14
    sheet.text(left, totals_baseline, f"Всего наименований на сумму {format_rub(total)} руб.", size=8)
    sheet.text(right - 60, totals_baseline, "Итого к оплате:", font=FONT_BOLD, size=9, align="right")
    sheet.text(right, totals_baseline, format_rub(total), font=FONT_BOLD, size=9, align="right")

    words_baseline = totals_baseline + 18
    sheet.text(left, words_baseline, amount_in_words_rub(total), font=FONT_BOLD, size=9)
    sheet.hline(left, right, words_baseline + 10, width=2)

    signature_baseline = words_baseline + 40
    sheet.text(left, signature_baseline, "Руководитель", font=FONT_BOLD, size=8)
    sheet.hline(left + 72, left + 250, signature_baseline + 3)
    sheet.text(left + 300, signature_baseline, "Бухгалтер", font=FONT_BOLD, size=8)
    sheet.hline(left + 360, right, signature_baseline + 3)

    canvas_obj.showPage()
    canvas_obj.save()
    return buffer.getvalue()
