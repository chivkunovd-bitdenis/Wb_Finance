from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from io import BytesIO
from statistics import median
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

    if not wb.worksheets:
        raise ParseError("Excel has no worksheets")

    def _norm(v: object) -> str:
        return str(v or "").strip().lower().replace(" ", "_")

    def _to_float_or_none(v: object) -> float | None:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip().replace(" ", "").replace(",", ".")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    # Format A (WB "Показатели" sheet): metrics in rows, nm_id in columns.
    if "Показатели" in wb.sheetnames:
        sh = wb["Показатели"]
        # openpyxl read_only may report max_column=1 for wide sheets; cap max_col explicitly.
        header = list(next(sh.iter_rows(min_row=2, max_row=2, max_col=300, values_only=True), []))
        if header and _norm(header[0]) in {"показатели", "показатель"}:
            # Collect nm_id columns for current period: "Артикул WB <id>" (exclude "Разница" and previous period).
            nm_cols: list[tuple[int, int]] = []
            for idx, h in enumerate(header):
                hs = str(h or "")
                if "(предыдущий период)" in hs:
                    continue
                if "Разница" in hs:
                    continue
                if "Артикул WB" in hs:
                    parts = hs.replace("Артикул WB", "").strip().split()
                    nm_raw = parts[0] if parts else ""
                    if nm_raw.isdigit():
                        nm_cols.append((int(nm_raw), idx))

            if not nm_cols:
                raise ParseError("Показатели: no nm_id columns found")

            # Map russian row labels to metric_code
            row_map = {
                "показы": ("traffic", "шт"),
                "ctr": ("ctr", "%"),
                "добавления_в_корзину,_шт": ("funnel_cart", "шт"),
                "добавления_в_корзину": ("funnel_cart", "шт"),
                "заказы,_шт": ("funnel_order", "шт"),
                "заказы": ("funnel_order", "шт"),
            }

            # Find row indexes of metrics
            metric_rows: dict[str, int] = {}
            for r_idx, r in enumerate(sh.iter_rows(min_row=3, max_row=200, values_only=True), start=3):
                name = _norm(r[0] if r else "")
                if not name:
                    continue
                if name in row_map:
                    metric_code = row_map[name][0]
                    metric_rows[metric_code] = r_idx

            if not metric_rows:
                raise ParseError("Показатели: metric rows not found")

            items: list[dict[str, Any]] = []
            for metric_code, r_idx in metric_rows.items():
                # Read row values once
                row_vals = list(next(sh.iter_rows(min_row=r_idx, max_row=r_idx, max_col=300, values_only=True), []))
                unit = next((u for (k, u) in row_map.values() if k == metric_code), None)
                # Build per nm_id items
                for nm_id, c_idx in nm_cols:
                    our = _to_float_or_none(row_vals[c_idx] if c_idx < len(row_vals) else None)
                    others = []
                    for nm2, c2 in nm_cols:
                        if nm2 == nm_id:
                            continue
                        v2 = _to_float_or_none(row_vals[c2] if c2 < len(row_vals) else None)
                        if v2 is not None:
                            others.append(v2)
                    comp = float(median(others)) if others else None
                    items.append(
                        {
                            "nm_id": nm_id,
                            "metric_code": metric_code,
                            "our_value": our,
                            "competitor_median_value": comp,
                            "unit": unit,
                            "extra": None,
                        }
                    )

            return {
                "report_date": report_date,
                "period": period,
                "source": "playwright",
                "raw_payload": raw_payload,
                "items": items,
            }

    # Format B (legacy MVP): look for first sheet that has expected headers in rows.
    sheet = wb.worksheets[0]

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

    legacy_items: list[dict[str, Any]] = []

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
            legacy_items.append(
                {
                    "nm_id": nm_id,
                    "metric_code": code,
                    "our_value": our_v,
                    "competitor_median_value": med_v,
                    "unit": None,
                    "extra": None,
                }
            )

    if not legacy_items:
        raise ParseError("No metrics parsed from excel")

    return {
        "report_date": report_date,
        "period": period,
        "source": "playwright",
        "raw_payload": raw_payload,
        "items": legacy_items,
    }

