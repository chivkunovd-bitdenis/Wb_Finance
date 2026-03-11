"""
Клиент к API Wildberries. Логика как в GAS (Code.js): те же URL, пагинация rrid.
"""
import requests

SALES_URL = "https://statistics-api.wildberries.ru/api/v5/supplier/reportDetailByPeriod"


def _parse_date(value) -> str | None:
    """Привести к YYYY-MM-DD."""
    if not value:
        return None
    if hasattr(value, "split"):
        return value.split("T")[0].split(" ")[0]
    return str(value)[:10]


def fetch_sales(date_from: str, date_to: str, wb_api_key: str) -> list[dict]:
    """
    Загрузить продажи за период. Пагинация по rrid (как в GAS fetchWbWithRrid).
    Возвращает список словарей с ключами: date, nm_id, doc_type, retail_price, ppvz_for_pay,
    delivery_rub, penalty, additional_payment, storage_fee, quantity.
    """
    headers = {"Authorization": wb_api_key}
    all_rows = []
    rrid = 0

    while True:
        url = f"{SALES_URL}?dateFrom={date_from}&dateTo={date_to}&period=daily&limit=100000&rrid={rrid}"
        resp = requests.get(url, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        if not data or not isinstance(data, list):
            break

        for row in data:
            # WB может отдавать date_from или date, doc_type_name или doc_type
            d = _parse_date(row.get("date_from") or row.get("date"))
            if not d:
                continue
            all_rows.append({
                "date": d,
                "nm_id": row.get("nm_id") or row.get("nmId"),
                "doc_type": row.get("doc_type_name") or row.get("doc_type") or "",
                "retail_price": row.get("retail_price"),
                "ppvz_for_pay": row.get("ppvz_for_pay"),
                "delivery_rub": row.get("delivery_rub"),
                "penalty": row.get("penalty"),
                "additional_payment": row.get("additional_payment"),
                "storage_fee": row.get("storage_fee"),
                "quantity": row.get("quantity", 1),
            })

        if len(data) >= 100000 and data[-1].get("rrid"):
            rrid = data[-1]["rrid"]
        else:
            break

    return all_rows
