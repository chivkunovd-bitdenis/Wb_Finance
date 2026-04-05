"""
Тесты для app/services/wb_client.py с подменой HTTP-запросов (mock).
Реальные запросы в WB не уходят.
"""
from unittest.mock import patch, MagicMock

from app.services.wb_client import fetch_sales, fetch_ads, fetch_funnel, fetch_funnel_products_for_day


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


@patch("app.services.wb_client.requests.post")
def test_fetch_funnel_empty_history(mock_post):
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = []
    result = fetch_funnel("2025-03-01", "2025-03-07", [123], "fake-token")
    assert result == []
