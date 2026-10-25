from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.crypto import encrypt_text
from app.core.security import create_access_token
from app.db import get_db
from app.main import app
from app.models.ozon import (
    OzonConnection,
    OzonFinanceSettings,
    OzonOperationalExpense,
    OzonProductCost,
    OzonSyncDay,
    OzonSyncRun,
)
from app.models.store_access_grant import StoreAccessGrant
from app.models.user import User


@pytest.fixture(autouse=True)
def _no_eager_worker(monkeypatch):
    """Saving a connection queues a backfill; API tests must never run the worker eagerly against the database."""
    monkeypatch.setattr("app.routers.marketplace_finance.ozon_sync_run.delay", lambda run_id: None)


def _retire_auto_backfill(session, owner_id: str) -> None:
    """Saving a connection queues the automatic backfill; the stubbed broker never runs it, so retire it to free the owner's slot."""
    session.query(OzonSyncRun).filter(
        OzonSyncRun.user_id == owner_id,
        OzonSyncRun.status.in_(("queued", "running")),
    ).update({"status": "failed"}, synchronize_session=False)
    session.commit()


def _user(email: str, *, tax_rate: Decimal = Decimal("0.06")) -> User:
    return User(id=str(uuid4()), email=email, password_hash="hash", is_active=True, tax_rate=tax_rate)


def _token(user_id: str) -> str:
    return create_access_token({"sub": user_id})


def _install_db_override(real_db_session):
    def _get_db():
        yield real_db_session

    app.dependency_overrides[get_db] = _get_db


def test_connection_is_encrypted_owner_writable_and_viewer_read_only(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("ozon-api-owner@example.com")
    viewer = _user("ozon-api-viewer@example.com")
    real_db_session.add_all([owner, viewer])
    real_db_session.commit()
    real_db_session.add(
        StoreAccessGrant(
            store_owner_user_id=str(owner.id),
            viewer_user_id=str(viewer.id),
            status="active",
            revoked_at=None,
        )
    )
    real_db_session.commit()
    _install_db_override(real_db_session)

    try:
        with TestClient(app) as client:
            owner_headers = {"Authorization": f"Bearer {_token(str(owner.id))}"}
            viewer_headers = {
                "Authorization": f"Bearer {_token(str(viewer.id))}",
                "X-Store-Owner-Id": str(owner.id),
            }

            oversized_dummy_key = "DUMMY-OZON-KEY-MARKER-" + ("x" * 4097)
            rejected_secret = client.put(
                "/dashboard/marketplace/connection",
                headers=owner_headers,
                json={"client_id": "ozon-client-123", "api_key": oversized_dummy_key},
            )
            assert rejected_secret.status_code == 422
            assert "DUMMY-OZON-KEY-MARKER" not in rejected_secret.text

            saved = client.put(
                "/dashboard/marketplace/connection",
                headers=owner_headers,
                json={"client_id": "ozon-client-123", "api_key": "ozon-secret-test-value"},
            )
            assert saved.status_code == 200
            _retire_auto_backfill(real_db_session, str(owner.id))
            assert saved.json()["configured"] is True
            assert saved.json()["client_id"] == "ozon-client-123"
            assert "ozon-secret-test-value" not in saved.text
            stored = real_db_session.query(OzonConnection).filter_by(user_id=str(owner.id)).one()
            assert stored.client_id_enc != "ozon-client-123"
            assert stored.api_key_enc != "ozon-secret-test-value"

            viewer_read = client.get("/dashboard/marketplace/connection", headers=viewer_headers)
            assert viewer_read.status_code == 200
            assert viewer_read.json()["client_id"] == "ozon-client-123"
            assert "api_key" not in viewer_read.json()

            viewer_write = client.put(
                "/dashboard/marketplace/connection",
                headers=viewer_headers,
                json={"client_id": "other-client", "api_key": "must-not-save"},
            )
            assert viewer_write.status_code == 403

            cost_write = client.put(
                "/dashboard/marketplace/products/7001/cost",
                headers=owner_headers,
                json={"cost_price": 0},
            )
            assert cost_write.status_code == 200
            assert cost_write.json()["cost_price"] == 0.0
            assert real_db_session.query(OzonProductCost).filter_by(user_id=str(owner.id), sku="7001").one().cost_price == 0

            unset = client.put(
                "/dashboard/marketplace/products/7001/cost",
                headers=owner_headers,
                json={"cost_price": None},
            )
            assert unset.status_code == 200
            assert unset.json()["cost_price"] is None

            viewer_cost_write = client.put(
                "/dashboard/marketplace/products/7001/cost",
                headers=viewer_headers,
                json={"cost_price": 5},
            )
            assert viewer_cost_write.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_read_endpoints_do_not_make_ozon_provider_calls(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("ozon-no-get-provider@example.com")
    real_db_session.add(owner)
    real_db_session.commit()
    _install_db_override(real_db_session)

    class UnexpectedProviderCall:
        def __init__(self, *args, **kwargs):
            raise AssertionError("GET endpoint made a provider call")

    monkeypatch.setattr("app.services.ozon_client.OzonClient", UnexpectedProviderCall)
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {_token(str(owner.id))}"}
            connection = client.get("/dashboard/marketplace/connection", headers=headers)
            overview = client.get(
                "/dashboard/marketplace/overview",
                headers=headers,
                params={"marketplace": "ozon", "date_from": "2026-10-10", "date_to": "2026-10-10"},
            )
            assert connection.status_code == 200
            assert connection.json()["configured"] is False
            assert overview.status_code == 200
            assert overview.json()["coverage"]["complete"] is False
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_ozon_finance_settings_and_expenses_are_owner_scoped(real_db_session):
    owner = _user("ozon-finance-settings-owner@example.com", tax_rate=Decimal("0.06"))
    viewer = _user("ozon-finance-settings-viewer@example.com", tax_rate=Decimal("0.18"))
    other_owner = _user("ozon-finance-settings-other-owner@example.com", tax_rate=Decimal("0.12"))
    real_db_session.add_all([owner, viewer, other_owner])
    real_db_session.commit()
    real_db_session.add(
        StoreAccessGrant(
            store_owner_user_id=str(owner.id),
            viewer_user_id=str(viewer.id),
            status="active",
            revoked_at=None,
        )
    )
    real_db_session.commit()
    _install_db_override(real_db_session)

    try:
        with TestClient(app) as client:
            owner_headers = {"Authorization": f"Bearer {_token(str(owner.id))}"}
            viewer_headers = {
                "Authorization": f"Bearer {_token(str(viewer.id))}",
                "X-Store-Owner-Id": str(owner.id),
            }
            other_owner_headers = {"Authorization": f"Bearer {_token(str(other_owner.id))}"}

            default = client.get("/dashboard/marketplace/settings", headers=viewer_headers)
            assert default.status_code == 200
            assert default.json() == {
                "tax_rate": 0.06,
                "tax_rate_source": "wb",
                "operation_expenses_confirmed": False,
            }
            other_owner_settings = client.get("/dashboard/marketplace/settings", headers=other_owner_headers)
            assert other_owner_settings.status_code == 200
            assert other_owner_settings.json()["tax_rate"] == 0.12

            saved = client.put(
                "/dashboard/marketplace/settings",
                headers=owner_headers,
                json={"operation_expenses_confirmed": True},
            )
            assert saved.status_code == 200
            assert saved.json() == {
                "tax_rate": 0.06,
                "tax_rate_source": "wb",
                "operation_expenses_confirmed": True,
            }
            setting = real_db_session.query(OzonFinanceSettings).filter_by(user_id=str(owner.id)).one()
            assert setting.operation_expenses_confirmed is True
            assert float(real_db_session.query(User).filter_by(id=str(owner.id)).one().tax_rate) == 0.06

            rejected_tax_override = client.put(
                "/dashboard/marketplace/settings",
                headers=owner_headers,
                json={"tax_rate": 0.18, "operation_expenses_confirmed": True},
            )
            assert rejected_tax_override.status_code == 422
            assert float(real_db_session.query(User).filter_by(id=str(owner.id)).one().tax_rate) == 0.06

            viewer_write = client.put(
                "/dashboard/marketplace/settings",
                headers=viewer_headers,
                json={"operation_expenses_confirmed": True},
            )
            assert viewer_write.status_code == 403

            expense_url = "/dashboard/marketplace/expenses/2026-10-10"
            expense = client.put(expense_url, headers=owner_headers, json={"amount": 12.5})
            assert expense.status_code == 200
            assert expense.json() == {"date": "2026-10-10", "amount": 12.5}
            zeroed = client.put(expense_url, headers=owner_headers, json={"amount": 0})
            assert zeroed.status_code == 200
            rows = real_db_session.query(OzonOperationalExpense).filter_by(user_id=str(owner.id)).all()
            assert len(rows) == 1
            assert rows[0].amount == 0

            negative = client.put(expense_url, headers=owner_headers, json={"amount": -1})
            assert negative.status_code == 422
            viewer_expense = client.put(expense_url, headers=viewer_headers, json={"amount": 5})
            assert viewer_expense.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_sync_is_bounded_and_only_owner_queues_run(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("ozon-sync-owner@example.com")
    viewer = _user("ozon-sync-viewer@example.com")
    real_db_session.add_all([owner, viewer])
    real_db_session.commit()
    real_db_session.add(
        StoreAccessGrant(
            store_owner_user_id=str(owner.id),
            viewer_user_id=str(viewer.id),
            status="active",
            revoked_at=None,
        )
    )
    real_db_session.commit()
    _install_db_override(real_db_session)
    queued: list[str] = []
    monkeypatch.setattr("app.routers.marketplace_finance.ozon_sync_run.delay", lambda run_id: queued.append(run_id))

    try:
        with TestClient(app) as client:
            owner_headers = {"Authorization": f"Bearer {_token(str(owner.id))}"}
            viewer_headers = {
                "Authorization": f"Bearer {_token(str(viewer.id))}",
                "X-Store-Owner-Id": str(owner.id),
            }
            connected = client.put(
                "/dashboard/marketplace/connection",
                headers=owner_headers,
                json={"client_id": "client", "api_key": "key"},
            )
            assert connected.status_code == 200
            # Saving the connection queues only the automatic backfill chunk; the manual requests below are counted separately.
            assert len(queued) == 1
            assert real_db_session.query(OzonSyncRun).filter_by(id=queued[0]).one().trigger == "backfill"
            _retire_auto_backfill(real_db_session, str(owner.id))
            queued.clear()

            too_long = client.post(
                "/dashboard/marketplace/sync",
                headers=owner_headers,
                json={"date_from": "2026-09-01", "date_to": "2026-10-02"},
            )
            assert too_long.status_code == 422
            assert queued == []

            viewer_attempt = client.post(
                "/dashboard/marketplace/sync",
                headers=viewer_headers,
                json={"date_from": "2026-10-01", "date_to": "2026-10-01"},
            )
            assert viewer_attempt.status_code == 403
            assert queued == []

            accepted = client.post(
                "/dashboard/marketplace/sync",
                headers=owner_headers,
                json={"date_from": "2026-10-01", "date_to": "2026-10-31"},
            )
            assert accepted.status_code == 202
            assert accepted.json()["status"] == "queued"
            assert queued == [accepted.json()["run_id"]]
            run = real_db_session.query(OzonSyncRun).filter_by(id=accepted.json()["run_id"]).one()
            assert run.status == "queued"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_changing_client_id_is_rejected_after_ledger_exists(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner = _user("ozon-client-id-stable@example.com")
    real_db_session.add(owner)
    real_db_session.commit()
    _install_db_override(real_db_session)

    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {_token(str(owner.id))}"}
            first = client.put(
                "/dashboard/marketplace/connection",
                headers=headers,
                json={"client_id": "client-1", "api_key": "key-1"},
            )
            assert first.status_code == 200
            _retire_auto_backfill(real_db_session, str(owner.id))
            active_run = OzonSyncRun(
                user_id=str(owner.id),
                date_from=date(2026, 10, 1),
                date_to=date(2026, 10, 1),
                status="queued",
                progress=0,
            )
            real_db_session.add(active_run)
            real_db_session.commit()
            edit_during_sync = client.put(
                "/dashboard/marketplace/connection",
                headers=headers,
                json={"client_id": "client-1", "api_key": "key-rotated"},
            )
            assert edit_during_sync.status_code == 422
            active_run.status = "complete"
            active_run.progress = 1
            real_db_session.add(active_run)
            real_db_session.commit()
            real_db_session.add(
                OzonSyncRun(
                    user_id=str(owner.id),
                    date_from=date(2026, 10, 1),
                    date_to=date(2026, 10, 1),
                    status="complete",
                    progress=1,
                )
            )
            # An empty-but-covered day is still ledger history for this owner/client identity.
            from app.models.ozon import OzonSyncDay

            real_db_session.add(OzonSyncDay(user_id=str(owner.id), day=date(2026, 10, 1), status="empty"))
            real_db_session.commit()
            switched = client.put(
                "/dashboard/marketplace/connection",
                headers=headers,
                json={"client_id": "client-2", "api_key": "key-2"},
            )
            assert switched.status_code == 422
            assert "Client-Id" in switched.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_owner_data_and_costs_are_scoped_to_each_account(real_db_session, monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    owner_a = _user("ozon-isolation-a@example.com")
    owner_b = _user("ozon-isolation-b@example.com")
    day = date(2026, 10, 10)
    owner_a_id = str(owner_a.id)
    owner_b_id = str(owner_b.id)
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    real_db_session.add_all([owner_a, owner_b])
    real_db_session.commit()
    real_db_session.add_all(
        [
            OzonConnection(
                user_id=owner_a_id,
                client_id_enc=encrypt_text("client-a"),
                api_key_enc=encrypt_text("key-a"),
                status="ready",
            ),
            OzonConnection(
                user_id=owner_b_id,
                client_id_enc=encrypt_text("client-b"),
                api_key_enc=encrypt_text("key-b"),
                status="ready",
            ),
            OzonSyncDay(
                user_id=owner_a_id,
                day=day,
                status="complete",
                daily_data={"revenue": "100.00"},
                products_data=[{"sku": "shared-sku", "revenue": "100.00"}],
                warnings=[],
            ),
            OzonSyncDay(
                user_id=owner_b_id,
                day=day,
                status="complete",
                daily_data={"revenue": "900.00"},
                products_data=[{"sku": "shared-sku", "revenue": "900.00"}],
                warnings=[],
            ),
            OzonProductCost(user_id=owner_a_id, sku="shared-sku", cost_price=5),
            OzonProductCost(user_id=owner_b_id, sku="shared-sku", cost_price=9),
        ]
    )
    real_db_session.commit()
    _install_db_override(real_db_session)

    try:
        with TestClient(app) as client:
            headers_a = {"Authorization": f"Bearer {_token(owner_a_id)}"}
            visible_a = client.get(
                "/dashboard/marketplace/overview",
                headers=headers_a,
                params={"marketplace": "ozon", "date_from": day.isoformat(), "date_to": day.isoformat()},
            )
            assert visible_a.status_code == 200
            assert visible_a.json()["daily"][0]["revenue"] == 100.0
            assert visible_a.json()["products"][0]["cost_price"] == 5.0

            updated = client.put(
                "/dashboard/marketplace/products/shared-sku/cost",
                headers=headers_a,
                json={"cost_price": 6},
            )
            assert updated.status_code == 200
            assert real_db_session.query(OzonProductCost).filter_by(
                user_id=owner_a_id, sku="shared-sku"
            ).one().cost_price == 6
            assert real_db_session.query(OzonProductCost).filter_by(
                user_id=owner_b_id, sku="shared-sku"
            ).one().cost_price == 9
    finally:
        app.dependency_overrides.pop(get_db, None)
