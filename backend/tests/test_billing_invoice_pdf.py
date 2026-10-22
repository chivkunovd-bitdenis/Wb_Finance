import io
from dataclasses import replace
from datetime import date
from decimal import Decimal
from urllib.parse import unquote

from pypdf import PdfReader

from app.routers.billing import _attachment_header
from app.services.invoice_pdf import (
    InvoiceBuyer,
    InvoicePdfData,
    build_payment_qr_payload,
    invoice_pdf_filename,
    render_invoice_pdf,
)

INVOICE_ID = "3f2b8e6a-1c4d-4e5f-9a7b-0c1d2e3f4a5b"


def _data(**changes: object) -> InvoicePdfData:
    base = InvoicePdfData(
        invoice_id=INVOICE_ID,
        number=1,
        issued_on=date(2026, 10, 3),
        period_start=date(2026, 10, 3),
        period_end=date(2026, 11, 3),
        marketplaces=("wb", "ozon"),
        unit_price=Decimal("1490.00"),
        buyer=InvoiceBuyer(name="ООО «ТЕСТ»", inn="1234567890", kpp="123456789", address="Г МОСКВА, УЛ ТЕСТОВАЯ, Д 1"),
    )
    return replace(base, **changes)


def _pdf_text(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    return " ".join(reader.pages[0].extract_text().split())


def test_qr_payload_matches_st00012_template_exactly() -> None:
    payload = build_payment_qr_payload(
        invoice_id=INVOICE_ID,
        number=1,
        issued_on=date(2026, 10, 3),
        amount=Decimal("2980.00"),
    )
    assert payload == (
        "ST00012|Name=ИП ЧИВКУНОВ ДЕНИС СЕРГЕЕВИЧ|PersonalAcc=40802810200005161598|BankName=АО «ТБанк»"
        "|BIC=044525974|CorrespAcc=30101810145250000974|PayeeINN=525406220193|Sum=298000"
        "|Purpose=Платеж по счету № ФП-1 от 03.10.26. НДС не облагается - 2980.00 руб."
        f"|Id={INVOICE_ID}"
    )


def test_qr_payload_for_single_marketplace_uses_kopecks_sum() -> None:
    payload = build_payment_qr_payload(
        invoice_id=INVOICE_ID,
        number=7,
        issued_on=date(2026, 11, 30),
        amount=Decimal("1490.00"),
    )
    assert "|Sum=149000|" in payload
    assert "|Purpose=Платеж по счету № ФП-7 от 30.11.26. НДС не облагается - 1490.00 руб.|" in payload


def test_pdf_is_single_page_and_contains_title_supplier_rows_and_total() -> None:
    pdf = render_invoice_pdf(_data())
    assert pdf.startswith(b"%PDF")
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 1

    text = _pdf_text(pdf)
    assert "Счет на оплату № ФП-1 от 3 октября 2026 г." in text
    assert "ИП ЧИВКУНОВ ДЕНИС СЕРГЕЕВИЧ, ИНН 525406220193, 607186, РОССИЯ" in text
    assert "ООО «ТЕСТ», ИНН 1234567890, КПП 123456789, Г МОСКВА, УЛ ТЕСТОВАЯ, Д 1" in text
    assert (
        "Предоставление доступа к системе WB Finance Pro (Wildberries) и её использование "
        "за период с 03.10.2026 по 03.11.2026"
    ) in text
    assert "Предоставление доступа к системе WB Finance Pro (Ozon)" in text
    assert "Всего наименований на сумму 2 980,00 руб." in text
    assert "Итого к оплате:" in text
    assert "2 980,00" in text
    assert "Две тысячи девятьсот восемьдесят рублей" in text
    assert "ТБанк" in text and "Business" not in text


def test_pdf_with_only_wb_has_single_row_and_1490_total() -> None:
    text = _pdf_text(render_invoice_pdf(_data(marketplaces=("wb",))))
    assert "(Wildberries)" in text
    assert "(Ozon)" not in text
    assert "Всего наименований на сумму 1 490,00 руб." in text
    assert "Одна тысяча четыреста девяносто рублей" in text


def test_buyer_line_omits_kpp_when_absent() -> None:
    buyer = InvoiceBuyer(name="ИП ТЕСТОВ", inn="123456789012", kpp=None, address="Г КАЗАНЬ")
    text = _pdf_text(render_invoice_pdf(_data(buyer=buyer)))
    assert "ИП ТЕСТОВ, ИНН 123456789012, Г КАЗАНЬ" in text
    assert "КПП" not in text


def test_pdf_render_is_deterministic_for_same_input() -> None:
    assert render_invoice_pdf(_data()) == render_invoice_pdf(_data())


def test_invoice_pdf_filename_uses_number_and_issue_date() -> None:
    assert invoice_pdf_filename(1, date(2026, 10, 3)) == "Счет_ФП-1_от_03.10.26.pdf"


def test_content_disposition_carries_utf8_filename_and_ascii_fallback() -> None:
    filename = "Счет_ФП-1_от_03.10.26.pdf"
    header = _attachment_header(filename, "invoice-FP-1.pdf")
    assert header.startswith('attachment; filename="invoice-FP-1.pdf"; filename*=UTF-8\'\'')
    # «Счет» в UTF-8 процентной записью: С = D0 A1, ч = D1 87, е = D0 B5, т = D1 82
    assert "%D0%A1%D1%87%D0%B5%D1%82" in header
    encoded = header.split("filename*=UTF-8''", 1)[1]
    assert unquote(encoded) == filename
