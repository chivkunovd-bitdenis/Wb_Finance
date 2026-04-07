import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.subscription import Subscription
from app.models.user import User
from app.models.webhook_event import WebhookEvent
from app.services.billing_service import (
    collect_due_reminders,
    create_checkout,
    get_billing_status,
    process_yookassa_webhook,
    require_access,
    sync_latest_yookassa_payment,
    yookassa_money_string,
)


def test_yookassa_money_string_two_fractional_digits() -> None:
    assert yookassa_money_string(Decimal("1490")) == "1490.00"
    assert yookassa_money_string(Decimal("10.1")) == "10.10"
    assert yookassa_money_string(Decimal("99.999")) == "100.00"


def test_create_checkout_without_yookassa_keys_empty_confirmation_url() -> None:
    """Без ключей ЮKassa — mock-платёж, без return_url в ответе (иначе фронт «крутится» на месте)."""
    sub = MagicMock()
    sub.id = "sub-mock"
    db = MagicMock()
    user = User(id="u1", email="a@b.c", password_hash="x", wb_api_key="k", is_active=True)
    with (
        patch("app.services.billing_service.get_or_create_subscription", return_value=sub),
        patch.dict(os.environ, {"YOOKASSA_SHOP_ID": "", "YOOKASSA_SECRET_KEY": ""}),
    ):
        out = create_checkout(db, user, Decimal("1490"), "https://app.example/billing?payment=return")
    assert out["confirmation_url"] == ""
    assert out["payment_id"].startswith("mock-")
    db.add.assert_called_once()
    db.commit.assert_called()


def test_create_checkout_includes_receipt_when_required() -> None:
    db = MagicMock()
    sub = MagicMock()
    sub.id = "sub-mock"
    user = User(id="u1", email="u1@example.com", password_hash="x", wb_api_key="k", is_active=True)

    with (
        patch("app.services.billing_service.get_or_create_subscription", return_value=sub),
        patch.dict(
            os.environ,
            {
                "YOOKASSA_SHOP_ID": "shop",
                "YOOKASSA_SECRET_KEY": "sec",
                "YOOKASSA_REQUIRE_RECEIPT": "true",
                "YOOKASSA_VAT_CODE": "1",
            },
        ),
        patch("app.services.billing_service.requests.post") as mock_post,
    ):
        mock_post.return_value.raise_for_status = MagicMock()
        mock_post.return_value.json.return_value = {"id": "p1", "status": "pending", "confirmation": {"confirmation_url": "u"}}
        _ = create_checkout(db, user, Decimal("1490"), "https://app.example/billing?payment=return")

    (_, kwargs) = mock_post.call_args
    sent = kwargs["json"]
    assert "receipt" in sent
    assert sent["receipt"]["customer"]["email"] == "u1@example.com"
    assert sent["receipt"]["items"][0]["vat_code"] == 1


def test_sync_latest_yookassa_payment_no_pending() -> None:
    db = MagicMock()
    end = MagicMock()
    end.first.return_value = None
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.order_by.return_value = end
    db.query.return_value = chain
    user = User(id="u1", email="a@b.c", password_hash="x", wb_api_key=None, is_active=True)
    with patch.dict(os.environ, {"YOOKASSA_SHOP_ID": "shop", "YOOKASSA_SECRET_KEY": "sec"}):
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

    with patch.dict(os.environ, {"YOOKASSA_SHOP_ID": "shop", "YOOKASSA_SECRET_KEY": "sec"}), patch(
        "app.services.billing_service.requests.get"
    ) as mock_get:
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


def test_get_billing_status_days_left_ceil_for_active_period() -> None:
    """
    Регрессия: сразу после оплаты может показывать 29 вместо 30, потому что timedelta.days
    округляет вниз. Должны показывать потолок по суткам.
    """
    db = MagicMock()
    user = User(id="u1", email="u1@example.com", password_hash="h", wb_api_key="k", is_active=True)
    now = datetime(2026, 4, 6, 12, 0, tzinfo=UTC)
    sub = Subscription(user_id="u1", status="active", current_period_end=now + timedelta(days=30) - timedelta(seconds=1))
    with (
        patch("app.services.billing_service._is_lifetime", return_value=False),
        patch("app.services.billing_service.get_or_create_subscription", return_value=sub),
        patch("app.services.billing_service.utc_now", return_value=now),
    ):
        out = get_billing_status(db, user)
    assert out["subscription_status"] == "active"
    assert out["days_left"] == 30
