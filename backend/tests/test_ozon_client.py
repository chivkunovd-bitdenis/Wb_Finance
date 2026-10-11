from __future__ import annotations

import json

import httpx
import pytest

from app.services.ozon_client import (
    ACCRUAL_BY_DAY_PATH,
    ACCRUAL_TYPES_PATH,
    PRODUCT_INFO_LIST_PATH,
    OzonAPIError,
    OzonClient,
    OzonContractError,
)


def _client(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, trust_env=False)
    return OzonClient("test-client-id", "test-api-key", http_client=http_client, **kwargs), http_client


def test_fetch_accrual_types_posts_expected_path_and_caches_response():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"accrual_types": [{"id": 9, "name": "TYPE", "description": "Type"}]})

    client, http_client = _client(handler)
    try:
        first = client.fetch_accrual_types()
        first[0]["name"] = "caller mutation"
        second = client.fetch_accrual_types()
    finally:
        client.close()
        http_client.close()

    assert len(calls) == 1
    assert calls[0].url.path == ACCRUAL_TYPES_PATH
    assert calls[0].method == "POST"
    assert calls[0].read() == b"{}"
    assert calls[0].headers["Client-Id"] == "test-client-id"
    assert calls[0].headers["Api-Key"] == "test-api-key"
    assert second == [{"id": 9, "name": "TYPE", "description": "Type"}]


def test_fetch_accruals_day_follows_empty_page_with_nonempty_cursor():
    seen_payloads = []

    def handler(request):
        seen_payloads.append(request.read())
        if len(seen_payloads) == 1:
            return httpx.Response(200, json={"accruals": [], "last_id": "cursor-1"})
        return httpx.Response(200, json={"accruals": [{"accrual_id": 22}], "last_id": ""})

    client, http_client = _client(handler)
    try:
        accruals = client.fetch_accruals_day("2026-10-11")
    finally:
        client.close()
        http_client.close()

    assert accruals == [{"accrual_id": 22}]
    assert [json.loads(body) for body in seen_payloads] == [
        {"date": "2026-10-11", "last_id": ""},
        {"date": "2026-10-11", "last_id": "cursor-1"},
    ]


def test_fetch_accruals_day_rejects_repeated_cursor_and_page_limit():
    def repeated_handler(request):
        return httpx.Response(200, json={"accruals": [], "last_id": "same"})

    client, http_client = _client(repeated_handler)
    try:
        with pytest.raises(OzonContractError, match="повторил курсор"):
            client.fetch_accruals_day("2026-10-11")
    finally:
        client.close()
        http_client.close()

    client, http_client = _client(repeated_handler, max_pages=1)
    try:
        with pytest.raises(OzonContractError, match="лимит страниц"):
            client.fetch_accruals_day("2026-10-11")
    finally:
        client.close()
        http_client.close()


def test_fetch_accruals_day_rejects_invalid_calendar_date_before_request():
    def handler(request):
        pytest.fail("invalid date must not issue a request")

    client, http_client = _client(handler)
    try:
        with pytest.raises(OzonContractError, match="несуществующая"):
            client.fetch_accruals_day("2026-02-30")
    finally:
        client.close()
        http_client.close()


def test_fetch_products_deduplicates_and_chunks_at_one_thousand():
    requested = []

    def handler(request):
        payload = json.loads(request.read())
        requested.append(payload["sku"])
        return httpx.Response(200, json={"items": [{"sku": sku} for sku in payload["sku"]]})

    client, http_client = _client(handler)
    skus = [f"sku-{index}" for index in range(1000)] + ["sku-0", "sku-1000"]
    try:
        products = client.fetch_products(skus)
    finally:
        client.close()
        http_client.close()

    assert [len(batch) for batch in requested] == [1000, 1]
    assert requested[0][0] == "sku-0"
    assert requested[1] == ["sku-1000"]
    assert len(products) == 1001


def test_fetch_products_empty_list_does_not_issue_request():
    def handler(request):
        pytest.fail("empty SKU input must not issue a request")

    client, http_client = _client(handler)
    try:
        assert client.fetch_products([]) == []
    finally:
        client.close()
        http_client.close()


def test_retry_after_is_honored_and_long_retry_after_is_not_shortened():
    sleeps = []
    requests = []

    def retry_handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"secret": "body"})
        return httpx.Response(200, json={"accrual_types": []})

    client, http_client = _client(retry_handler, sleep=sleeps.append)
    try:
        assert client.fetch_accrual_types() == []
    finally:
        client.close()
        http_client.close()
    assert sleeps == [2.0]
    assert len(requests) == 2

    sleeps.clear()
    requests.clear()

    def long_wait_handler(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "31"}, json={"secret": "body"})

    client, http_client = _client(long_wait_handler, retry_after_cap_seconds=30, sleep=sleeps.append)
    try:
        with pytest.raises(OzonAPIError, match="ограничил частоту") as error:
            client.fetch_accrual_types()
    finally:
        client.close()
        http_client.close()
    assert len(requests) == 1
    assert sleeps == []
    assert "test-api-key" not in str(error.value)
    assert "secret" not in str(error.value)


def test_server_errors_are_bounded_and_auth_errors_do_not_expose_response_body():
    requests = []

    def unavailable_handler(request):
        requests.append(request)
        return httpx.Response(503, json={"secret": "body-value"})

    sleeps = []
    client, http_client = _client(unavailable_handler, max_attempts=2, sleep=sleeps.append)
    try:
        with pytest.raises(OzonAPIError, match="временно недоступен") as error:
            client.fetch_accrual_types()
    finally:
        client.close()
        http_client.close()
    assert len(requests) == 2
    assert len(sleeps) == 1
    assert "test-api-key" not in str(error.value)
    assert "body-value" not in str(error.value)

    client, http_client = _client(
        lambda request: httpx.Response(401, json={"secret": "credential-value"})
    )
    try:
        with pytest.raises(OzonAPIError, match="отклонил доступ") as auth_error:
            client.fetch_accrual_types()
    finally:
        client.close()
        http_client.close()
    assert "test-api-key" not in str(auth_error.value)
    assert "credential-value" not in str(auth_error.value)


def test_only_allowed_read_endpoints_are_called_by_client_methods():
    requested_paths = []

    def handler(request):
        requested_paths.append(request.url.path)
        if request.url.path == ACCRUAL_TYPES_PATH:
            return httpx.Response(200, json={"accrual_types": []})
        if request.url.path == ACCRUAL_BY_DAY_PATH:
            return httpx.Response(200, json={"accruals": [], "last_id": ""})
        if request.url.path == PRODUCT_INFO_LIST_PATH:
            return httpx.Response(200, json={"items": []})
        return httpx.Response(404)

    client, http_client = _client(handler)
    try:
        client.fetch_accrual_types()
        client.fetch_accruals_day("2026-10-11")
        client.fetch_products(["sku-1"])
    finally:
        client.close()
        http_client.close()

    assert requested_paths == [ACCRUAL_TYPES_PATH, ACCRUAL_BY_DAY_PATH, PRODUCT_INFO_LIST_PATH]
