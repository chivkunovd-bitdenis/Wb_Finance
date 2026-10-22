"""HTTP-контракт счетов на оплату: реквизиты, предпросмотр, выпуск, PDF, админские отметки об оплате."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.dependencies import get_current_user
from app.main import app
from app.models.user import User

ADMIN_SECRET = "test-admin-secret"
REQUISITES = {
    "name": "ООО «ТЕСТ»",
    "inn": "1234567890",
    "kpp": "123456789",
    "address": "Г МОСКВА, УЛ ТЕСТОВАЯ, Д 1",
}


def _user() -> User:
    return User(
        id=str(uuid4()),
        email=f"invoice-api-{uuid4()}@example.com",
        password_hash="$2b$12$fake",
        wb_api_key="k",
        is_active=True,
    )


@pytest.fixture
def offline_client() -> Iterator[TestClient]:
    """Клиент без базы: сессия подменена MagicMock, проверки срабатывают до обращения к БД."""
    user = _user()

    def _db() -> Iterator[MagicMock]:
        yield MagicMock()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def invoice_client(real_db_session) -> Iterator[TestClient]:
    """Клиент на реальной БД (транзакция откатывается): пройти весь путь счёта по HTTP."""
    user = _user()
    real_db_session.add(user)
    real_db_session.commit()
    real_db_session.refresh(user)

    def _db() -> Iterator:
        yield real_db_session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        real_db_session.close = MagicMock()
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_requisites_reject_inn_with_wrong_length(offline_client: TestClient) -> None:
    r = offline_client.put("/billing/requisites", json={**REQUISITES, "inn": "12345"})
    assert r.status_code == 422


def test_requisites_reject_non_digit_inn(offline_client: TestClient) -> None:
    r = offline_client.put("/billing/requisites", json={**REQUISITES, "inn": "12345abcde"})
    assert r.status_code == 422


def test_requisites_reject_kpp_that_is_not_nine_digits(offline_client: TestClient) -> None:
    r = offline_client.put("/billing/requisites", json={**REQUISITES, "kpp": "12345"})
    assert r.status_code == 422


def test_invoice_needs_at_least_one_marketplace(offline_client: TestClient) -> None:
    r = offline_client.post("/billing/invoices", json={"marketplaces": []})
    assert r.status_code == 422


def test_invoice_rejects_unknown_marketplace(offline_client: TestClient) -> None:
    r = offline_client.post("/billing/invoices", json={"marketplaces": ["yandex"]})
    assert r.status_code == 422


def test_preview_requires_marketplaces_query(offline_client: TestClient) -> None:
    r = offline_client.get("/billing/invoices/preview")
    assert r.status_code == 422


def test_preview_rejects_unknown_marketplace_with_400(offline_client: TestClient) -> None:
    r = offline_client.get("/billing/invoices/preview", params={"marketplaces": "yandex"})
    assert r.status_code == 400


def test_lifetime_account_gets_400_for_preview_and_for_create(offline_client: TestClient) -> None:
    with patch("app.services.billing_invoice_service._is_lifetime", return_value=True):
        preview = offline_client.get("/billing/invoices/preview", params={"marketplaces": "wb,ozon"})
        created = offline_client.post("/billing/invoices", json={"marketplaces": ["wb"]})
    assert preview.status_code == 400
    assert created.status_code == 400
    assert created.json()["detail"] == "Безлимитный доступ: счёт на оплату не требуется"


def test_create_without_requisites_is_400(offline_client: TestClient) -> None:
    with (
        patch("app.services.billing_invoice_service._is_lifetime", return_value=False),
        patch("app.services.billing_invoice_service.get_or_create_subscription", return_value=MagicMock()),
    ):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        def _db() -> Iterator[MagicMock]:
            yield db

        app.dependency_overrides[get_db] = _db
        r = offline_client.post("/billing/invoices", json={"marketplaces": ["wb"]})
    assert r.status_code == 400
    assert "реквизиты" in r.json()["detail"]


def test_unknown_invoice_pdf_is_404_without_touching_db(offline_client: TestClient) -> None:
    r = offline_client.get("/billing/invoices/not-a-uuid/pdf")
    assert r.status_code == 404


def test_admin_endpoints_require_secret(offline_client: TestClient) -> None:
    with patch("app.routers.billing.ADMIN_SECRET", ADMIN_SECRET):
        listing = offline_client.get("/billing/admin/invoices")
        wrong = offline_client.get("/billing/admin/invoices", headers={"x-admin-secret": "nope"})
        paid = offline_client.post("/billing/admin/invoices/1/mark-paid")
    assert listing.status_code == 403
    assert wrong.status_code == 403
    assert paid.status_code == 403


def test_admin_list_rejects_unknown_status_with_secret(offline_client: TestClient) -> None:
    with patch("app.routers.billing.ADMIN_SECRET", ADMIN_SECRET):
        r = offline_client.get(
            "/billing/admin/invoices",
            params={"status": "lost"},
            headers={"x-admin-secret": ADMIN_SECRET},
        )
    assert r.status_code == 400


def test_full_invoice_flow_over_http(invoice_client: TestClient) -> None:
    client = invoice_client

    saved = client.put("/billing/requisites", json=REQUISITES)
    assert saved.status_code == 200
    assert client.get("/billing/requisites").json() == REQUISITES

    preview = client.get("/billing/invoices/preview", params={"marketplaces": "wb,ozon"})
    assert preview.status_code == 200
    assert preview.json()["amount"] == 2980.0
    assert preview.json()["marketplaces"] == ["wb", "ozon"]

    issued = client.post("/billing/invoices", json={"marketplaces": ["wb", "ozon"]})
    assert issued.status_code == 200
    invoice = issued.json()
    assert invoice["status"] == "issued"
    assert invoice["number_display"] == f"ФП-{invoice['number']}"
    assert invoice["amount"] == 2980.0
    assert invoice["buyer"]["inn"] == "1234567890"
    assert invoice["period_start"] == preview.json()["period_start"]
    assert invoice["period_end"] == preview.json()["period_end"]

    again = client.post("/billing/invoices", json={"marketplaces": ["ozon", "wb"]})
    assert again.json()["id"] == invoice["id"]

    listed = client.get("/billing/invoices").json()
    assert [row["id"] for row in listed] == [invoice["id"]]

    pdf = client.get(f"/billing/invoices/{invoice['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    assert "filename*=UTF-8''" in pdf.headers["content-disposition"]

    with patch("app.routers.billing.ADMIN_SECRET", ADMIN_SECRET):
        headers = {"x-admin-secret": ADMIN_SECRET}
        pending = client.get("/billing/admin/invoices", params={"status": "issued"}, headers=headers)
        assert any(row["id"] == invoice["id"] for row in pending.json())

        marked = client.post(f"/billing/admin/invoices/{invoice['number']}/mark-paid", headers=headers)
        assert marked.status_code == 200
        assert marked.json()["subscription_status"] == "active"
        assert marked.json()["is_access_blocked"] is False

        repeated = client.post(f"/billing/admin/invoices/{invoice['number']}/mark-paid", headers=headers)
        assert repeated.status_code == 409

        paid = client.get("/billing/admin/invoices", params={"status": "paid"}, headers=headers).json()
        assert any(row["id"] == invoice["id"] and row["status"] == "paid" for row in paid)

    status = client.get("/billing/status").json()
    assert status["subscription_status"] == "active"
    assert status["is_access_blocked"] is False
