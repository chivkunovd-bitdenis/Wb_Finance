from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.subscription import Subscription
from app.models.user import User
from app.models.webhook_event import WebhookEvent
from app.services.billing_service import (
    collect_due_reminders,
    process_yookassa_webhook,
    require_access,
    sync_latest_yookassa_payment,
    yookassa_money_string,
)


def test_yookassa_money_string_two_fractional_digits() -> None:
    assert yookassa_money_string(Decimal("1490")) == "1490.00"
    assert yookassa_money_string(Decimal("10.1")) == "10.10"
    assert yookassa_money_string(Decimal("99.999")) == "100.00"


def test_sync_latest_yookassa_payment_no_pending() -> None:
    db = MagicMock()
    end = MagicMock()
    end.first.return_value = None
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = end
    db.query.return_value = chain
    user = User(id="u1", email="a@b.c", password_hash="x", wb_api_key=None, is_active=True)
    with patch("app.services.billing_service.YOOKASSA_SHOP_ID", "shop"), patch(
        "app.services.billing_service.YOOKASSA_SECRET_KEY", "sec"
    ):
        out = sync_latest_yookassa_payment(db, user)
    assert out["activated"] is False
    assert out["detail"] == "no_pending_payment"


def test_sync_latest_yookassa_payment_activates_on_succeeded() -> None:
    db = MagicMock()
    pay = MagicMock()
    pay.provider_payment_id = "ym-pay-1"
    pay.status = "pending"
    pay.user_id = "u1"
    end = MagicMock()
    end.first.return_value = pay
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = end
    db.query.return_value = chain
    user = User(id="u1", email="a@b.c", password_hash="x", wb_api_key=None, is_active=True)

    with patch("app.services.billing_service.YOOKASSA_SHOP_ID", "shop"), patch(
        "app.services.billing_service.YOOKASSA_SECRET_KEY", "sec"
    ), patch("app.services.billing_service.requests.get") as mock_get:
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = {
            "id": "ym-pay-1",
            "status": "succeeded",
            "paid": True,
        }
        with patch("app.services.billing_service.activate_subscription_from_payment") as mock_act:
            out = sync_latest_yookassa_payment(db, user)
    assert out["activated"] is True
    mock_act.assert_called_once()


def test_require_access_blocks_expired_trial():
    db = MagicMock()
    user = User(id="u1", email="u1@example.com", password_hash="h", wb_api_key="k", is_active=True)
    expired_sub = Subscription(
        user_id="u1",
        status="trial",
        trial_started_at=datetime.now(UTC) - timedelta(days=10),
        trial_ends_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.query.return_value.filter.return_value.first.return_value = expired_sub

    with pytest.raises(PermissionError):
        require_access(db, user)


def test_collect_due_reminders_idempotent():
    db = MagicMock()
    now = datetime(2026, 3, 26, 10, 0, tzinfo=UTC)
    sub = Subscription(
        user_id="u1",
        status="trial",
        trial_started_at=now - timedelta(days=2),
        trial_ends_at=now + timedelta(days=3),
    )

    q_sub = MagicMock()
    q_sub.filter.return_value.all.return_value = [sub]

    q_rem = MagicMock()
    # first run: no reminders, second run: already exists
    q_rem.filter.return_value.first.side_effect = [None, None, object(), object()]

    def _query(model):
        if model is Subscription:
            return q_sub
        return q_rem

    db.query.side_effect = _query

    with patch("app.services.billing_service.utc_now", return_value=now):
        created_first = collect_due_reminders(db)
        created_second = collect_due_reminders(db)
    assert created_first == 2
    assert created_second == 0


def test_process_yookassa_webhook_duplicate_is_ignored():
    db = MagicMock()
    first_q = MagicMock()
    first_q.filter.return_value.first.return_value = None
    second_q = MagicMock()
    second_q.filter.return_value.first.return_value = WebhookEvent(provider="yookassa", event_key="payment.succeeded:p1")
    payment_q = MagicMock()
    payment_q.filter.return_value.first.return_value = None
    seq = [first_q, second_q]

    def _query(model):
        if model is WebhookEvent:
            return seq.pop(0)
        return payment_q

    db.query.side_effect = _query
    payload = {"event": "payment.succeeded", "object": {"id": "p1", "metadata": {"user_id": "u1"}}}

    with patch("app.services.billing_service.activate_subscription_from_payment") as mock_activate:
        process_yookassa_webhook(db, payload, None)
        process_yookassa_webhook(db, payload, None)
        mock_activate.assert_called_once()
