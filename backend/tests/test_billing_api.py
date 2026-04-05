from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.db import get_db
from app.dependencies import get_current_user
from app.main import app
from app.models.user import User


def _mock_get_db():
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    try:
        yield session
    finally:
        pass


def _mock_user() -> User:
    return User(
        id="billing-user-id",
        email="billing@example.com",
        password_hash="fake",
        wb_api_key=None,
        is_active=True,
    )


def test_billing_status_ok():
    app.dependency_overrides[get_db] = _mock_get_db
    app.dependency_overrides[get_current_user] = _mock_user
    with patch("app.routers.billing.get_billing_status") as mock_status:
        mock_status.return_value = {
            "subscription_status": "trial",
            "trial_ends_at": None,
            "current_period_ends_at": None,
            "auto_renew": True,
            "is_access_blocked": False,
            "days_left": 5,
        }
        with TestClient(app) as client:
            r = client.get("/billing/status")
        assert r.status_code == 200
        assert r.json()["subscription_status"] == "trial"
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


def test_billing_checkout_ok():
    app.dependency_overrides[get_db] = _mock_get_db
    app.dependency_overrides[get_current_user] = _mock_user
    with patch("app.routers.billing.create_checkout") as mock_checkout:
        mock_checkout.return_value = {
            "payment_id": "p-1",
            "confirmation_url": "https://example.org/pay",
        }
        with TestClient(app) as client:
            r = client.post("/billing/checkout", json={"amount": 1990})
        assert r.status_code == 200
        assert r.json()["payment_id"] == "p-1"
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


def test_billing_webhook_invalid_secret():
    app.dependency_overrides[get_db] = _mock_get_db
    with patch("app.routers.billing.process_yookassa_webhook") as mock_webhook:
        mock_webhook.side_effect = ValueError("Invalid webhook signature")
        with TestClient(app) as client:
            r = client.post(
                "/billing/webhook/yookassa",
                json={"event": "payment.succeeded", "object": {"id": "p1", "metadata": {"user_id": "u1"}}},
                headers={"x-webhook-secret": "wrong"},
            )
        assert r.status_code == 401
    app.dependency_overrides.pop(get_db, None)


def test_auth_update_wb_key_starts_trial():
    session = MagicMock()
    user = _mock_user()

    def _db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    with patch("app.routers.auth.start_trial_if_needed") as mock_trial:
        with TestClient(app) as client:
            r = client.put("/auth/wb-key", json={"wb_api_key": "new-key"})
        assert r.status_code == 200
        assert r.json()["wb_api_key"] == "new-key"
        mock_trial.assert_called_once()
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)
