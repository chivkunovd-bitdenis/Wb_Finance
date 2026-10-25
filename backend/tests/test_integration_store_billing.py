"""
Интеграционные тесты биллинга выданного магазина (нужна PostgreSQL, см. conftest.real_db_session).

Сотрудник работает под подпиской владельца: при запросе в выданный магазин (X-Store-Owner-Id)
доступ определяет подписка владельца. Собственный аккаунт сотрудника при этом не учитывается.
Без заголовка (собственный магазин) действует прежняя логика.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db import get_db
from app.dependencies import GRANTED_ACCESS_BLOCKED_DETAIL
from app.main import app
from app.models.store_access_grant import StoreAccessGrant
from app.models.subscription import Subscription
from app.models.user import User


@pytest.fixture
def client_real_db(real_db_session):
    def _get_db_override():
        yield real_db_session

    app.dependency_overrides[get_db] = _get_db_override
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


def _mk_user(email: str, *, wb_api_key: str | None = "wb-key") -> User:
    return User(
        id=str(uuid4()),
        email=email,
        password_hash="hash",
        wb_api_key=wb_api_key,
        is_active=True,
    )


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'sub': str(user.id)})}"}


def _set_trial(session, user: User, *, ends_in_days: float) -> datetime:
    """Подписка в демо: ends_in_days > 0 — ещё действует, < 0 — истекла. Возвращает срок окончания."""
    now = datetime.now(UTC)
    ends_at = now + timedelta(days=ends_in_days)
    session.add(
        Subscription(
            user_id=str(user.id),
            status="trial",
            trial_started_at=now - timedelta(days=5),
            trial_ends_at=ends_at,
            auto_renew=True,
            provider="yookassa",
        )
    )
    return ends_at


def _grant(session, owner: User, viewer: User) -> None:
    session.add(
        StoreAccessGrant(
            store_owner_user_id=str(owner.id),
            viewer_user_id=str(viewer.id),
            status="active",
            revoked_at=None,
        )
    )


GATED_PATH = "/dashboard/operational-expenses"


def test_manager_is_not_blocked_when_seller_trial_is_active(client_real_db, real_db_session):
    owner = _mk_user("bill-owner-1@example.com")
    manager = _mk_user("bill-manager-1@example.com", wb_api_key=None)
    real_db_session.add_all([owner, manager])
    real_db_session.commit()
    _set_trial(real_db_session, owner, ends_in_days=5)
    _set_trial(real_db_session, manager, ends_in_days=-2)  # собственное демо истекло
    _grant(real_db_session, owner, manager)
    real_db_session.commit()

    granted = client_real_db.get(
        GATED_PATH,
        headers={**_auth(manager), "X-Store-Owner-Id": str(owner.id)},
    )
    assert granted.status_code == 200, granted.text

    # Без заголовка это собственный магазин сотрудника: его истёкшее демо блокирует по-прежнему.
    own = client_real_db.get(GATED_PATH, headers=_auth(manager))
    assert own.status_code == 402


def test_manager_is_blocked_when_seller_subscription_expired(client_real_db, real_db_session):
    owner = _mk_user("bill-owner-2@example.com")
    manager = _mk_user("bill-manager-2@example.com", wb_api_key=None)
    real_db_session.add_all([owner, manager])
    real_db_session.commit()
    _set_trial(real_db_session, owner, ends_in_days=-1)
    _set_trial(real_db_session, manager, ends_in_days=10)  # собственное демо действует — не важно
    _grant(real_db_session, owner, manager)
    real_db_session.commit()

    headers = {**_auth(manager), "X-Store-Owner-Id": str(owner.id)}
    r = client_real_db.get(GATED_PATH, headers=headers)
    assert r.status_code == 402
    assert r.json()["detail"] == GRANTED_ACCESS_BLOCKED_DETAIL
    # Сотруднику не предлагаем оплачивать чужой магазин: в тексте нет призыва к оплате.
    assert "оплат" not in r.json()["detail"].lower()

    status = client_real_db.get("/billing/status", headers=headers)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["is_access_blocked"] is True
    assert body["access_source"] == "granted"
    assert body["store_owner_email"] == "bill-owner-2@example.com"


def test_manager_own_account_is_unaffected_by_seller_subscription(client_real_db, real_db_session):
    owner = _mk_user("bill-owner-3@example.com")
    manager = _mk_user("bill-manager-3@example.com", wb_api_key=None)
    real_db_session.add_all([owner, manager])
    real_db_session.commit()
    _set_trial(real_db_session, owner, ends_in_days=10)  # владелец в порядке
    _set_trial(real_db_session, manager, ends_in_days=-3)  # собственное демо истекло
    _grant(real_db_session, owner, manager)
    real_db_session.commit()

    # Заголовок собственного магазина — собственная подписка, как раньше.
    own_header = client_real_db.get(
        GATED_PATH,
        headers={**_auth(manager), "X-Store-Owner-Id": str(manager.id)},
    )
    assert own_header.status_code == 402

    # И статус биллинга собственного магазина — собственный.
    status = client_real_db.get(
        "/billing/status",
        headers={**_auth(manager), "X-Store-Owner-Id": str(manager.id)},
    )
    assert status.status_code == 200
    assert status.json()["access_source"] == "own"
    assert status.json()["is_access_blocked"] is True


def test_seller_expired_does_not_block_manager_own_active_store(client_real_db, real_db_session):
    owner = _mk_user("bill-owner-4@example.com")
    manager = _mk_user("bill-manager-4@example.com", wb_api_key=None)
    real_db_session.add_all([owner, manager])
    real_db_session.commit()
    _set_trial(real_db_session, owner, ends_in_days=-1)
    _set_trial(real_db_session, manager, ends_in_days=10)
    _grant(real_db_session, owner, manager)
    real_db_session.commit()

    # Собственный магазин сотрудника (без заголовка) работает по его собственной подписке.
    r = client_real_db.get(GATED_PATH, headers=_auth(manager))
    assert r.status_code == 200


def test_billing_status_for_granted_store_reports_owner_access_until(client_real_db, real_db_session):
    owner = _mk_user("bill-owner-5@example.com")
    manager = _mk_user("bill-manager-5@example.com", wb_api_key=None)
    real_db_session.add_all([owner, manager])
    real_db_session.commit()
    owner_ends = _set_trial(real_db_session, owner, ends_in_days=5)
    _grant(real_db_session, owner, manager)
    real_db_session.commit()

    r = client_real_db.get(
        "/billing/status",
        headers={**_auth(manager), "X-Store-Owner-Id": str(owner.id)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_source"] == "granted"
    assert body["store_owner_email"] == "bill-owner-5@example.com"
    assert body["is_access_blocked"] is False
    assert body["subscription_status"] == "trial"
    assert datetime.fromisoformat(body["access_until"]) == owner_ends


def test_checkout_and_yookassa_sync_are_not_available_in_granted_store(client_real_db, real_db_session):
    owner = _mk_user("bill-owner-6@example.com")
    manager = _mk_user("bill-manager-6@example.com", wb_api_key=None)
    real_db_session.add_all([owner, manager])
    real_db_session.commit()
    _set_trial(real_db_session, owner, ends_in_days=-1)  # даже когда доступ закрыт
    _grant(real_db_session, owner, manager)
    real_db_session.commit()

    headers = {**_auth(manager), "X-Store-Owner-Id": str(owner.id)}
    with patch("app.routers.billing.create_checkout") as mock_checkout:
        checkout = client_real_db.post("/billing/checkout", headers=headers, json={"amount": 1490})
    assert checkout.status_code == 403
    assert checkout.json()["detail"] == "Оплату вносит владелец магазина"
    mock_checkout.assert_not_called()

    with patch("app.routers.billing.sync_latest_yookassa_payment") as mock_sync:
        sync = client_real_db.post("/billing/yookassa/sync-return", headers=headers)
    assert sync.status_code == 403
    mock_sync.assert_not_called()


def test_checkout_in_own_store_still_creates_payment_for_own_account(client_real_db, real_db_session):
    owner = _mk_user("bill-owner-7@example.com")
    real_db_session.add(owner)
    real_db_session.commit()

    with patch(
        "app.routers.billing.create_checkout",
        return_value={"payment_id": "p-1", "confirmation_url": "https://pay.example/p-1"},
    ) as mock_checkout:
        r = client_real_db.post("/billing/checkout", headers=_auth(owner), json={"amount": 1490})
    assert r.status_code == 200, r.text
    assert r.json()["payment_id"] == "p-1"
    assert mock_checkout.call_args.args[1].id == owner.id


def test_employee_without_own_wb_key_gets_granted_store_as_default(client_real_db, real_db_session):
    owner = _mk_user("bill-owner-8@example.com")
    employee = _mk_user("bill-employee-8@example.com", wb_api_key=None)
    real_db_session.add_all([owner, employee])
    real_db_session.commit()
    _grant(real_db_session, owner, employee)
    real_db_session.commit()

    r = client_real_db.get("/stores", headers=_auth(employee))
    assert r.status_code == 200
    assert r.json()["default_store_owner_user_id"] == str(owner.id)


def test_no_default_store_when_employee_has_own_wb_key(client_real_db, real_db_session):
    owner = _mk_user("bill-owner-9@example.com")
    employee = _mk_user("bill-employee-9@example.com", wb_api_key="own-key")
    real_db_session.add_all([owner, employee])
    real_db_session.commit()
    _grant(real_db_session, owner, employee)
    real_db_session.commit()

    r = client_real_db.get("/stores", headers=_auth(employee))
    assert r.json()["default_store_owner_user_id"] is None


def test_no_default_store_when_employee_has_two_granted_stores(client_real_db, real_db_session):
    owner_a = _mk_user("bill-owner-10a@example.com")
    owner_b = _mk_user("bill-owner-10b@example.com")
    employee = _mk_user("bill-employee-10@example.com", wb_api_key=None)
    real_db_session.add_all([owner_a, owner_b, employee])
    real_db_session.commit()
    _grant(real_db_session, owner_a, employee)
    _grant(real_db_session, owner_b, employee)
    real_db_session.commit()

    r = client_real_db.get("/stores", headers=_auth(employee))
    assert r.json()["default_store_owner_user_id"] is None


def test_employee_with_expired_own_account_can_still_list_stores(client_real_db, real_db_session):
    owner = _mk_user("bill-owner-11@example.com")
    employee = _mk_user("bill-employee-11@example.com", wb_api_key=None)
    real_db_session.add_all([owner, employee])
    real_db_session.commit()
    _set_trial(real_db_session, employee, ends_in_days=-4)
    _grant(real_db_session, owner, employee)
    real_db_session.commit()

    # Без списка магазинов сотрудник не может выбрать кабинет селлера, поэтому /stores не блокируется.
    r = client_real_db.get("/stores", headers=_auth(employee))
    assert r.status_code == 200
    assert r.json()["default_store_owner_user_id"] == str(owner.id)
