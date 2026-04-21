"""
Интеграционные тесты: реальные запросы к API Wildberries.

Ключ WB берётся из переменной WB_API_KEY или из файла backend/.env (см. .env.example).
Без ключа тесты пропускаются — в CI и при обычном pytest их не будет.

Запуск с реальным WB:
  Положи ключ в backend/.env (скопируй из .env.example и заполни WB_API_KEY).
  Затем: pytest tests/test_wb_client_real.py -v
  Или в Docker: docker compose run --rm api pytest tests/test_wb_client_real.py -v
  (.env монтируется в контейнер автоматически)
"""
import os
from datetime import date, timedelta
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

from app.services.wb_client import fetch_sales, fetch_ads, fetch_funnel

# Реальные запросы к WB нельзя запускать "случайно" (локально/в CI):
# они зависят от внешней сети, лимитов и могут быть долгими/нестабильными.
# Поэтому включаем их только явным флагом.
pytestmark = pytest.mark.skipif(
    (os.getenv("RUN_REAL_WB_TESTS") or "").strip() not in {"1", "true", "TRUE", "yes", "YES"},
    reason="Real WB API tests are disabled by default. Set RUN_REAL_WB_TESTS=1 to enable.",
)

# Подгрузить backend/.env (при запуске из backend/ или из Docker /app)
# override=True — чтобы значения из файла перезаписали пустые переменные из env_file
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path, override=True)
# На случай если запуск не из /app (например из backend/)
if _env_path != Path.cwd() / ".env":
    load_dotenv(Path.cwd() / ".env", override=True)


def _read_key_from_file(path: Path) -> str | None:
    """Прочитать WB_API_KEY из файла .env."""
    if not path.is_file():
        return None
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("WB_API_KEY=") and not line.startswith("WB_API_KEY=#"):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    except OSError:
        pass
    return None


def _get_wb_key():
    key = (os.getenv("WB_API_KEY") or "").strip()
    if key:
        return key
    # В Docker .env монтируется в /app/.env — читаем файл напрямую
    for p in (_env_path, Path("/app/.env")):
        key = _read_key_from_file(p)
        if key:
            return key
    return None


def _short_period(days_back: int = 2) -> tuple[str, str]:
    """Короткий период для теста: последние days_back дней до вчера."""
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days_back - 1)
    return start.isoformat(), end.isoformat()


# Ожидаемые ключи после нашего парсинга (как в wb_client)
SALES_KEYS = {"date", "nm_id", "doc_type", "retail_price", "ppvz_for_pay", "delivery_rub", "penalty", "additional_payment", "storage_fee", "quantity"}
ADS_KEYS = {"date", "nm_id", "campaign_id", "spend"}
FUNNEL_KEYS = {"date", "nm_id", "vendor_code", "open_count", "cart_count", "order_count", "order_sum", "buyout_percent", "cr_to_cart", "cr_to_order"}


def test_real_fetch_sales_structure_and_period():
    """Реальный запрос продаж: структура полей и даты в запрошенном периоде."""
    key = _get_wb_key()
    if not key:
        pytest.skip("WB_API_KEY не задан — проверь backend/.env")
    date_from, date_to = _short_period(2)

    try:
        rows = fetch_sales(date_from, date_to, key)
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else None
        if code == 429:
            pytest.skip("WB API rate limit (429) — пропускаем real-test")
        raise

    assert isinstance(rows, list)
    for row in rows:
        assert set(row.keys()) >= SALES_KEYS, f"Нет ожидаемых ключей в {row.keys()}"
        assert row["date"] >= date_from and row["date"] <= date_to, (
            f"Дата {row['date']} вне периода {date_from}–{date_to}"
        )
        # date в формате YYYY-MM-DD
        assert len(row["date"]) == 10 and row["date"][4] == "-" and row["date"][7] == "-"


def test_real_fetch_ads_structure_and_period():
    """Реальный запрос рекламы: структура и даты строго в запрошенном периоде."""
    key = _get_wb_key()
    if not key:
        pytest.skip("WB_API_KEY не задан — проверь backend/.env")
    date_from, date_to = _short_period(3)

    rows = fetch_ads(date_from, date_to, key)

    assert isinstance(rows, list)
    for row in rows:
        assert set(row.keys()) >= ADS_KEYS
        assert date_from <= row["date"] <= date_to, (
            f"Дата {row['date']} вне периода {date_from}–{date_to}"
        )
        assert "spend" in row  # число или 0


def test_real_fetch_funnel_structure_and_period():
    """Реальный запрос воронки: нужны nm_id; берём из продаж или один тестовый."""
    key = _get_wb_key()
    if not key:
        pytest.skip("WB_API_KEY не задан — проверь backend/.env")
    date_from, date_to = _short_period(2)

    # Сначала получаем хотя бы один nm_id из продаж
    try:
        sales = fetch_sales(date_from, date_to, key)
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else None
        if code == 429:
            pytest.skip("WB API rate limit (429) — пропускаем real-test")
        raise
    nm_ids = list({int(r["nm_id"]) for r in sales if r.get("nm_id") is not None})
    if not nm_ids:
        # Можно задать тестовый nm_id через env, иначе скип
        extra = (os.getenv("WB_FUNNEL_NM_IDS") or "").strip()
        if extra:
            nm_ids = [int(x) for x in extra.replace(",", " ").split() if x.strip()]
        if not nm_ids:
            pytest.skip("Нет nm_id для воронки (нет продаж за период и не задан WB_FUNNEL_NM_IDS)")

    nm_ids = nm_ids[:5]  # не более 5 для скорости
    rows = fetch_funnel(date_from, date_to, nm_ids, key)

    assert isinstance(rows, list)
    for row in rows:
        assert set(row.keys()) >= FUNNEL_KEYS
        assert row["date"] >= date_from and row["date"] <= date_to
        assert row["nm_id"] in nm_ids
