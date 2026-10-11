"""Read-only HTTP client for the Ozon Seller API endpoints used by finance sync."""
from __future__ import annotations

from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import re
import time
from typing import Any, Callable

import httpx


OZON_API_BASE_URL = "https://api-seller.ozon.ru"
ACCRUAL_TYPES_PATH = "/v1/finance/accrual/types"
ACCRUAL_BY_DAY_PATH = "/v1/finance/accrual/by-day"
PRODUCT_INFO_LIST_PATH = "/v3/product/info/list"


class OzonAPIError(RuntimeError):
    """Sanitized provider/transport failure; response bodies and credentials are omitted."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str,
        status_code: int | None = None,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.status_code = status_code
        self.attempts = attempts


class OzonContractError(ValueError):
    """The request or response does not match the supported Ozon API contract."""


class OzonClient:
    """Synchronous, bounded, read-only client for Ozon finance and product lookups.

    ``http_client`` is injectable for deterministic tests and controlled callers. When the
    client creates its own transport it explicitly disables environment proxy discovery.
    The API key is used only in request headers and is never included in an error message.
    """

    def __init__(
        self,
        client_id: str,
        api_key: str,
        *,
        http_client: httpx.Client | None = None,
        base_url: str = OZON_API_BASE_URL,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
        max_pages: int = 500,
        retry_after_cap_seconds: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(client_id, str) or not client_id.strip():
            raise OzonContractError("Требуется Client-Id Ozon")
        if not isinstance(api_key, str) or not api_key.strip():
            raise OzonContractError("Требуется Api-Key Ozon")
        if not 0 < timeout_seconds <= 120:
            raise OzonContractError("Тайм-аут должен быть от 0 до 120 секунд")
        if not 1 <= max_attempts <= 5:
            raise OzonContractError("Число попыток должно быть от 1 до 5")
        if not 1 <= max_pages <= 500:
            raise OzonContractError("Лимит страниц должен быть от 1 до 500")
        if not 0 <= retry_after_cap_seconds <= 120:
            raise OzonContractError("Лимит ожидания повтора должен быть от 0 до 120 секунд")

        self._client_id = client_id.strip()
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_attempts = max_attempts
        self._max_pages = max_pages
        self._retry_after_cap_seconds = retry_after_cap_seconds
        self._sleep = sleep
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
            trust_env=False,
        )
        self._accrual_types_cache: list[dict[str, Any]] | None = None

    def close(self) -> None:
        """Close only the transport created by this client."""
        if self._owns_http_client:
            self._http_client.close()

    def __enter__(self) -> OzonClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def fetch_accrual_types(self) -> list[dict[str, Any]]:
        """Fetch and locally cache the fee type dictionary for this client instance."""
        if self._accrual_types_cache is not None:
            return [dict(item) for item in self._accrual_types_cache]

        payload = self._post_json(ACCRUAL_TYPES_PATH, {})
        accrual_types = payload.get("accrual_types")
        if not isinstance(accrual_types, list) or any(not isinstance(item, dict) for item in accrual_types):
            raise OzonContractError("Ozon вернул справочник начислений в неверном формате")
        self._accrual_types_cache = [dict(item) for item in accrual_types]
        return [dict(item) for item in self._accrual_types_cache]

    def fetch_accruals_day(self, day: date | str) -> list[dict[str, Any]]:
        """Fetch every accrual page for one day, rejecting repeated or unbounded cursors."""
        day_text = _iso_day(day)
        cursor = ""
        seen_cursors: set[str] = set()
        accruals: list[dict[str, Any]] = []

        for _page_number in range(1, self._max_pages + 1):
            payload = self._post_json(
                ACCRUAL_BY_DAY_PATH,
                {"date": day_text, "last_id": cursor},
            )
            page_accruals = payload.get("accruals")
            next_cursor = payload.get("last_id")
            if not isinstance(page_accruals, list) or any(not isinstance(item, dict) for item in page_accruals):
                raise OzonContractError("Ozon вернул начисления дня в неверном формате")
            if not isinstance(next_cursor, str):
                raise OzonContractError("Ozon не вернул курсор last_id для следующей страницы")

            accruals.extend(dict(item) for item in page_accruals)
            if next_cursor == "":
                return accruals
            if next_cursor == cursor or next_cursor in seen_cursors:
                raise OzonContractError("Ozon повторил курсор страниц начислений")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        raise OzonContractError("Превышен лимит страниц начислений Ozon за день")

    def fetch_products(self, skus: list[str]) -> list[dict[str, Any]]:
        """Fetch product labels in batches of at most 1,000 SKUs."""
        if not isinstance(skus, list):
            raise OzonContractError("Список SKU должен быть списком строк")
        normalized: list[str] = []
        seen: set[str] = set()
        for sku in skus:
            if not isinstance(sku, str) or not sku.strip():
                raise OzonContractError("Каждый SKU должен быть непустой строкой")
            value = sku.strip()
            if value not in seen:
                seen.add(value)
                normalized.append(value)

        products: list[dict[str, Any]] = []
        for start in range(0, len(normalized), 1000):
            payload = self._post_json(
                PRODUCT_INFO_LIST_PATH,
                {"sku": normalized[start : start + 1000]},
            )
            items = payload.get("items")
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise OzonContractError("Ozon вернул карточки товаров в неверном формате")
            products.extend(dict(item) for item in items)
        return products

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}{endpoint}"
        headers = {
            "Client-Id": self._client_id,
            "Api-Key": self._api_key,
            "Content-Type": "application/json",
        }
        last_status: int | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._http_client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self._timeout,
                )
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == self._max_attempts:
                    raise OzonAPIError(
                        "Не удалось связаться с Ozon после нескольких попыток",
                        endpoint=endpoint,
                        attempts=attempt,
                    ) from None
                self._sleep(self._backoff_seconds(attempt))
                continue

            last_status = response.status_code
            if response.status_code == 200:
                try:
                    body = response.json()
                except ValueError as exc:
                    raise OzonContractError("Ozon API returned invalid JSON") from exc
                if not isinstance(body, dict):
                    raise OzonContractError("Ozon API returned a non-object JSON response")
                return body

            if response.status_code in {429} or response.status_code >= 500:
                if attempt < self._max_attempts:
                    delay = self._retry_after_seconds(response) if response.status_code == 429 else None
                    if delay is not None and delay > self._retry_after_cap_seconds:
                        raise OzonAPIError(
                            "Ozon ограничил частоту запросов; повторите синхронизацию позже",
                            endpoint=endpoint,
                            status_code=response.status_code,
                            attempts=attempt,
                        )
                    self._sleep(delay if delay is not None else self._backoff_seconds(attempt))
                    continue

            if response.status_code in {401, 403}:
                message = "Ozon отклонил доступ; проверьте Client-Id, Api-Key и права API"
            elif response.status_code == 429:
                message = "Ozon ограничил частоту запросов; повторите синхронизацию позже"
            elif response.status_code >= 500:
                message = "Сервис Ozon временно недоступен; повторите синхронизацию позже"
            else:
                message = f"Ozon вернул ошибку HTTP {response.status_code}"
            raise OzonAPIError(
                message,
                endpoint=endpoint,
                status_code=response.status_code,
                attempts=attempt,
            )

        raise OzonAPIError(
            "Не удалось выполнить запрос к Ozon после нескольких попыток",
            endpoint=endpoint,
            status_code=last_status,
            attempts=self._max_attempts,
        )

    def _backoff_seconds(self, attempt: int) -> float:
        return min(self._retry_after_cap_seconds, 0.5 * (2 ** (attempt - 1)))

    def _retry_after_seconds(self, response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            seconds = float(value.strip())
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return None
        return max(0.0, seconds)


_ISO_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _iso_day(value: date | str) -> str:
    if isinstance(value, datetime):
        raise OzonContractError("day must be a calendar date, not a datetime")
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or not _ISO_DAY_RE.fullmatch(value):
        raise OzonContractError("Дата должна быть в формате YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise OzonContractError("Указана несуществующая календарная дата") from exc
    return value
