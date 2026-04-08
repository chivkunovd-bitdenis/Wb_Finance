from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db import get_db
from app.main import app
from app.models.payment import Payment
from app.models.subscription import Subscription
from app.models.user import User
from app.models.webhook_event import WebhookEvent


@pytest.fixture
def billing_client(real_db_session):
    """
    Клиент с реальной БД для billing+access сценариев.

    Делаем JWT через create_access_token и подменяем get_db, чтобы API писал в ту же session.
    """
    user = User(
        email="bill-edge@example.com",
        password_hash="$2b$12$fake",
        wb_api_key="k",
        is_active=True,
    )
    real_db_session.add(user)
    real_db_session.commit()
    real_db_session.refresh(user)
    user_id = str(user.id)
    token = create_access_token(data={"sub": user_id})

    # Создаём subscription (trial) как базовое состояние
    sub = Subscription(user_id=user_id, status="trial", auto_renew=True, provider="yookassa")
    real_db_session.add(sub)
    real_db_session.commit()

    def get_db_override():
        yield real_db_session

    app.dependency_overrides[get_db] = get_db_override
    try:
        real_db_session.close = MagicMock()
        with TestClient(app) as client:
            yield client, real_db_session, user_id, token
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_billing_webhook_payment_canceled_marks_payment_failed(billing_client):
    client, session, user_id, _token = billing_client

    pay = Payment(
        user_id=user_id,
        subscription_id=session.query(Subscription).filter(Subscription.user_id == user_id).first().id,
        provider="yookassa",
        provider_payment_id="pcancel-1",
        idempotency_key="idem-cancel-1",
        amount=Decimal("1490.00"),
        currency="RUB",
        status="pending",
        raw_payload=None,
    )
    session.add(pay)
    session.commit()

    payload = {"event": "payment.canceled", "object": {"id": "pcancel-1", "metadata": {"user_id": user_id}}}
    with patch.dict(os.environ, {"YOOKASSA_WEBHOOK_SECRET": "sec"}):
        r1 = client.post("/billing/webhook/yookassa", json=payload, headers={"x-webhook-secret": "sec"})
        assert r1.status_code == 200
        r2 = client.post("/billing/webhook/yookassa", json=payload, headers={"x-webhook-secret": "sec"})
        assert r2.status_code == 200

    pay2 = session.query(Payment).filter(Payment.provider_payment_id == "pcancel-1").first()
    assert isinstance(pay2, Payment)
    assert pay2.status == "failed"

    rows = session.query(WebhookEvent).filter(WebhookEvent.event_key == "payment.canceled:pcancel-1").all()
    assert len(rows) == 1


def test_billing_webhook_succeeded_when_payment_row_missing_still_activates_subscription(billing_client):
    """
    Гонка: webhook может прийти раньше, чем мы записали Payment.
    Должны активировать подписку и записать WebhookEvent, даже если Payment не найден.
    """
    client, session, user_id, _token = billing_client
    payload = {"event": "payment.succeeded", "object": {"id": "p-webhook-first", "metadata": {"user_id": user_id}}}
    with patch.dict(os.environ, {"YOOKASSA_WEBHOOK_SECRET": "sec"}):
        r = client.post("/billing/webhook/yookassa", json=payload, headers={"x-webhook-secret": "sec"})
    assert r.status_code == 200

    sub = session.query(Subscription).filter(Subscription.user_id == user_id).first()
    assert isinstance(sub, Subscription)
    assert sub.status == "active"
    assert sub.current_period_end is not None
    assert sub.current_period_end > datetime.now(UTC)

    rows = session.query(WebhookEvent).filter(WebhookEvent.event_key == "payment.succeeded:p-webhook-first").all()
    assert len(rows) == 1


def test_checkout_double_click_creates_two_payments_in_db_when_yookassa_not_configured(billing_client):
    client, session, user_id, token = billing_client
    # checkout требует current_user через get_current_user, поэтому используем Authorization header
    # (get_current_user подключён глобально в app роутерах).
    headers = {"Authorization": f"Bearer {token}"}

    with patch.dict(os.environ, {"YOOKASSA_SHOP_ID": "", "YOOKASSA_SECRET_KEY": ""}):
        r1 = client.post("/billing/checkout", json={"amount": 1990, "return_url": "https://app.example/billing?payment=return"}, headers=headers)
        r2 = client.post("/billing/checkout", json={"amount": 1990, "return_url": "https://app.example/billing?payment=return"}, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200

    payments = session.query(Payment).filter(Payment.user_id == user_id).order_by(Payment.created_at.desc()).all()
    # В текущей реализации каждый checkout создаёт отдельный Payment (idem генерируется на каждый вызов)
    assert len(payments) >= 2
    assert str(payments[0].provider_payment_id).startswith("mock-")
    assert str(payments[1].provider_payment_id).startswith("mock-")


def test_access_is_unblocked_after_successful_webhook(billing_client):
    client, session, user_id, token = billing_client
    headers = {"Authorization": f"Bearer {token}"}

    # Сначала делаем подписку "expired" (чтобы гейтинг реально блокировал).
    sub = session.query(Subscription).filter(Subscription.user_id == user_id).first()
    assert isinstance(sub, Subscription)
    # Важно: get_current_user вызывает start_trial_if_needed() лениво.
    # Если trial_started_at пустой, он может снова включить trial и тест потеряет смысл.
    now = datetime.now(UTC)
    sub.status = "expired"
    sub.trial_started_at = now - timedelta(days=10)
    sub.trial_ends_at = now - timedelta(days=1)
    session.commit()

    blocked = client.get("/dashboard/state", headers=headers)
    assert blocked.status_code == 402

    payload = {"event": "payment.succeeded", "object": {"id": "p-unblock-1", "metadata": {"user_id": user_id}}}
    with patch.dict(os.environ, {"YOOKASSA_WEBHOOK_SECRET": "sec"}):
        r = client.post("/billing/webhook/yookassa", json=payload, headers={"x-webhook-secret": "sec"})
    assert r.status_code == 200

    ok = client.get("/dashboard/state", headers=headers)
    assert ok.status_code == 200

