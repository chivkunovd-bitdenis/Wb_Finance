"""
Интеграционные тесты: реальная БД, полный пайплайн.

Проверяют:
- структуру ответов API (как контракт для фронта);
- что данные, записанные в БД, возвращаются через API без искажений;
- цепочку: данные в формате WB → raw_sales → recalculate_pnl → pnl_daily → GET /dashboard/pnl;
- при наличии WB_API_KEY в .env — структуру ответа реального WB API.

Требуется работающая PostgreSQL (DATABASE_URL). Запуск: pytest tests/test_integration_dashboard.py -v
"""
import os
from datetime import date
from datetime import timedelta
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import get_db
from app.models.user import User
from app.models.article import Article
from app.models.raw_sales import RawSale
from app.models.pnl_daily import PnlDaily
from app.models.sku_daily import SkuDaily
from app.models.funnel_daily import FunnelDaily
from app.models.funnel_backfill_state import FunnelBackfillState
from app.models.operational_expense import OperationalExpense
from app.core.security import create_access_token
from celery_app.tasks import sync_sales, recalculate_pnl
from app.services.wb_client import fetch_sales


# Ключи, которые должен возвращать наш парсер WB (контракт для raw_sales)
WB_SALES_ROW_KEYS = {
    "date", "nm_id", "doc_type", "retail_price", "ppvz_for_pay",
    "delivery_rub", "penalty", "additional_payment", "storage_fee", "quantity",
    "subject_name",
}

# Ожидаемые ключи ответов API (контракт для фронта)
STATE_KEYS = {
    "has_data",
    "min_date",
    "max_date",
    "has_2025",
    "has_2026",
    "has_funnel",
    "autostart_disabled",
    "autostart_disabled_reason",
    "funnel_ytd_backfill",
    "finance_backfill",
    "finance_backfill_2025",
    "finance_missing_sync",
}
FUNNEL_YTD_KEYS = {"year", "status", "last_completed_date", "through_date", "error_message"}
FINANCE_BACKFILL_KEYS = {"year", "status", "last_completed_date", "through_date", "error_message"}
PNL_DAY_KEYS = {
    "date", "revenue", "commission", "logistics", "penalties", "storage",
    "ads_spend", "cogs", "tax", "operation_expenses", "margin",
}
SKU_DAY_KEYS = {
    "date", "nm_id", "revenue", "commission", "logistics", "penalties", "storage",
    "ads_spend", "cogs", "tax", "margin", "open_count", "cart_count", "order_count", "order_sum",
}
ARTICLE_KEYS = {"nm_id", "vendor_code", "name", "subject_name", "cost_price"}
FUNNEL_DAY_KEYS = {
    "date", "nm_id", "vendor_code", "open_count", "cart_count", "order_count",
    "order_sum", "buyout_percent", "cr_to_cart", "cr_to_order",
}

PLAN_FACT_MONTH_KEYS = {"month", "metrics"}
PLAN_FACT_METRIC_KEYS = {
    "metric_key",
    "is_percent",
    "plan",
    "fact",
    "pct_of_plan",
    "forecast",
    "forecast_pct_of_plan",
}


@pytest.fixture
def authenticated_client(real_db_session):
    """Клиент с JWT и сессией БД, в которой создан пользователь."""
    user = User(
        id="a0000000-0000-4000-8000-000000000001",
        email="int@example.com",
        password_hash="$2b$12$fake",
        wb_api_key="test-wb-key",
        is_active=True,
    )
    real_db_session.add(user)
    real_db_session.commit()
    token = create_access_token(data={"sub": str(user.id)})

    def get_db_override():
        yield real_db_session

    app.dependency_overrides[get_db] = get_db_override
    try:
        with TestClient(app) as c:
            yield c, real_db_session, str(user.id), token
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_dashboard_state_structure_and_has_data_false(authenticated_client):
    """GET /dashboard/state без данных: полная структура ответа и has_data=False."""
    client, _session, _user_id, token = authenticated_client
    r = client.get("/dashboard/state", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == STATE_KEYS
    assert data["has_data"] is False
    assert data["min_date"] is None
    assert data["max_date"] is None
    assert data["has_2025"] is False
    assert data["has_2026"] is False
    assert data["has_funnel"] is False
    assert set(data["funnel_ytd_backfill"].keys()) == FUNNEL_YTD_KEYS
    assert set(data["finance_backfill"].keys()) == FINANCE_BACKFILL_KEYS
    assert set(data["finance_backfill_2025"].keys()) == FINANCE_BACKFILL_KEYS


def test_dashboard_state_structure_and_has_data_true(authenticated_client):
    """В БД есть pnl_daily → GET /dashboard/state возвращает has_data=True и даты."""
    client, session, user_id, token = authenticated_client
    d = date(2025, 3, 15)
    session.add(
        PnlDaily(
            user_id=user_id,
            date=d,
            revenue=100_000,
            commission=15_000,
            logistics=5_000,
            penalties=0,
            storage=1_000,
            ads_spend=10_000,
            cogs=40_000,
            tax=6_000,
            margin=3_000,
        )
    )
    session.commit()

    r = client.get("/dashboard/state", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == STATE_KEYS
    assert data["has_data"] is True
    assert data["min_date"] == "2025-03-15"
    assert data["max_date"] == "2025-03-15"
    assert data["has_2025"] is True
    assert data["has_2026"] is False
    assert set(data["funnel_ytd_backfill"].keys()) == FUNNEL_YTD_KEYS
    assert set(data["finance_backfill"].keys()) == FINANCE_BACKFILL_KEYS
    assert set(data["finance_backfill_2025"].keys()) == FINANCE_BACKFILL_KEYS


def test_dashboard_state_does_not_autostart_funnel_ytd_when_yesterday_missing(authenticated_client):
    """
    При входе не стартуем историческую YTD-воронку: воронка ограничена rolling 7 дней
    и запускается только после финансового sales-синка.
    """
    client, session, user_id, token = authenticated_client

    today = date.today()
    yesterday = today - timedelta(days=1)

    # Есть продажи в диапазоне backfill, но за вчера funnel_daily отсутствует.
    session.add(
        RawSale(
            user_id=user_id,
            date=yesterday,
            nm_id=123,
            doc_type="Продажа",
            retail_price=100,
            ppvz_for_pay=90,
            delivery_rub=5,
            penalty=0,
            additional_payment=0,
            storage_fee=0,
            quantity=1,
        )
    )
    session.commit()

    with patch("app.routers.dashboard.sync_funnel_ytd_step.delay") as mock_delay:
        r = client.get("/dashboard/state", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        mock_delay.assert_not_called()


def test_dashboard_state_resets_stuck_running_funnel_backfill_without_autostart(authenticated_client):
    """
    Регрессия: если funnel_backfill_state застрял в status=running (например, воркер умер),
    баннер может висеть сутками и задача не будет перезапущена.

    /dashboard/state должен сбросить running→idle при старом updated_at, но не автостартовать YTD.
    """
    client, session, user_id, token = authenticated_client

    today = date.today()
    yesterday = today - timedelta(days=1)

    # Есть продажи в диапазоне backfill, но за вчера funnel_daily отсутствует.
    session.add(
        RawSale(
            user_id=user_id,
            date=yesterday,
            nm_id=123,
            doc_type="Продажа",
            retail_price=100,
            ppvz_for_pay=90,
            delivery_rub=5,
            penalty=0,
            additional_payment=0,
            storage_fee=0,
            quantity=1,
        )
    )

    stuck = FunnelBackfillState(
        user_id=user_id,
        calendar_year=2026,
        status="running",
        last_completed_date=date(2026, 1, 24),
        error_message=None,
        updated_at=datetime.now(timezone.utc) - timedelta(hours=12),
    )
    session.add(stuck)
    session.commit()

    with patch("app.routers.dashboard.sync_funnel_ytd_step.delay") as mock_delay:
        r = client.get("/dashboard/state", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        mock_delay.assert_not_called()
        session.refresh(stuck)
        assert stuck.status == "idle"


def test_dashboard_state_syncs_finance_only_for_yesterday_when_only_yesterday_missing(authenticated_client):
    """
    Как должно работать (финансы):
    - данные в pnl_daily за последние дни есть,
    - но ровно за вчера отсутствуют,
    => /dashboard/state должен поставить в очередь sales-догрузку только за вчера,
       а не запускать годовой finance backfill.
    """
    client, session, user_id, token = authenticated_client

    today = date.today()
    yesterday = today - timedelta(days=1)
    day_before = today - timedelta(days=2)

    # Чтобы финансовая логика была активна: должны быть хоть какие-то raw_sales за год.
    session.add(
        RawSale(
            user_id=user_id,
            date=day_before,
            nm_id=123,
            doc_type="Продажа",
            retail_price=100,
            ppvz_for_pay=90,
            delivery_rub=5,
            penalty=0,
            additional_payment=0,
            storage_fee=0,
            quantity=1,
        )
    )
    # Есть P&L за день до вчера (т.е. "в остальные дни есть"), но за вчера нет.
    session.add(
        PnlDaily(
            user_id=user_id,
            date=day_before,
            revenue=100,
            commission=10,
            logistics=1,
            penalties=0,
            storage=0,
            ads_spend=0,
            cogs=10,
            tax=6,
            margin=73,
        )
    )
    session.commit()

    with patch("app.routers.dashboard.sync_finance_missing_range.delay") as mock_missing:
        with patch("app.routers.dashboard.sync_finance_backfill_step.delay") as mock_backfill:
            r = client.get("/dashboard/state", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200
            mock_backfill.assert_not_called()
            mock_missing.assert_called_once()
            args, _kwargs = mock_missing.call_args
            assert args[0] == user_id
            assert args[1] == yesterday.isoformat()
            assert args[2] == yesterday.isoformat()


def test_dashboard_state_syncs_finance_tail_for_yesterday_and_day_before(authenticated_client):
    """
    Регрессия: если хвостом отсутствуют вчера и позавчера, /dashboard/state должен поставить
    одну точечную догрузку ровно на этот диапазон.
    """
    client, session, user_id, token = authenticated_client

    today = date.today()
    yesterday = today - timedelta(days=1)
    day_before = today - timedelta(days=2)
    last_complete = today - timedelta(days=3)

    session.add(
        RawSale(
            user_id=user_id,
            date=last_complete,
            nm_id=123,
            doc_type="Продажа",
            retail_price=100,
            ppvz_for_pay=90,
            delivery_rub=5,
            penalty=0,
            additional_payment=0,
            storage_fee=0,
            quantity=1,
        )
    )
    session.add(
        PnlDaily(
            user_id=user_id,
            date=last_complete,
            revenue=100,
            commission=10,
            logistics=1,
            penalties=0,
            storage=0,
            ads_spend=0,
            cogs=10,
            tax=6,
            margin=73,
        )
    )
    session.commit()

    with patch("app.routers.dashboard.sync_finance_missing_range.delay") as mock_missing:
        with patch("app.routers.dashboard.sync_finance_backfill_step.delay") as mock_backfill:
            r = client.get("/dashboard/state", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200
            mock_backfill.assert_not_called()
            mock_missing.assert_called_once()
            args, _kwargs = mock_missing.call_args
            assert args[0] == user_id
            assert args[1] == day_before.isoformat()
            assert args[2] == yesterday.isoformat()


def test_dashboard_state_finance_missing_range_is_deduped_when_running(authenticated_client):
    """Повторный вход не должен ставить missing-range задачу, если она уже running."""
    client, session, user_id, token = authenticated_client
    from datetime import timezone as _tz

    today = date.today()
    yesterday = today - timedelta(days=1)
    day_before = today - timedelta(days=2)

    # активируем финансовый автологик: наличие raw_sales + pnl_daily за день до вчера
    session.add(
        RawSale(
            user_id=user_id,
            date=day_before,
            nm_id=123,
            doc_type="Продажа",
            retail_price=100,
            ppvz_for_pay=90,
            delivery_rub=5,
            penalty=0,
            additional_payment=0,
            storage_fee=0,
            quantity=1,
        )
    )
    session.add(
        PnlDaily(
            user_id=user_id,
            date=day_before,
            revenue=100,
            commission=10,
            logistics=1,
            penalties=0,
            storage=0,
            ads_spend=0,
            cogs=10,
            tax=6,
            margin=73,
        )
    )

    # уже есть state running на диапазон вчера
    from app.models.finance_missing_sync_state import FinanceMissingSyncState

    session.add(
        FinanceMissingSyncState(
            user_id=user_id,
            date_from=yesterday,
            date_to=yesterday,
            status="running",
            next_run_at=None,
            updated_at=datetime.now(_tz.utc),
        )
    )
    session.commit()

    with patch("app.routers.dashboard.sync_finance_missing_range.delay") as mock_missing:
        r = client.get("/dashboard/state", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        mock_missing.assert_not_called()


def test_dashboard_state_enqueues_missing_range_for_middle_hole_when_yesterday_present(authenticated_client):
    """
    Если вчера уже есть, но в пределах lookback есть дыра в середине —
    /dashboard/state должен поставить догрузку по этой дыре (ограниченно), без запуска backfill.
    """
    client, session, user_id, token = authenticated_client

    today = date.today()
    yesterday = today - timedelta(days=1)
    d1 = yesterday - timedelta(days=10)
    d2 = yesterday - timedelta(days=9)
    hole_start = yesterday - timedelta(days=7)
    hole_end = yesterday - timedelta(days=6)

    # eligibility + "yesterday present": raw_sales must exist for вчера, иначе сработает missing-tail.
    session.add_all(
        [
            RawSale(
                user_id=user_id,
                date=d1,
                nm_id=123,
                doc_type="Продажа",
                retail_price=100,
                ppvz_for_pay=90,
                delivery_rub=5,
                penalty=0,
                additional_payment=0,
                storage_fee=0,
                quantity=1,
            ),
            RawSale(
                user_id=user_id,
                date=d2,
                nm_id=123,
                doc_type="Продажа",
                retail_price=100,
                ppvz_for_pay=90,
                delivery_rub=5,
                penalty=0,
                additional_payment=0,
                storage_fee=0,
                quantity=1,
            ),
            RawSale(
                user_id=user_id,
                date=yesterday,
                nm_id=123,
                doc_type="Продажа",
                retail_price=100,
                ppvz_for_pay=90,
                delivery_rub=5,
                penalty=0,
                additional_payment=0,
                storage_fee=0,
                quantity=1,
            ),
            # P&L тут не влияет на определение дыр, но пусть присутствует как "витрина есть".
            PnlDaily(user_id=user_id, date=d1, revenue=1, commission=0, logistics=0, penalties=0, storage=0, ads_spend=0, cogs=0, tax=0, margin=1, operation_expenses=0),
            PnlDaily(user_id=user_id, date=d2, revenue=1, commission=0, logistics=0, penalties=0, storage=0, ads_spend=0, cogs=0, tax=0, margin=1, operation_expenses=0),
            PnlDaily(user_id=user_id, date=yesterday, revenue=1, commission=0, logistics=0, penalties=0, storage=0, ads_spend=0, cogs=0, tax=0, margin=1, operation_expenses=0),
        ]
    )
    session.commit()

    with patch("app.routers.dashboard.sync_finance_missing_range.delay") as mock_missing:
        with patch("app.routers.dashboard.sync_finance_backfill_step.delay") as mock_backfill:
            r = client.get("/dashboard/state", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200
            mock_backfill.assert_not_called()
            assert mock_missing.called
            # at least one call should cover the hole_end..hole_start region (order doesn't matter)
            calls = [c.args for c in mock_missing.call_args_list]
            assert any(args[1] == hole_start.isoformat() and args[2] == hole_end.isoformat() for args in calls) or any(
                hole_start.isoformat() <= args[1] <= hole_end.isoformat() or hole_start.isoformat() <= args[2] <= hole_end.isoformat()
                for args in calls
            )

def test_dashboard_pnl_response_structure_and_values_from_db(authenticated_client):
    """Данные из pnl_daily возвращаются через GET /dashboard/pnl без искажений."""
    client, session, user_id, token = authenticated_client
    session.add(
        PnlDaily(
            user_id=user_id,
            date=date(2025, 3, 10),
            revenue=50_000,
            commission=7_500,
            logistics=2_000,
            penalties=500,
            storage=500,
            ads_spend=5_000,
            cogs=20_000,
            tax=3_000,
            margin=1_500,
        )
    )
    session.add(
        PnlDaily(
            user_id=user_id,
            date=date(2025, 3, 11),
            revenue=60_000,
            commission=9_000,
            logistics=2_500,
            penalties=0,
            storage=600,
            ads_spend=6_000,
            cogs=24_000,
            tax=3_600,
            margin=1_800,
        )
    )
    session.commit()

    r = client.get(
        "/dashboard/pnl",
        params={"date_from": "2025-03-10", "date_to": "2025-03-11"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    assert len(items) == 2
    for item in items:
        assert set(item.keys()) == PNL_DAY_KEYS
    by_date = {x["date"]: x for x in items}
    assert by_date["2025-03-10"]["revenue"] == 50000.0
    assert by_date["2025-03-10"]["margin"] == 1500.0
    assert by_date["2025-03-11"]["revenue"] == 60000.0
    assert by_date["2025-03-11"]["margin"] == 1800.0


def test_dashboard_articles_response_structure(authenticated_client):
    """GET /dashboard/articles: структура как контракт для фронта."""
    client, session, user_id, token = authenticated_client
    session.add(
        Article(
            user_id=user_id,
            nm_id=12345678,
            vendor_code="ART-01",
            name="Товар тест",
            cost_price=500.50,
        )
    )
    session.commit()

    r = client.get("/dashboard/articles", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    assert len(items) == 1
    assert set(items[0].keys()) == ARTICLE_KEYS
    assert items[0]["nm_id"] == 12345678
    assert items[0]["cost_price"] == 500.5


def test_dashboard_articles_vendor_code_fallback_from_funnel_latest_non_null(authenticated_client):
    """
    Регрессия: если Article.vendor_code пустой, API должен подставить vendor_code
    из последней non-null записи funnel_daily по (user_id, nm_id), независимо от выбранного дня на фронте.
    """
    client, session, user_id, token = authenticated_client
    nm_id = 777
    session.add(
        Article(
            user_id=user_id,
            nm_id=nm_id,
            vendor_code=None,
            name="Товар без vendor_code",
            cost_price=100,
        )
    )
    session.add_all(
        [
            FunnelDaily(
                user_id=user_id,
                date=date(2025, 3, 10),
                nm_id=nm_id,
                vendor_code="OLD",
                open_count=1,
                cart_count=1,
                order_count=1,
                order_sum=1,
            ),
            FunnelDaily(
                user_id=user_id,
                date=date(2025, 3, 11),
                nm_id=nm_id,
                vendor_code="NEW",
                open_count=1,
                cart_count=1,
                order_count=1,
                order_sum=1,
            ),
            # Более поздняя дата, но vendor_code пустой — не должна “перебить” NEW.
            FunnelDaily(
                user_id=user_id,
                date=date(2025, 3, 12),
                nm_id=nm_id,
                vendor_code=None,
                open_count=1,
                cart_count=1,
                order_count=1,
                order_sum=1,
            ),
        ]
    )
    session.commit()

    r = client.get("/dashboard/articles", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    items = r.json()
    by_nm = {x["nm_id"]: x for x in items}
    assert by_nm[nm_id]["vendor_code"] == "NEW"


def test_dashboard_articles_vendor_code_article_value_has_priority_over_funnel(authenticated_client):
    """Если vendor_code задан в articles, он не должен подменяться данными из funnel_daily."""
    client, session, user_id, token = authenticated_client
    nm_id = 778
    session.add(
        Article(
            user_id=user_id,
            nm_id=nm_id,
            vendor_code="ART",
            name="Товар с vendor_code",
            cost_price=100,
        )
    )
    session.add(
        FunnelDaily(
            user_id=user_id,
            date=date(2025, 3, 11),
            nm_id=nm_id,
            vendor_code="FUNNEL",
            open_count=1,
            cart_count=1,
            order_count=1,
            order_sum=1,
        )
    )
    session.commit()

    r = client.get("/dashboard/articles", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    items = r.json()
    by_nm = {x["nm_id"]: x for x in items}
    assert by_nm[nm_id]["vendor_code"] == "ART"


def test_dashboard_articles_vendor_code_blank_string_falls_back_to_funnel(authenticated_client):
    """Если в articles vendor_code пустой/пробельный, должен сработать fallback из funnel_daily."""
    client, session, user_id, token = authenticated_client
    nm_id = 779
    session.add(
        Article(
            user_id=user_id,
            nm_id=nm_id,
            vendor_code="   ",
            name="Товар с пустым vendor_code",
            cost_price=100,
        )
    )
    session.add(
        FunnelDaily(
            user_id=user_id,
            date=date(2025, 3, 11),
            nm_id=nm_id,
            vendor_code="FUNNEL-VC",
            open_count=1,
            cart_count=1,
            order_count=1,
            order_sum=1,
        )
    )
    session.commit()

    r = client.get("/dashboard/articles", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    items = r.json()
    by_nm = {x["nm_id"]: x for x in items}
    assert by_nm[nm_id]["vendor_code"] == "FUNNEL-VC"


def test_dashboard_sku_response_structure_and_values_from_db(authenticated_client):
    """Данные из sku_daily возвращаются через GET /dashboard/sku без искажений."""
    client, session, user_id, token = authenticated_client
    session.add(
        SkuDaily(
            user_id=user_id,
            date=date(2025, 3, 12),
            nm_id=111,
            revenue=20_000,
            commission=3_000,
            logistics=500,
            penalties=0,
            storage=200,
            ads_spend=2_000,
            cogs=8_000,
            tax=1_200,
            margin=1_100,
            open_count=100,
            cart_count=30,
            order_count=10,
            order_sum=20_000,
        )
    )
    session.commit()

    r = client.get(
        "/dashboard/sku",
        params={"date_from": "2025-03-12", "date_to": "2025-03-12"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    assert len(items) == 1
    assert set(items[0].keys()) == SKU_DAY_KEYS
    assert items[0]["date"] == "2025-03-12"
    assert items[0]["nm_id"] == 111
    assert items[0]["revenue"] == 20000.0
    assert items[0]["margin"] == 1100.0
    assert items[0]["order_count"] == 10


def test_dashboard_funnel_response_structure(authenticated_client):
    """GET /dashboard/funnel: структура ответа как контракт для фронта."""
    client, session, user_id, token = authenticated_client
    session.add(
        FunnelDaily(
            user_id=user_id,
            date=date(2025, 3, 14),
            nm_id=222,
            vendor_code="FUN-1",
            open_count=50,
            cart_count=15,
            order_count=5,
            order_sum=10_000,
            buyout_percent=80.0,
            cr_to_cart=30.0,
            cr_to_order=10.0,
        )
    )
    session.commit()

    r = client.get(
        "/dashboard/funnel",
        params={"date_from": "2025-03-14", "date_to": "2025-03-14"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    assert len(items) == 1
    assert set(items[0].keys()) == FUNNEL_DAY_KEYS
    assert items[0]["date"] == "2025-03-14"
    assert items[0]["nm_id"] == 222
    assert items[0]["order_count"] == 5
    assert items[0]["buyout_percent"] == 80.0


def test_dashboard_funnel_vendor_code_fallback_from_latest_non_null(authenticated_client):
    """
    Регрессия под запрос из фронта:
    если на выбранный день FunnelDaily.vendor_code пустой,
    API должен подставить vendor_code из последней non-null записи этого nm_id.
    """
    client, session, user_id, token = authenticated_client
    nm_id = 333
    session.add(
        FunnelDaily(
            user_id=user_id,
            date=date(2025, 3, 10),
            nm_id=nm_id,
            vendor_code=None,
            open_count=1,
            cart_count=1,
            order_count=1,
            order_sum=1,
        )
    )
    session.add(
        FunnelDaily(
            user_id=user_id,
            date=date(2025, 3, 11),
            nm_id=nm_id,
            vendor_code="LATEST-VC",
            open_count=1,
            cart_count=1,
            order_count=1,
            order_sum=1,
        )
    )
    session.commit()

    r = client.get(
        "/dashboard/funnel",
        params={"date_from": "2025-03-10", "date_to": "2025-03-10"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    assert len(items) == 1
    assert items[0]["nm_id"] == nm_id
    assert items[0]["vendor_code"] == "LATEST-VC"


def test_full_flow_wb_shape_to_raw_to_pnl_to_api(authenticated_client):
    """
    Полный пайплайн: данные в формате WB → sync_sales (raw_sales) → recalculate_pnl (pnl_daily) → GET /dashboard/pnl.
    Проверяем структуру на каждом шаге и что итоговый ответ API совпадает с ожидаемой агрегацией.
    """
    client, session, user_id, token = authenticated_client
    # Артикул для себестоимости
    session.add(
        Article(
            user_id=user_id,
            nm_id=999,
            vendor_code="TEST",
            name="Test",
            cost_price=100,
        )
    )
    session.commit()

    # Формат ответа WB (как в wb_client.fetch_sales)
    wb_shaped = [
        {
            "date": "2025-03-20",
            "nm_id": 999,
            "doc_type": "Продажа",
            "retail_price": 1000,
            "ppvz_for_pay": 850,
            "delivery_rub": 50,
            "penalty": 0,
            "additional_payment": 0,
            "storage_fee": 10,
            "quantity": 1,
        },
    ]

    # Чтобы задача не закрывала сессию теста
    session.close = MagicMock()
    with patch("celery_app.tasks.fetch_sales", return_value=wb_shaped):
        with patch("celery_app.tasks.SessionLocal", return_value=session):
            with patch("celery_app.tasks.recalculate_pnl") as mock_rec:
                mock_rec.delay.return_value = MagicMock(id="rec-1")
                res = sync_sales(user_id, "2025-03-20", "2025-03-20")
    assert res["ok"] is True
    assert res["count"] == 1

    # В raw_sales должна быть одна запись
    raw = session.query(RawSale).filter(RawSale.user_id == user_id, RawSale.date == date(2025, 3, 20)).all()
    assert len(raw) == 1
    assert raw[0].nm_id == 999
    assert float(raw[0].retail_price) == 1000
    assert (raw[0].doc_type or "").strip().lower() == "продажа"

    # Пересчёт P&L вручную (recalculate_pnl в тесте с той же сессией)
    with patch("celery_app.tasks.SessionLocal", return_value=session):
        rec_res = recalculate_pnl(user_id, "2025-03-20", "2025-03-20")
    assert rec_res["ok"] is True
    assert rec_res["count"] == 1

    # Читаем через API и проверяем структуру и значения
    r = client.get(
        "/dashboard/pnl",
        params={"date_from": "2025-03-20", "date_to": "2025-03-20"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert set(items[0].keys()) == PNL_DAY_KEYS
    # revenue = 1000, commission = 1000 - 850 = 150, tax = 1000*0.06 = 60, cogs = 100, margin = 1000 - 150 - 50 - 10 - 100 - 60 = 630
    assert items[0]["date"] == "2025-03-20"
    assert items[0]["revenue"] == 1000.0
    assert items[0]["commission"] == 150.0
    assert items[0]["cogs"] == 100.0
    assert items[0]["tax"] == 60.0
    assert items[0]["margin"] == 630.0


def test_pnl_deducts_operational_expenses_from_margin(authenticated_client):
    """Операционные расходы уменьшают margin в pnl_daily и отображаются в GET /dashboard/pnl."""
    client, session, user_id, token = authenticated_client

    d = date(2025, 3, 20)
    session.add(
        Article(
            user_id=user_id,
            nm_id=999,
            vendor_code="TEST",
            name="Test",
            cost_price=100,
        )
    )
    session.add(
        RawSale(
            user_id=user_id,
            date=d,
            nm_id=999,
            doc_type="Продажа",
            retail_price=1000,
            ppvz_for_pay=850,
            delivery_rub=50,
            penalty=0,
            additional_payment=0,
            storage_fee=10,
            quantity=1,
        )
    )
    session.add(
        OperationalExpense(
            user_id=user_id,
            date=d,
            amount=25.5,
            comment="Оплата отгрузки/фулфилмента",
        )
    )
    session.commit()

    # Чтобы recalculate_pnl работал в той же транзакции, подменяем SessionLocal на нашу session.
    session.close = MagicMock()
    with patch("celery_app.tasks.SessionLocal", return_value=session):
        rec_res = recalculate_pnl(user_id, d.isoformat(), d.isoformat())

    assert rec_res["ok"] is True

    r = client.get(
        "/dashboard/pnl",
        params={"date_from": d.isoformat(), "date_to": d.isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["operation_expenses"] == 25.5
    assert items[0]["margin"] == 604.5


def test_pnl_only_operational_expenses_creates_day_and_is_negative_margin(authenticated_client):
    """Если в период есть только OperationalExpense, pnl_daily всё равно должен появиться и margin=-operation_expenses."""
    client, session, user_id, token = authenticated_client

    d = date(2025, 3, 20)
    amount = 25.5
    session.add(
        OperationalExpense(
            user_id=user_id,
            date=d,
            amount=amount,
            comment="Оплата отгрузки/фулфилмента",
        )
    )
    session.commit()

    # Чтобы recalculate_pnl работал в той же транзакции, подменяем SessionLocal на нашу session.
    session.close = MagicMock()
    with patch("celery_app.tasks.SessionLocal", return_value=session):
        rec_res = recalculate_pnl(user_id, d.isoformat(), d.isoformat())

    assert rec_res["ok"] is True
    assert rec_res["count"] == 1

    r = client.get(
        "/dashboard/pnl",
        params={"date_from": d.isoformat(), "date_to": d.isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["operation_expenses"] == amount
    assert items[0]["margin"] == -amount


def test_plan_fact_save_and_month_metrics_contract(authenticated_client):
    """
    План-факт:
    - POST /dashboard/plan-fact/plans сохраняет планы на месяц (upsert) и применяет derived суммы от % (от revenue).
    - GET /dashboard/plan-fact/months возвращает помесячный блок с метриками, где fact/plan и отношения месячные
      и НЕ зависят от выбранного периода (date_from/date_to может быть подмножеством месяца).
    """
    client, session, user_id, token = authenticated_client

    # Use a past month to make forecast deterministic (remaining_days=0 => forecast == fact).
    d1 = date(2020, 1, 1)
    d2 = date(2020, 1, 2)
    session.add_all(
        [
            PnlDaily(
                user_id=user_id,
                date=d1,
                revenue=100,
                commission=10,
                logistics=5,
                penalties=1,
                storage=2,
                ads_spend=3,
                cogs=50,
                tax=6,
                operation_expenses=4,
                margin=19,
            ),
            PnlDaily(
                user_id=user_id,
                date=d2,
                revenue=200,
                commission=20,
                logistics=10,
                penalties=0,
                storage=4,
                ads_spend=6,
                cogs=100,
                tax=12,
                operation_expenses=8,
                margin=40,
            ),
            # orders_sum comes from FunnelDaily aggregated across nm_id
            FunnelDaily(
                user_id=user_id,
                date=d1,
                nm_id=1,
                vendor_code="A",
                open_count=1,
                cart_count=1,
                order_count=1,
                order_sum=111,
            ),
            FunnelDaily(
                user_id=user_id,
                date=d2,
                nm_id=2,
                vendor_code="B",
                open_count=1,
                cart_count=1,
                order_count=1,
                order_sum=222,
            ),
        ]
    )
    session.commit()

    # Save plans for Jan 2020: revenue + percent-based cost plan.
    save_res = client.post(
        "/dashboard/plan-fact/plans",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "month": "2020-01-01",
            "values": {
                "revenue": 1000,
                "commission_pct": 10,  # should derive commission plan = 100
            },
        },
    )
    assert save_res.status_code == 200
    saved = save_res.json()
    assert saved["month"] == "2020-01-01"
    assert saved["values"]["revenue"] == 1000.0
    assert saved["values"]["commission_pct"] == 10.0
    assert saved["values"]["commission"] == 100.0

    # Query only one day as selected period; month stats still full month.
    r = client.get(
        "/dashboard/plan-fact/months",
        params={"date_from": "2020-01-02", "date_to": "2020-01-02"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    assert len(items) == 1
    assert set(items[0].keys()) == PLAN_FACT_MONTH_KEYS
    assert items[0]["month"] == "2020-01-01"
    metrics = items[0]["metrics"]
    assert isinstance(metrics, list)
    assert len(metrics) > 0
    for row in metrics:
        assert set(row.keys()) == PLAN_FACT_METRIC_KEYS

    by_key = {x["metric_key"]: x for x in metrics}
    assert by_key["revenue"]["is_percent"] is False
    assert by_key["revenue"]["fact"] == 300.0  # 100 + 200 for full month
    assert by_key["revenue"]["plan"] == 1000.0
    assert by_key["revenue"]["pct_of_plan"] == 0.3  # fact/plan
    assert by_key["revenue"]["forecast"] == 300.0  # past month => forecast == fact
    assert by_key["revenue"]["forecast_pct_of_plan"] == 0.3

    assert by_key["orders_sum"]["fact"] == 333.0  # 111 + 222

    # Percent metric: no forecast fields
    assert by_key["commission_pct"]["is_percent"] is True
    assert by_key["commission_pct"]["plan"] == 10.0
    assert by_key["commission_pct"]["pct_of_plan"] is None
    assert by_key["commission_pct"]["forecast"] is None


def test_real_wb_sales_response_structure():
    """
    Реальный запрос к WB API: ответ после парсинга имеет ожидаемую структуру.
    Пропуск, если WB_API_KEY не задан (например в .env).
    """
    try:
        from pathlib import Path
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
    except ImportError:
        pass
    wb_key = (os.getenv("WB_API_KEY") or "").strip()
    if not wb_key:
        pytest.skip("WB_API_KEY не задан — тест реального WB пропущен")
    from datetime import timedelta
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=1)
    date_from = start.isoformat()
    date_to = end.isoformat()
    import requests
    try:
        rows = fetch_sales(date_from, date_to, wb_key)
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else None
        if code == 429:
            pytest.skip("WB API rate limit (429) — пропускаем real-test")
        raise
    assert isinstance(rows, list)
    for row in rows:
        assert set(row.keys()) == WB_SALES_ROW_KEYS, f"Структура строки WB не совпадает: {row.keys()}"
        assert row["date"] is not None
        assert "nm_id" in row and "quantity" in row
