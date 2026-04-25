"""
Тесты для app/services/wb_client.py с подменой HTTP-запросов (mock).
Реальные запросы в WB не уходят.
"""
import requests
import pytest
from requests import Response
from unittest.mock import patch, MagicMock

from app.services.wb_client import (
    fetch_sales,
    fetch_ads,
    fetch_funnel,
    fetch_funnel_products_for_day,
    fetch_funnel_products_for_day_with_retry,
)


@patch("app.services.wb_client.requests.get")
def test_fetch_sales_parses_response(mock_get):
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = [
        {
            "date_from": "2025-03-01",
            "nm_id": 12345,
            "doc_type_name": "Продажа",
            "retail_price": 1000.5,
            "ppvz_for_pay": 800,
            "delivery_rub": 50,
            "penalty": 0,
            "additional_payment": 0,
            "storage_fee": 10,
            "quantity": 1,
        }
    ]
    result = fetch_sales("2025-03-01", "2025-03-01", "fake-token")
    assert len(result) == 1
    assert result[0]["date"] == "2025-03-01"
    assert result[0]["nm_id"] == 12345
    assert result[0]["doc_type"] == "Продажа"
    assert result[0]["retail_price"] == 1000.5
    assert result[0]["quantity"] == 1


@patch("app.services.wb_client.requests.get")
def test_fetch_sales_empty_response(mock_get):
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = []
    result = fetch_sales("2025-03-01", "2025-03-05", "fake-token")
    assert result == []


@patch("app.services.wb_client.requests.get")
def test_fetch_ads_parses_and_splits_spend(mock_get):
    # Первый вызов — adv/v1/upd
    # Второй и далее — api/advert/v2/adverts (детали кампаний)
    def side_effect(url, **kwargs):
        r = MagicMock()
        if "adv/v1/upd" in url:
            r.status_code = 200
            r.json.return_value = [
                {"advertId": 1, "updSum": 300, "updTime": "2025-03-01"}
            ]
        else:
            r.status_code = 200
            r.json.return_value = [
                {"id": 1, "advertId": 1, "nm_settings": [{"nm_id": 10}, {"nm_id": 20}]}
            ]
        return r

    mock_get.side_effect = side_effect
    result = fetch_ads("2025-03-01", "2025-03-01", "fake-token")
    # 300 / 2 артикула = 150 на каждый
    assert len(result) == 2
    spends = [r["spend"] for r in result]
    assert spends[0] == 150.0 and spends[1] == 150.0
    assert result[0]["date"] == "2025-03-01"
    assert result[0]["campaign_id"] == 1


@patch("app.services.wb_client.requests.post")
def test_fetch_funnel_parses_response(mock_post):
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = [
        {
            "nmId": 100500,
            "vendorCode": "ART-001",
            "title": "Тестовый товар",
            "history": [
                {
                    "date": "2025-03-01",
                    "openCount": 100,
                    "cartCount": 20,
                    "orderCount": 5,
                    "orderSum": 15000.5,
                    "buyoutPercent": 25.5,
                    "addToCartConversion": 0.2,
                    "cartToOrderConversion": 0.25,
                }
            ],
        }
    ]
    result = fetch_funnel("2025-03-01", "2025-03-07", [100500], "fake-token")
    assert len(result) == 1
    assert result[0]["date"] == "2025-03-01"
    assert result[0]["nm_id"] == 100500
    assert result[0]["vendor_code"] == "ART-001"
    assert result[0]["title"] == "Тестовый товар"
    assert result[0]["open_count"] == 100
    assert result[0]["cart_count"] == 20
    assert result[0]["order_count"] == 5
    assert result[0]["order_sum"] == 15000.5
    assert result[0]["buyout_percent"] == 25.5
    assert result[0]["cr_to_cart"] == 0.2
    assert result[0]["cr_to_order"] == 0.25
    mock_post.assert_called_once()
    call_kw = mock_post.call_args[1]
    assert call_kw["json"]["selectedPeriod"]["start"] == "2025-03-01"
    assert call_kw["json"]["selectedPeriod"]["end"] == "2025-03-07"
    assert call_kw["json"]["nmIds"] == [100500]


@patch("app.services.wb_client.requests.post")
def test_fetch_funnel_products_for_day_parses(mock_post):
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "data": {
            "products": [
                {
                    "product": {
                        "nmId": 268913787,
                        "vendorCode": "vc-1",
                        "title": "Кроссовки для бега",
                        "subjectName": "Кроссовки",
                    },
                    "statistic": {
                        "selected": {
                            "openCount": 45,
                            "cartCount": 34,
                            "orderCount": 19,
                            "orderSum": 1262,
                            "conversions": {
                                "addToCartPercent": 19,
                                "cartToOrderPercent": 65,
                                "buyoutPercent": 0,
                            },
                            "wbClub": {"buyoutPercent": 43},
                        }
                    },
                }
            ]
        }
    }
    out = fetch_funnel_products_for_day("2024-03-01", [268913787], "fake-token")
    assert len(out) == 1
    row = out[0]
    assert row["date"] == "2024-03-01"
    assert row["nm_id"] == 268913787
    assert row["vendor_code"] == "vc-1"
    assert row["title"] == "Кроссовки для бега"
    assert row["open_count"] == 45
    assert row["cart_count"] == 34
    assert row["order_count"] == 19
    assert row["order_sum"] == 1262.0
    assert row["buyout_percent"] == 43.0
    assert abs(row["cr_to_cart"] - 0.19) < 1e-9
    assert abs(row["cr_to_order"] - 0.65) < 1e-9
    call_kw = mock_post.call_args[1]["json"]
    assert call_kw["selectedPeriod"]["start"] == "2024-03-01"
    assert "pastPeriod" not in call_kw
    assert call_kw["nmIds"] == [268913787]


@patch("app.services.wb_client.requests.post")
def test_fetch_funnel_products_for_day_all_products_when_nm_ids_empty(mock_post):
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"data": {"products": []}}

    out = fetch_funnel_products_for_day("2024-03-01", [], "fake-token")
    assert out == []

    payload = mock_post.call_args[1]["json"]
    assert payload["selectedPeriod"]["start"] == "2024-03-01"
    assert payload["selectedPeriod"]["end"] == "2024-03-01"
    assert "nmIds" not in payload


@patch("app.services.wb_client.requests.post")
def test_fetch_funnel_empty_history(mock_post):
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = []
    result = fetch_funnel("2025-03-01", "2025-03-07", [123], "fake-token")
    assert result == []


@patch("app.services.wb_client.time.sleep", return_value=None)
@patch("app.services.wb_client.requests.post")
def test_fetch_funnel_retries_same_chunk_on_429(mock_post, _mock_sleep):
    ok_item = {
        "nmId": 1,
        "history": [
            {
                "date": "2025-03-01",
                "openCount": 1,
                "cartCount": 1,
                "orderCount": 1,
                "orderSum": 10,
                "buyoutPercent": 0,
                "addToCartConversion": 0.1,
                "cartToOrderConversion": 0.1,
            }
        ],
    }
    ok = MagicMock(status_code=200)
    ok.json.return_value = [ok_item]
    err = MagicMock(status_code=429, reason="Too Many", text="limit")
    mock_post.side_effect = [err, ok]
    result = fetch_funnel("2025-03-01", "2025-03-01", [1], "fake-token")
    assert len(result) == 1
    assert mock_post.call_count == 2


@patch("app.services.wb_client.time.sleep", return_value=None)
@patch("app.services.wb_client.requests.post")
def test_fetch_funnel_non_blocking_mode_raises_on_429(mock_post, mock_sleep):
    """
    Для Celery-воркера используем sleep_on_retry=False: на 429 не спим внутри fetch_funnel,
    а даём вызывающему коду (task) поставить retry через countdown.
    """
    r429 = Response()
    r429.status_code = 429
    r429.url = "https://example/funnel"
    r429._content = b"limit"  # type: ignore[attr-defined]
    mock_post.return_value = r429
    with pytest.raises(requests.HTTPError):
        fetch_funnel(
            "2025-03-01",
            "2025-03-01",
            [1],
            "fake-token",
            sleep_on_retry=False,
        )
    assert mock_post.call_count == 1
    mock_sleep.assert_not_called()


@patch("app.services.wb_client.requests.post")
def test_fetch_funnel_non_retry_http_raises(mock_post):
    r401 = Response()
    r401.status_code = 401
    r401.url = "https://example/funnel"
    mock_post.return_value = r401
    with pytest.raises(requests.HTTPError):
        fetch_funnel("2025-03-01", "2025-03-01", [1], "fake-token")


@patch("app.services.wb_client.time.sleep", return_value=None)
@patch("app.services.wb_client.fetch_funnel_products_for_day")
def test_fetch_funnel_products_for_day_with_retry_on_429(mock_fetch_day, _mock_sleep):
    r429 = Response()
    r429.status_code = 429
    mock_fetch_day.side_effect = [
        requests.HTTPError(response=r429),
        [],
    ]
    out = fetch_funnel_products_for_day_with_retry("2025-03-01", [1], "fake-token", max_attempts=5)
    assert out == []
    assert mock_fetch_day.call_count == 2
