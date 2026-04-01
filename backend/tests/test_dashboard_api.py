"""
Тесты эндпоинтов дашборда: GET /dashboard/pnl, /articles, /funnel, /sku, /operational-expenses.
БД подменена на mock; без JWT — 401.
"""
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import get_db

FAKE_HASH = "$2b$12$faketesthash"


def _mock_get_db_dashboard():
    """Сессия: пользователь для логина и get_current_user; данные дашборда пустые."""
    from datetime import date as date_type
    from app.models.user import User
    from app.models.pnl_daily import PnlDaily
    from app.models.article import Article
    from app.models.funnel_daily import FunnelDaily
    from app.models.funnel_backfill_state import FunnelBackfillState
    from app.models.finance_backfill_state import FinanceBackfillState
    from app.models.sku_daily import SkuDaily
    from app.models.operational_expense import OperationalExpense

    user = User(
        id="dashboard-user-id",
        email="dash@example.com",
        password_hash=FAKE_HASH,
        wb_api_key="wb-key",
        is_active=True,
    )
    session = MagicMock()
    session.get.return_value = user

    user_chain = MagicMock()
    user_chain.filter.return_value = user_chain
    user_chain.first.return_value = user

    data_chain = MagicMock()
    data_chain.filter.return_value = data_chain
    data_chain.order_by.return_value = data_chain
    data_chain.first.return_value = None
    data_chain.all.return_value = []

    existing_op = MagicMock()
    existing_op.id = "op-expense-1"
    existing_op.date = date_type(2025, 3, 20)
    existing_op.amount = 25.5
    existing_op.comment = "test"

    multi_chain = MagicMock()
    multi_chain.filter.return_value = multi_chain
    multi_chain.outerjoin.return_value = multi_chain
    multi_chain.order_by.return_value = multi_chain
    multi_chain.first.return_value = None
    multi_chain.all.return_value = []
    multi_chain.subquery.return_value = MagicMock(
        c=MagicMock(
            user_id=MagicMock(),
            nm_id=MagicMock(),
            vendor_code=MagicMock(),
            rn=MagicMock(),
        )
    )

    def _query(*models):
        # SQLAlchemy допускает db.query(col1, col2, ...) — для таких вызовов
        # нам достаточно универсальной цепочки (тесты проверяют только, что эндпоинт отвечает).
        if len(models) != 1:
            return multi_chain

        model = models[0]
        if model is User:
            return user_chain
        # Для PnlDaily, Article, FunnelDaily, SkuDaily — пустые данные
        if model is PnlDaily or model is Article or model is FunnelDaily or model is SkuDaily:
            return data_chain
        # db.query(PnlDaily.date) в коде автостарта финансов
        if getattr(model, "class_", None) is PnlDaily and getattr(model, "key", None) == "date":
            return data_chain
        if model is FunnelBackfillState:
            fb_chain = MagicMock()
            fb_chain.filter.return_value = fb_chain
            fb_chain.first.return_value = None
            return fb_chain
        if model is FinanceBackfillState:
            fin_chain = MagicMock()
            fin_chain.filter.return_value = fin_chain
            fin_chain.first.return_value = None
            return fin_chain
        if model is OperationalExpense:
            op_chain = MagicMock()
            op_chain.filter.return_value = op_chain
            op_chain.order_by.return_value = op_chain
            op_chain.first.return_value = existing_op
            op_chain.all.return_value = []
            return op_chain
        return data_chain

    session.query.side_effect = _query
    try:
        yield session
    finally:
        pass


@pytest.fixture
def client_dashboard():
    def _fake_verify(plain: str, hashed: str) -> bool:
        return plain == "pass" and hashed == FAKE_HASH

    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.verify.side_effect = _fake_verify
        app.dependency_overrides[get_db] = _mock_get_db_dashboard
        try:
            with TestClient(app) as c:
                yield c
        finally:
            app.dependency_overrides.pop(get_db, None)


def _get_token(client_dashboard: TestClient) -> str:
    r = client_dashboard.post(
        "/auth/login",
        json={"email": "dash@example.com", "password": "pass"},
    )
    assert r.status_code == 200
    return r.json()["access_token"]


def test_dashboard_state_without_token_returns_401(client_dashboard: TestClient):
    r = client_dashboard.get("/dashboard/state")
    assert r.status_code == 401


def test_dashboard_state_with_token_no_data(client_dashboard: TestClient):
    token = _get_token(client_dashboard)
    r = client_dashboard.get("/dashboard/state", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert data["has_data"] is False
    assert data["min_date"] is None
    assert data["max_date"] is None
    assert data["has_2025"] is False
    assert data["has_2026"] is False
    assert data["has_funnel"] is False
    fb = data.get("funnel_ytd_backfill")
    assert isinstance(fb, dict)
    assert "year" in fb and "status" in fb


def test_dashboard_pnl_without_token_returns_401(client_dashboard: TestClient):
    r = client_dashboard.get("/dashboard/pnl")
    assert r.status_code == 401


def test_dashboard_pnl_with_token_returns_list(client_dashboard: TestClient):
    token = _get_token(client_dashboard)
    r = client_dashboard.get("/dashboard/pnl", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_dashboard_articles_with_token_returns_list(client_dashboard: TestClient):
    token = _get_token(client_dashboard)
    r = client_dashboard.get("/dashboard/articles", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_dashboard_funnel_with_token_returns_list(client_dashboard: TestClient):
    token = _get_token(client_dashboard)
    r = client_dashboard.get("/dashboard/funnel", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_dashboard_sku_with_token_returns_list(client_dashboard: TestClient):
    token = _get_token(client_dashboard)
    r = client_dashboard.get("/dashboard/sku", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_operational_expenses_without_token_returns_401(client_dashboard: TestClient):
    r = client_dashboard.get("/dashboard/operational-expenses")
    assert r.status_code == 401


def test_operational_expenses_with_token_returns_list(client_dashboard: TestClient):
    token = _get_token(client_dashboard)
    r = client_dashboard.get("/dashboard/operational-expenses", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_create_operational_expense_with_token_returns_object(client_dashboard: TestClient):
    token = _get_token(client_dashboard)
    r = client_dashboard.post(
        "/dashboard/operational-expenses",
        headers={"Authorization": f"Bearer {token}"},
        json={"date": "2025-03-20", "amount": 123.45, "comment": "test"},
    )
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"id", "date", "amount", "comment"}
    assert data["date"] == "2025-03-20"
    assert data["amount"] == 123.45


def test_update_operational_expense_with_token_returns_object(client_dashboard: TestClient):
    token = _get_token(client_dashboard)
    r = client_dashboard.put(
        "/dashboard/operational-expenses/op-expense-1",
        headers={"Authorization": f"Bearer {token}"},
        json={"date": "2025-03-21", "amount": 50.0, "comment": "updated"},
    )
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"id", "date", "amount", "comment"}
    assert data["date"] == "2025-03-21"
    assert data["amount"] == 50.0
