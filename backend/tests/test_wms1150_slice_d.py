"""
WMS-1150 срез Д (R32–R36): контрактные тесты до правки оцифровки.

Проверки C39–C41 из постановки. Часть тестов должна падать на текущем коде:
- демо не стартует от Ozon (только WB-ключ);
- inactive без дат проходит require_access (402 не выставляется).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db import get_db
from app.main import app
from app.models.license import License
from app.models.ozon import OzonConnection, OzonSyncRun
from app.models.subscription import Subscription
from app.models.user import User
from app.services.billing_service import require_access, start_trial_if_needed


@pytest.fixture(autouse=True)
def _no_ozon_backfill_worker(monkeypatch):
    monkeypatch.setattr("app.routers.marketplace_finance.ozon_sync_run.delay", lambda run_id: None)


def _retire_auto_backfill(session, owner_id: str) -> None:
    session.query(OzonSyncRun).filter(
        OzonSyncRun.user_id == owner_id,
        OzonSyncRun.status.in_(("queued", "running")),
    ).update({"status": "failed"}, synchronize_session=False)
    session.commit()


def _install_db(real_db_session):
    def _get_db():
        yield real_db_session

    app.dependency_overrides[get_db] = _get_db


@pytest.fixture
def ozon_finance_env(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("APP_ENCRYPTION_KEY", key)
    return key


@pytest.fixture
def api_client(real_db_session):
    _install_db(real_db_session)
    real_db_session.close = MagicMock()
    try:
        with TestClient(app) as client:
            yield client, real_db_session
    finally:
        app.dependency_overrides.pop(get_db, None)


def _mk_user(*, email: str, wb_api_key: str | None = None) -> User:
    return User(
        id=str(uuid4()),
        email=email,
        password_hash="$2b$12$fake",
        wb_api_key=wb_api_key,
        is_active=True,
    )


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'sub': user_id})}"}


def _ensure_inactive_subscription(session, user_id: str) -> Subscription:
    sub = session.query(Subscription).filter(Subscription.user_id == user_id).first()
    if sub is None:
        sub = Subscription(user_id=user_id, status="inactive", auto_renew=True, provider="yookassa")
        session.add(sub)
        session.commit()
        session.refresh(sub)
    else:
        sub.status = "inactive"
        sub.trial_started_at = None
        sub.trial_ends_at = None
        sub.current_period_end = None
        session.commit()
    return sub


# --- C39 / R33: доступ без подписки закрыт ---


@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("GET", "/dashboard/pnl", {}),
        ("GET", "/dashboard/state", {}),
        (
            "GET",
            "/dashboard/marketplace/overview",
            {"params": {"marketplace": "ozon", "date_from": "2026-01-01", "date_to": "2026-01-07"}},
        ),
    ],
)
def test_c39_inactive_without_keys_gets_402_on_protected_paths(
    api_client, method: str, path: str, kwargs: dict
) -> None:
    """C39: inactive без WB/Ozon и без дат подписки — 402 на защищённых путях."""
    client, session = api_client
    user = _mk_user(email=f"c39-{uuid4()}@example.com", wb_api_key=None)
    session.add(user)
    session.commit()
    _ensure_inactive_subscription(session, str(user.id))

    headers = _auth_headers(str(user.id))
    if method == "GET":
        response = client.get(path, headers=headers, **kwargs)
    else:
        response = client.post(path, headers=headers, json={})

    assert response.status_code != 422, response.text
    assert response.status_code == 402, (
        "ожидали блокировку доступа (402) для inactive без ключей; "
        f"получили {response.status_code}"
    )


def test_c39_sync_recent_blocked_for_inactive_without_subscription(api_client) -> None:
    """C39: POST /sync/recent — 402 при inactive (после R33; не 400 из‑за WB)."""
    client, session = api_client
    user = _mk_user(email=f"c39-sync-{uuid4()}@example.com", wb_api_key=None)
    session.add(user)
    session.commit()
    _ensure_inactive_subscription(session, str(user.id))
    headers = _auth_headers(str(user.id))
    response = client.post("/sync/recent", headers=headers)
    assert response.status_code == 402, response.text


def test_c39_require_access_blocks_inactive_without_subscription_dates() -> None:
    """R33 на уровне сервиса: inactive без дат не должен проходить require_access."""
    db = MagicMock()
    user = User(
        id="u-inactive",
        email="inactive-c39@example.com",
        password_hash="h",
        wb_api_key=None,
        is_active=True,
    )
    inactive_sub = Subscription(
        user_id="u-inactive",
        status="inactive",
        trial_started_at=None,
        trial_ends_at=None,
        current_period_end=None,
    )
    db.query.return_value.filter.return_value.first.side_effect = [
        None,  # _is_lifetime → no license
        inactive_sub,  # get_or_create_subscription / require_access read
    ]

    with pytest.raises(PermissionError):
        require_access(db, user)


def test_c39_exempt_billing_and_health_still_allowed_for_inactive(api_client) -> None:
    """C39: /billing/* и /health остаются доступны без подписки."""
    client, session = api_client
    user = _mk_user(email=f"c39-exempt-{uuid4()}@example.com", wb_api_key=None)
    session.add(user)
    session.commit()
    _ensure_inactive_subscription(session, str(user.id))
    headers = _auth_headers(str(user.id))

    health = client.get("/health")
    assert health.status_code == 200

    billing = client.get("/billing/status", headers=headers)
    assert billing.status_code == 200
    body = billing.json()
    assert body["subscription_status"] == "inactive"
    assert body["is_access_blocked"] is True


def test_c39_marketplace_connection_exempt_for_ozon_only_setup(
    api_client, ozon_finance_env
) -> None:
    """C39: GET/PUT /dashboard/marketplace/connection доступны без активной подписки (для Ozon-only)."""
    client, session = api_client
    user = _mk_user(email=f"c39-conn-{uuid4()}@example.com", wb_api_key=None)
    session.add(user)
    session.commit()
    _ensure_inactive_subscription(session, str(user.id))
    headers = _auth_headers(str(user.id))

    read = client.get("/dashboard/marketplace/connection", headers=headers)
    assert read.status_code == 200

    saved = client.put(
        "/dashboard/marketplace/connection",
        headers=headers,
        json={"client_id": "ozon-client-c39", "api_key": "ozon-secret-c39-value"},
    )
    assert saved.status_code != 422, saved.text
    assert saved.status_code == 200, saved.text
    _retire_auto_backfill(session, str(user.id))


# --- C40 / R32: демо от Ozon ---


def test_c40_start_trial_if_needed_with_ozon_connection_and_no_wb_key(
    real_db_session, ozon_finance_env
) -> None:
    """C40: при подключённом Ozon и без WB-ключа start_trial_if_needed запускает trial."""
    user = _mk_user(email=f"c40-unit-{uuid4()}@example.com", wb_api_key=None)
    real_db_session.add(user)
    real_db_session.commit()
    _ensure_inactive_subscription(real_db_session, str(user.id))
    real_db_session.add(
        OzonConnection(
            user_id=str(user.id),
            client_id_enc="enc-client",
            api_key_enc="enc-key",
        )
    )
    real_db_session.commit()

    sub = start_trial_if_needed(real_db_session, user)
    real_db_session.commit()
    real_db_session.refresh(sub)

    assert sub.status == "trial"
    assert sub.trial_started_at is not None
    assert sub.trial_ends_at is not None
    lic = real_db_session.query(License).filter(License.user_id == str(user.id)).one()
    assert lic.status == "trial"


def test_c40_ozon_connection_put_starts_trial_and_unlocks_dashboard(
    api_client, ozon_finance_env
) -> None:
    """C40: PUT connection для Ozon-only → trial 5 дней, данные доступны."""
    client, session = api_client
    user = _mk_user(email=f"c40-api-{uuid4()}@example.com", wb_api_key=None)
    session.add(user)
    session.commit()
    _ensure_inactive_subscription(session, str(user.id))
    headers = _auth_headers(str(user.id))

    saved = client.put(
        "/dashboard/marketplace/connection",
        headers=headers,
        json={"client_id": "ozon-client-c40", "api_key": "ozon-secret-c40-value"},
    )
    assert saved.status_code == 200, saved.text
    _retire_auto_backfill(session, str(user.id))

    sub = session.query(Subscription).filter(Subscription.user_id == str(user.id)).one()
    assert sub.status == "trial", "после сохранения Ozon ожидаем trial (R32)"
    assert sub.trial_started_at is not None
    assert sub.trial_ends_at is not None

    billing = client.get("/billing/status", headers=headers).json()
    assert billing["subscription_status"] == "trial"
    assert billing["is_access_blocked"] is False

    dashboard = client.get("/dashboard/state", headers=headers)
    assert dashboard.status_code == 200


def test_c40_second_ozon_save_does_not_extend_trial(
    api_client, ozon_finance_env
) -> None:
    """C40: повторное сохранение подключения не продлевает trial."""
    client, session = api_client
    user = _mk_user(email=f"c40-repeat-{uuid4()}@example.com", wb_api_key=None)
    session.add(user)
    session.commit()
    _ensure_inactive_subscription(session, str(user.id))
    headers = _auth_headers(str(user.id))

    first = client.put(
        "/dashboard/marketplace/connection",
        headers=headers,
        json={"client_id": "ozon-a", "api_key": "ozon-secret-first"},
    )
    assert first.status_code == 200
    _retire_auto_backfill(session, str(user.id))
    sub = session.query(Subscription).filter(Subscription.user_id == str(user.id)).one()
    assert sub.status == "trial"
    first_end = sub.trial_ends_at

    second = client.put(
        "/dashboard/marketplace/connection",
        headers=headers,
        json={"client_id": "ozon-b", "api_key": "ozon-secret-second"},
    )
    assert second.status_code == 200
    _retire_auto_backfill(session, str(user.id))
    session.refresh(sub)
    assert sub.trial_ends_at == first_end


def test_c40_lifetime_not_overwritten_when_saving_ozon(
    api_client, ozon_finance_env
) -> None:
    """C40: lifetime остаётся lifetime после подключения Ozon."""
    client, session = api_client
    user = _mk_user(email=f"c40-life-{uuid4()}@example.com", wb_api_key=None)
    session.add(user)
    session.commit()
    session.add(License(user_id=str(user.id), status="lifetime", valid_until=None, source="manual"))
    _ensure_inactive_subscription(session, str(user.id))
    headers = _auth_headers(str(user.id))

    saved = client.put(
        "/dashboard/marketplace/connection",
        headers=headers,
        json={"client_id": "ozon-life", "api_key": "ozon-secret-life"},
    )
    assert saved.status_code == 200
    _retire_auto_backfill(session, str(user.id))

    lic = session.query(License).filter(License.user_id == str(user.id)).one()
    assert lic.status == "lifetime"
    billing = client.get("/billing/status", headers=headers).json()
    assert billing["subscription_status"] == "lifetime"


def test_c40_expired_does_not_get_new_demo_from_ozon_connection(
    api_client, ozon_finance_env
) -> None:
    """C40: expired не получает новое демо при сохранении Ozon."""
    client, session = api_client
    user = _mk_user(email=f"c40-exp-{uuid4()}@example.com", wb_api_key=None)
    session.add(user)
    session.commit()
    now = datetime.now(UTC)
    session.add(
        Subscription(
            user_id=str(user.id),
            status="expired",
            trial_started_at=now - timedelta(days=20),
            trial_ends_at=now - timedelta(days=15),
            current_period_end=None,
            auto_renew=True,
            provider="yookassa",
        )
    )
    session.commit()
    headers = _auth_headers(str(user.id))

    saved = client.put(
        "/dashboard/marketplace/connection",
        headers=headers,
        json={"client_id": "ozon-exp", "api_key": "ozon-secret-exp"},
    )
    assert saved.status_code == 200
    _retire_auto_backfill(session, str(user.id))

    sub = session.query(Subscription).filter(Subscription.user_id == str(user.id)).one()
    assert sub.status == "expired"
    assert sub.trial_ends_at == now - timedelta(days=15)


# --- C41 / R34: регрессия пользователей с WB-ключом и подписками ---


def test_c41_wb_key_lazy_trial_still_starts_on_protected_request(api_client) -> None:
    """C41: WB-ключ по-прежнему лениво запускает trial."""
    client, session = api_client
    user = _mk_user(email=f"c41-wb-{uuid4()}@example.com", wb_api_key="wb-key-for-trial")
    session.add(user)
    session.commit()
    _ensure_inactive_subscription(session, str(user.id))
    headers = _auth_headers(str(user.id))

    response = client.get("/dashboard/state", headers=headers)
    assert response.status_code == 200

    sub = session.query(Subscription).filter(Subscription.user_id == str(user.id)).one()
    assert sub.status == "trial"
    assert sub.trial_started_at is not None


def test_c41_active_trial_still_allows_dashboard(api_client) -> None:
    client, session = api_client
    user = _mk_user(email=f"c41-trial-{uuid4()}@example.com", wb_api_key="wb-key")
    session.add(user)
    session.commit()
    now = datetime.now(UTC)
    session.add(
        Subscription(
            user_id=str(user.id),
            status="trial",
            trial_started_at=now - timedelta(days=1),
            trial_ends_at=now + timedelta(days=4),
            auto_renew=True,
            provider="yookassa",
        )
    )
    session.commit()
    headers = _auth_headers(str(user.id))
    assert client.get("/dashboard/state", headers=headers).status_code == 200


def test_c41_lifetime_still_allows_dashboard_without_trial_dates(api_client) -> None:
    client, session = api_client
    user = _mk_user(email=f"c41-life-{uuid4()}@example.com", wb_api_key="wb-key")
    session.add(user)
    session.commit()
    session.add(License(user_id=str(user.id), status="lifetime", valid_until=None, source="manual"))
    _ensure_inactive_subscription(session, str(user.id))
    headers = _auth_headers(str(user.id))
    assert client.get("/dashboard/state", headers=headers).status_code == 200
    assert client.get("/billing/status", headers=headers).json()["subscription_status"] == "lifetime"


def test_c41_active_paid_period_still_allows_dashboard(api_client) -> None:
    client, session = api_client
    user = _mk_user(email=f"c41-active-{uuid4()}@example.com", wb_api_key="wb-key")
    session.add(user)
    session.commit()
    now = datetime.now(UTC)
    session.add(
        Subscription(
            user_id=str(user.id),
            status="active",
            current_period_end=now + timedelta(days=25),
            auto_renew=True,
            provider="yookassa",
        )
    )
    session.commit()
    headers = _auth_headers(str(user.id))
    assert client.get("/dashboard/state", headers=headers).status_code == 200


def test_c41_expired_subscription_still_returns_402(api_client) -> None:
    client, session = api_client
    user = _mk_user(email=f"c41-expired-{uuid4()}@example.com", wb_api_key="wb-key")
    session.add(user)
    session.commit()
    now = datetime.now(UTC)
    session.add(
        Subscription(
            user_id=str(user.id),
            status="expired",
            trial_started_at=now - timedelta(days=30),
            trial_ends_at=now - timedelta(days=1),
            auto_renew=True,
            provider="yookassa",
        )
    )
    session.commit()
    headers = _auth_headers(str(user.id))
    blocked = client.get("/dashboard/state", headers=headers)
    assert blocked.status_code == 402


def test_c41_register_with_wb_key_still_starts_trial(api_client) -> None:
    """C41: регистрация с WB-ключом без промокода — trial как раньше."""
    from unittest.mock import patch

    client, session = api_client
    email = f"c41-reg-{uuid4()}@example.com"
    with patch("app.core.security.pwd_context.hash", return_value="$2b$12$faketesthash"):
        registered = client.post(
            "/auth/register",
            json={"email": email, "password": "secret12345", "wb_api_key": "wb-register-key"},
        )
    assert registered.status_code == 200, registered.text
    user = session.query(User).filter(User.email == email).one()
    sub = session.query(Subscription).filter(Subscription.user_id == str(user.id)).one()
    assert sub.status == "trial"
    assert sub.trial_started_at is not None
