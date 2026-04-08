from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.dependencies import get_current_user
from app.main import app
from app.models.payment import Payment
from app.models.subscription import Subscription
from app.models.user import User
from app.services.billing_service import YooKassaRequestError


@pytest.fixture
def billing_user():
    return User(
        email="billing@example.com",
        password_hash="$2b$12$fake",
        wb_api_key="test-wb-key",
        is_active=True,
    )


@pytest.fixture
def billing_client_real_db(real_db_session, billing_user):
    """
    Интеграционный клиент: роутер billing → сервис → реальная БД (в транзакции с rollback).
    """
    real_db_session.add(billing_user)
    real_db_session.commit()
    real_db_session.refresh(billing_user)

    def _db():
        yield real_db_session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: billing_user
    try:
        # не даём сервисам/таскам закрывать общую session в тесте
        real_db_session.close = MagicMock()
        with TestClient(app) as client:
            yield client, real_db_session, str(billing_user.id)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_billing_status_ok(billing_client_real_db):
    client, session, user_id = billing_client_real_db
    now = datetime.now(UTC)
    session.add(
        Subscription(
            user_id=user_id,
            status="trial",
            trial_started_at=now - timedelta(days=1),
            trial_ends_at=now + timedelta(days=4),
            auto_renew=True,
            provider="yookassa",
        )
    )
    session.commit()

    r = client.get("/billing/status")
    assert r.status_code == 200
    data = r.json()
    assert data["subscription_status"] == "trial"
    assert data["is_access_blocked"] is False
    assert isinstance(data["days_left"], int)


def test_billing_checkout_ok_creates_payment_in_db_when_yookassa_not_configured(billing_client_real_db):
    client, session, user_id = billing_client_real_db
    with patch.dict(os.environ, {"YOOKASSA_SHOP_ID": "", "YOOKASSA_SECRET_KEY": ""}):
        r = client.post("/billing/checkout", json={"amount": 1990, "return_url": "https://app.example/billing?payment=return"})
    assert r.status_code == 200
    payload = r.json()
    assert payload["payment_id"].startswith("mock-")
    assert payload["confirmation_url"] == ""

    pay = (
        session.query(Payment)
        .filter(Payment.user_id == user_id)
        .order_by(Payment.created_at.desc())
        .first()
    )
    assert isinstance(pay, Payment)
    assert str(pay.provider_payment_id).startswith("mock-")
    assert pay.status == "pending"
    assert Decimal(str(pay.amount)) == Decimal("1990")


def test_billing_yookassa_sync_return_ok():
    # оставляем как контракт роутера: сеть не дергаем, сервис подменяем
    client = None
    session = MagicMock()
    user = User(
        id="billing-user-id",
        email="billing@example.com",
        password_hash="fake",
        wb_api_key=None,
        is_active=True,
    )

    def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with patch("app.routers.billing.sync_latest_yookassa_payment") as mock_sync:
            mock_sync.return_value = {"activated": True, "payment_status": "succeeded", "detail": None}
            with TestClient(app) as client:
                r = client.post("/billing/yookassa/sync-return")
            assert r.status_code == 200
            assert r.json()["activated"] is True
            assert r.json()["payment_status"] == "succeeded"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_billing_checkout_yookassa_upstream_error():
    # контракт обработки исключения YooKassaRequestError → 502
    session = MagicMock()
    user = User(
        id="billing-user-id",
        email="billing@example.com",
        password_hash="fake",
        wb_api_key=None,
        is_active=True,
    )

    def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with patch("app.routers.billing.create_checkout") as mock_checkout:
            mock_checkout.side_effect = YooKassaRequestError("ЮKassa недоступна")
            with TestClient(app) as client:
                r = client.post("/billing/checkout", json={"amount": 100})
            assert r.status_code == 502
            assert r.json()["detail"] == "ЮKassa недоступна"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_billing_webhook_invalid_secret(billing_client_real_db):
    client, _session, user_id = billing_client_real_db
    with patch.dict(os.environ, {"YOOKASSA_WEBHOOK_SECRET": "sec"}):
        r = client.post(
            "/billing/webhook/yookassa",
            json={"event": "payment.succeeded", "object": {"id": "p1", "metadata": {"user_id": user_id}}},
            headers={"x-webhook-secret": "wrong"},
        )
    assert r.status_code == 401


def test_auth_update_wb_key_starts_trial():
    session = MagicMock()
    user = User(
        id="billing-user-id",
        email="billing@example.com",
        password_hash="fake",
        wb_api_key=None,
        is_active=True,
    )

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
