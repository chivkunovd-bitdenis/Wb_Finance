from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from io import BytesIO
from typing import Any

from openpyxl import load_workbook


@dataclass(frozen=True)
class ParseError(Exception):
    message: str


def parse_wb_competitor_excel(
    *,
    content: bytes,
    report_date: date,
    period: str,
    raw_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Parse WB competitor comparison Excel into canonical payload for `import_competitor_report`.

    Contract output:
      {
        "report_date": <date>,
        "period": <period>,
        "source": "playwright",
        "raw_payload": {...},
        "items": [{"nm_id": int, "metric_code": str, "our_value": float|None, "competitor_median_value": float|None, "unit": str|None, "extra": dict|None}, ...]
      }

    NOTE: WB Excel structure may change. MVP parser is intentionally strict with a single supported sheet format.
    """
    if not content:
        raise ParseError("Empty excel payload")
    try:
        wb = load_workbook(BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ParseError("Failed to read excel") from exc

    # MVP: look for first sheet that has expected headers.
    sheet = wb.worksheets[0] if wb.worksheets else None
    if sheet is None:
        raise ParseError("Excel has no worksheets")

    # Expected columns (MVP-friendly): nm_id, ctr, traffic, funnel_cart, funnel_order and their medians.
    # We'll accept any header casing/spacing by normalizing.
    def _norm(v: object) -> str:
        return str(v or "").strip().lower().replace(" ", "_")

    header_row: list[str] | None = None
    header_row_idx: int | None = None
    for i, r in enumerate(sheet.iter_rows(min_row=1, max_row=40, values_only=True), start=1):
        if not r:
            continue
        nn = [_norm(x) for x in r]
        if "nm_id" in nn or "артикул" in nn:
            header_row = nn
            header_row_idx = i
            break
    if header_row is None:
        raise ParseError("Excel header row not found")
    if header_row_idx is None:
        raise ParseError("Excel header row index not found")

    # Map columns
    col_index: dict[str, int] = {name: i for i, name in enumerate(header_row) if name}

    def _col(*names: str) -> int | None:
        for n in names:
            if n in col_index:
                return col_index[n]
        return None

    nm_col = _col("nm_id", "артикул")
    if nm_col is None:
        raise ParseError("nm_id column not found")

    metric_cols = {
        "ctr": (_col("ctr"), _col("ctr_median", "median_ctr", "ctr_медиана")),
        "traffic": (_col("traffic"), _col("traffic_median", "median_traffic", "traffic_медиана")),
        "funnel_cart": (_col("funnel_cart", "to_cart", "воронка_в_корзину"), _col("funnel_cart_median", "median_funnel_cart")),
        "funnel_order": (_col("funnel_order", "to_order", "воронка_в_заказ"), _col("funnel_order_median", "median_funnel_order")),
    }

    items: list[dict[str, Any]] = []

    # Data starts after the detected header row.
    for r in sheet.iter_rows(min_row=header_row_idx + 1, values_only=True):
        if not r:
            continue
        try:
            nm_raw = r[nm_col]
            if nm_raw in {None, ""}:
                continue
            nm_id = int(str(nm_raw).strip())
        except Exception:
            continue

        for code, (our_i, med_i) in metric_cols.items():
            if our_i is None and med_i is None:
                continue
            our_v = None
            med_v = None
            try:
                if our_i is not None:
                    ov = r[our_i]
                    our_v = float(ov) if ov not in {None, ""} else None
            except Exception:
                our_v = None
            try:
                if med_i is not None:
                    mv = r[med_i]
                    med_v = float(mv) if mv not in {None, ""} else None
            except Exception:
                med_v = None

            if our_v is None and med_v is None:
                continue
            items.append(
                {
                    "nm_id": nm_id,
                    "metric_code": code,
                    "our_value": our_v,
                    "competitor_median_value": med_v,
                    "unit": None,
                    "extra": None,
                }
            )

    if not items:
        raise ParseError("No metrics parsed from excel")

    return {
        "report_date": report_date,
        "period": period,
        "source": "playwright",
        "raw_payload": raw_payload,
        "items": items,
    }

