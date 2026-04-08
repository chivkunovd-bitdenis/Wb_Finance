from __future__ import annotations

import os
from datetime import UTC, datetime
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
def client_with_real_db(real_db_session):
    """
    Клиент для /billing/webhook/yookassa, который пишет в реальную БД (в транзакции с rollback).
    """
    user = User(
        email="wh-int@example.com",
        password_hash="$2b$12$fake",
        wb_api_key="k",
        is_active=True,
    )
    real_db_session.add(user)
    real_db_session.commit()
    real_db_session.refresh(user)
    user_id = str(user.id)

    # subscription + pending payment заранее, чтобы webhook обновил Payment и активировал подписку
    sub = Subscription(user_id=user_id, status="trial", auto_renew=True, provider="yookassa")
    real_db_session.add(sub)
    real_db_session.commit()
    real_db_session.refresh(sub)

    pay = Payment(
        user_id=user_id,
        subscription_id=str(sub.id),
        provider="yookassa",
        provider_payment_id="p1",
        idempotency_key="idem-test-1",
        amount=Decimal("1490.00"),
        currency="RUB",
        status="pending",
        raw_payload=None,
    )
    real_db_session.add(pay)
    real_db_session.commit()

    token = create_access_token(data={"sub": user_id})

    def get_db_override():
        yield real_db_session

    app.dependency_overrides[get_db] = get_db_override
    try:
        real_db_session.close = MagicMock()
        with TestClient(app) as client:
            yield client, real_db_session, user_id, token
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_billing_yookassa_webhook_succeeds_and_is_idempotent(client_with_real_db):
    """
    Контракт:
    - валидный webhook (секрет совпал) возвращает ok=true;
    - активирует подписку;
    - помечает Payment как succeeded;
    - повтор webhook с тем же event/payment_id не дублирует WebhookEvent и не ломает состояние.
    """
    client, session, user_id, _token = client_with_real_db

    payload = {
        "event": "payment.succeeded",
        "object": {"id": "p1", "metadata": {"user_id": user_id}},
    }

    with patch.dict(os.environ, {"YOOKASSA_WEBHOOK_SECRET": "sec"}):
        r1 = client.post(
            "/billing/webhook/yookassa",
            json=payload,
            headers={"x-webhook-secret": "sec"},
        )
        assert r1.status_code == 200
        assert r1.json() == {"ok": True}

        r2 = client.post(
            "/billing/webhook/yookassa",
            json=payload,
            headers={"x-webhook-secret": "sec"},
        )
        assert r2.status_code == 200
        assert r2.json() == {"ok": True}

    # subscription active
    sub = session.query(Subscription).filter(Subscription.user_id == user_id).first()
    assert isinstance(sub, Subscription)
    assert sub.status == "active"
    assert sub.current_period_end is not None
    assert sub.current_period_end > datetime.now(UTC)

    # payment succeeded
    pay = session.query(Payment).filter(Payment.provider_payment_id == "p1").first()
    assert isinstance(pay, Payment)
    assert pay.status == "succeeded"
    assert pay.paid_at is not None

    # webhook event stored exactly once
    rows = session.query(WebhookEvent).filter(WebhookEvent.event_key == "payment.succeeded:p1").all()
    assert len(rows) == 1

