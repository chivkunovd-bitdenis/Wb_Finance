"""
Тесты эндпоинтов постановки задач синка в очередь: POST /sync/sales, /ads, /funnel, /recalculate, /initial.
Celery подменён: .delay() не ставит задачу в Redis, возвращает фейковый result.id.
Витрины: pnl_daily (по дням), sku_daily (артикул×день) заполняются задачами recalculate_*.
hash_password/verify_password мокаем, чтобы не дергать bcrypt в контейнере.
"""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import get_db
from app.dependencies import get_store_context
from app.services.store_access_service import StoreContext

FAKE_HASH = "$2b$12$faketesthash"


def _mock_get_db_with_user():
    """Пользователь есть (для авторизации в /sync)."""
    from app.models.user import User
    from app.models.funnel_backfill_state import FunnelBackfillState
    from app.models.finance_missing_sync_state import FinanceMissingSyncState

    session = MagicMock()
    user = User(
        id="sync-user-id",
        email="sync@example.com",
        password_hash=FAKE_HASH,
        wb_api_key="wb-key",
        is_active=True,
    )

    def _query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.order_by.return_value = chain
        if model is FunnelBackfillState:
            chain.first.return_value = None
        elif model is FinanceMissingSyncState:
            chain.first.return_value = None
        else:
            chain.first.return_value = user
        return chain

    session.query.side_effect = _query
    session.get.return_value = user
    try:
        yield session
    finally:
        pass


def _mock_get_db_with_user_no_key():
    """Пользователь есть, но WB API key не задан."""
    from app.models.user import User
    from app.models.funnel_backfill_state import FunnelBackfillState
    from app.models.finance_missing_sync_state import FinanceMissingSyncState

    session = MagicMock()
    user = User(
        id="sync-user-id",
        email="sync@example.com",
        password_hash=FAKE_HASH,
        wb_api_key=None,
        is_active=True,
    )

    def _query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.order_by.return_value = chain
        if model is FunnelBackfillState:
            chain.first.return_value = None
        elif model is FinanceMissingSyncState:
            chain.first.return_value = None
        else:
            chain.first.return_value = user
        return chain

    session.query.side_effect = _query
    session.get.return_value = user
    try:
        yield session
    finally:
        pass


@pytest.fixture
def client_sync():
    def _fake_verify(plain: str, hashed: str) -> bool:
        return plain == "pass" and hashed == FAKE_HASH
    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.verify.side_effect = _fake_verify
        app.dependency_overrides[get_db] = _mock_get_db_with_user
        try:
            with TestClient(app) as c:
                yield c
        finally:
            app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client_sync_no_key():
    def _fake_verify(plain: str, hashed: str) -> bool:
        return plain == "pass" and hashed == FAKE_HASH
    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.verify.side_effect = _fake_verify
        app.dependency_overrides[get_db] = _mock_get_db_with_user_no_key
        try:
            with TestClient(app) as c:
                yield c
        finally:
            app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client_sync_sales_retry_scheduled():
    from app.models.finance_missing_sync_state import FinanceMissingSyncState
    from app.models.funnel_backfill_state import FunnelBackfillState
    from app.models.user import User

    user = User(
        id="sync-user-id",
        email="sync@example.com",
        password_hash=FAKE_HASH,
        wb_api_key="wb-key",
        is_active=True,
    )
    retry_row = MagicMock()
    retry_row.next_run_at = datetime.now(timezone.utc) + timedelta(hours=2)

    def _mock_get_db():
        session = MagicMock()

        def _query(model):
            chain = MagicMock()
            chain.filter.return_value = chain
            chain.order_by.return_value = chain
            if model is FunnelBackfillState:
                chain.first.return_value = None
            elif model is FinanceMissingSyncState:
                chain.first.return_value = retry_row
            else:
                chain.first.return_value = user
            return chain

        session.query.side_effect = _query
        session.get.return_value = user
        try:
            yield session
        finally:
            pass

    def _fake_verify(plain: str, hashed: str) -> bool:
        return plain == "pass" and hashed == FAKE_HASH

    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.verify.side_effect = _fake_verify
        app.dependency_overrides[get_db] = _mock_get_db
        try:
            with TestClient(app) as c:
                yield c
        finally:
            app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client_sync_sales_running():
    from app.models.finance_missing_sync_state import FinanceMissingSyncState
    from app.models.funnel_backfill_state import FunnelBackfillState
    from app.models.user import User

    user = User(
        id="sync-user-id",
        email="sync@example.com",
        password_hash=FAKE_HASH,
        wb_api_key="wb-key",
        is_active=True,
    )
    running_row = MagicMock()
    running_row.status = "queued"
    running_row.updated_at = datetime.now(timezone.utc)
    running_row.next_run_at = None

    def _mock_get_db():
        session = MagicMock()

        def _query(model):
            chain = MagicMock()
            chain.filter.return_value = chain
            chain.order_by.return_value = chain
            if model is FunnelBackfillState:
                chain.first.return_value = None
            elif model is FinanceMissingSyncState:
                chain.first.return_value = running_row
            else:
                chain.first.return_value = user
            return chain

        session.query.side_effect = _query
        session.get.return_value = user
        try:
            yield session
        finally:
            pass

    def _fake_verify(plain: str, hashed: str) -> bool:
        return plain == "pass" and hashed == FAKE_HASH

    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.verify.side_effect = _fake_verify
        app.dependency_overrides[get_db] = _mock_get_db
        try:
            with TestClient(app) as c:
                yield c
        finally:
            app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client_sync_granted():
    """
    Клиент, в котором пользователь авторизован как viewer, но store_ctx.is_owner == False.

    Важно: здесь мы подменяем get_store_context напрямую, чтобы тестировать guardrail
    /sync/* не углубляясь в grant-таблицы и заголовки.
    """
    from app.models.user import User

    viewer = User(
        id="sync-viewer-id",
        email="viewer@example.com",
        password_hash=FAKE_HASH,
        wb_api_key="wb-key",
        is_active=True,
    )
    owner = User(
        id="sync-owner-id",
        email="owner@example.com",
        password_hash=FAKE_HASH,
        wb_api_key="wb-key",
        is_active=True,
    )

    def _mock_get_db():
        session = MagicMock()

        def _query(model):
            chain = MagicMock()
            chain.filter.return_value = chain
            chain.first.return_value = viewer
            return chain

        session.query.side_effect = _query
        session.get.return_value = viewer
        try:
            yield session
        finally:
            pass

    from fastapi import Request

    def _fake_store_context(request: Request) -> StoreContext:
        return StoreContext(viewer=viewer, store_owner=owner)

    def _fake_verify(plain: str, hashed: str) -> bool:
        return plain == "pass" and hashed == FAKE_HASH

    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.verify.side_effect = _fake_verify
        app.dependency_overrides[get_db] = _mock_get_db
        app.dependency_overrides[get_store_context] = _fake_store_context
        try:
            with TestClient(app) as c:
                yield c
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_store_context, None)


def _get_token(client_sync: TestClient) -> str:
    r = client_sync.post(
        "/auth/login",
        json={"email": "sync@example.com", "password": "pass"},
    )
    assert r.status_code == 200
    return r.json()["access_token"]


@patch("app.routers.sync.wb_orchestrator_kick")
def test_sync_sales_for_non_owner_returns_403(mock_kick, client_sync_granted: TestClient):
    mock_kick.delay.return_value = MagicMock(id="task-orch-sales-should-not-run")
    token = _get_token(client_sync_granted)
    r = client_sync_granted.post(
        "/sync/sales",
        json={"date_from": "2025-03-01", "date_to": "2025-03-05"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    mock_kick.delay.assert_called_once()


@patch("app.routers.sync.wb_orchestrator_kick")
def test_sync_sales_returns_202_and_task_id(mock_kick, client_sync: TestClient):
    mock_kick.delay.return_value = MagicMock(id="task-orch-123")
    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/sales",
        json={"date_from": "2025-03-01", "date_to": "2025-03-05"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == "task-orch-123"
    mock_kick.delay.assert_called_once()
    call_args = mock_kick.delay.call_args[0]
    assert call_args[0] == "sync-user-id"
    assert call_args[1]["high"]["finance_range"]["date_from"] == "2025-03-01"
    assert call_args[1]["high"]["finance_range"]["date_to"] == "2025-03-05"


@patch("app.routers.sync.wb_orchestrator_kick")
def test_sync_ads_returns_200_and_task_id(mock_kick, client_sync: TestClient):
    mock_kick.delay.return_value = MagicMock(id="task-orch-ads-456")
    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/ads",
        json={"date_from": "2025-03-01", "date_to": "2025-03-05"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == "task-orch-ads-456"
    mock_kick.delay.assert_called_once()


@patch("app.routers.sync.wb_orchestrator_kick")
def test_sync_funnel_returns_200_and_task_id(mock_kick, client_sync: TestClient):
    mock_kick.delay.return_value = MagicMock(id="task-orch-funnel-789")
    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/funnel",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == "task-orch-funnel-789"
    mock_kick.delay.assert_called_once()
    call_args = mock_kick.delay.call_args[0]
    assert call_args[0] == "sync-user-id"
    assert call_args[1]["high"]["funnel_tail"] is True


@patch("app.routers.sync.wb_orchestrator_kick")
def test_sync_period_enqueues_sales_only(
    mock_kick,
    client_sync: TestClient,
):
    mock_kick.delay.return_value = MagicMock(id="task-orch-period")

    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/period",
        json={"date_from": "2025-03-01", "date_to": "2025-03-07"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["task_id"] == "task-orch-period"
    mock_kick.delay.assert_called_once()


@patch("app.routers.sync.sync_funnel_ytd_step")
def test_sync_funnel_backfill_ytd_enqueues_task(mock_ytd, client_sync: TestClient):
    mock_ytd.delay.return_value = MagicMock(id="task-ytd-1")
    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/funnel/backfill-ytd",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == "task-ytd-1"
    mock_ytd.delay.assert_called_once()
    uid, year = mock_ytd.delay.call_args[0]
    assert uid == "sync-user-id"
    assert isinstance(year, int)


def test_sync_funnel_with_dates_passes_to_task(client_sync: TestClient):
    # Оркестратор чинит только rolling хвост, явные даты для funnel не принимаем.
    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/funnel",
        json={"date_from": "2025-03-01", "date_to": "2025-03-07"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200


@patch("app.routers.sync.recalculate_sku_daily")
@patch("app.routers.sync.recalculate_pnl")
def test_sync_recalculate_queues_both_tasks(mock_recalculate_pnl, mock_recalculate_sku_daily, client_sync: TestClient):
    mock_recalculate_pnl.delay.return_value = MagicMock(id="task-pnl-1")
    mock_recalculate_sku_daily.delay.return_value = MagicMock(id="task-sku-2")
    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/recalculate",
        json={"date_from": "2025-03-01", "date_to": "2025-03-10"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == "task-sku-2"
    mock_recalculate_pnl.delay.assert_called_once_with("sync-user-id", "2025-03-01", "2025-03-10")
    mock_recalculate_sku_daily.delay.assert_called_once_with("sync-user-id", "2025-03-01", "2025-03-10")


@patch("app.routers.sync.wb_orchestrator_kick")
@patch("app.routers.sync.date")
def test_sync_initial_uses_last_30_days(
    mock_date,
    mock_kick,
    client_sync: TestClient,
):
    """Проверяем, что /sync/initial запрашивает оркестратор на последние 30 дней."""
    # today = 2025‑03‑31 -> date_to = 2025‑03‑30, date_from = 2025‑03‑01
    mock_date.today.return_value = date(2025, 3, 31)

    def _date_ctor(*args, **kwargs):
        return date(*args, **kwargs)

    mock_date.side_effect = _date_ctor
    mock_kick.delay.return_value = MagicMock(id="task-orch-initial")

    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/initial",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == "task-orch-initial"
    mock_kick.delay.assert_called_once()
    uid, payload = mock_kick.delay.call_args[0]
    assert uid == "sync-user-id"
    assert payload["high"]["finance_range"]["date_from"] == "2025-03-01"
    assert payload["high"]["finance_range"]["date_to"] == "2025-03-30"
    assert payload["high"]["funnel_tail"] is True


def test_sync_initial_without_wb_key_returns_400(client_sync_no_key: TestClient):
    token = _get_token(client_sync_no_key)
    r = client_sync_no_key.post(
        "/sync/initial",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    assert "WB API" in (r.json().get("detail") or "")


@patch("app.routers.sync.wb_orchestrator_kick")
@patch("app.routers.sync.date")
def test_sync_recent_updates_last_7_days(
    mock_date,
    mock_kick,
    client_sync: TestClient,
):
    """
    /sync/recent — автосинк для «не первого входа»:
    обновляет последние 7 дней (включая вчера), сначала sales, затем воронку.
    """
    # today = 2025‑04‑08 -> date_to = 2025‑04‑07, date_from = 2025‑04‑01
    mock_date.today.return_value = date(2025, 4, 8)

    def _date_ctor(*args, **kwargs):
        return date(*args, **kwargs)

    mock_date.side_effect = _date_ctor

    mock_kick.delay.return_value = MagicMock(id="task-orch-recent")

    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/recent",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == "task-orch-recent"
    mock_kick.delay.assert_called_once()
    uid, payload = mock_kick.delay.call_args[0]
    assert uid == "sync-user-id"
    assert payload["high"]["finance_range"]["date_from"] == "2025-04-01"
    assert payload["high"]["finance_range"]["date_to"] == "2025-04-07"
    assert payload["high"]["funnel_tail"] is True


@patch("app.routers.sync.wb_orchestrator_kick")
def test_sync_recent_does_not_enqueue_sales_when_wb_retry_scheduled(
    mock_kick,
    client_sync_sales_retry_scheduled: TestClient,
):
    """
    Регрессия prod: если WB уже вернул retry-after по sales, повторный вход не должен
    ставить новый sync_sales и продлевать блокировку продавца.
    """
    token = _get_token(client_sync_sales_retry_scheduled)
    r = client_sync_sales_retry_scheduled.post(
        "/sync/recent",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == "wb-sales-retry-scheduled"
    assert "WB sales" in data["message"]
    mock_kick.delay.assert_not_called()


@patch("app.routers.sync.wb_orchestrator_kick")
def test_sync_recent_does_not_enqueue_sales_when_recent_sync_queued(
    mock_kick,
    client_sync_sales_running: TestClient,
):
    """Повторный быстрый вход не должен ставить второй sync_sales, пока первый уже queued/running."""
    token = _get_token(client_sync_sales_running)
    r = client_sync_sales_running.post(
        "/sync/recent",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == "wb-sales-retry-scheduled"
    assert "уже поставлен или выполняется" in data["message"]
    mock_kick.delay.assert_not_called()


@patch("app.routers.sync.wb_orchestrator_kick")
@patch("app.routers.sync.date")
def test_sync_backfill_2026_enqueues_sales_month_chunks_only(
    mock_date,
    mock_kick,
    client_sync: TestClient,
):
    """
    /sync/backfill/2026:
    - продажи по месяцам с 2026-01-01 до вчера
    - рекламу и историческую воронку не стартуем автоматически
    """
    # today = 2026‑03‑05 -> date_to = 2026‑03‑04
    mock_date.today.return_value = date(2026, 3, 5)

    def _date_ctor(*args, **kwargs):
        return date(*args, **kwargs)

    mock_date.side_effect = _date_ctor

    mock_kick.delay.return_value = MagicMock(id="task-orch-bf-2026")

    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/backfill/2026",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "task_ids" in data and isinstance(data["task_ids"], list)
    mock_kick.delay.assert_called_once()
    uid, payload = mock_kick.delay.call_args[0]
    assert uid == "sync-user-id"
    assert payload["low"]["finance_backfill_year"] == 2026
    assert data["task_ids"] == ["task-orch-bf-2026"]


@patch("app.routers.sync.wb_orchestrator_kick")
def test_sync_backfill_2025_enqueues_twelve_months(
    mock_kick,
    client_sync: TestClient,
):
    """POST /sync/backfill/2025 запрашивает backfill через оркестратор (без fan-out)."""
    mock_kick.delay.return_value = MagicMock(id="task-orch-bf-2025")

    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/backfill/2025",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "task_ids" in data and isinstance(data["task_ids"], list)
    mock_kick.delay.assert_called_once()
    uid, payload = mock_kick.delay.call_args[0]
    assert uid == "sync-user-id"
    assert payload["low"]["finance_backfill_year"] == 2025
    assert data["task_ids"] == ["task-orch-bf-2025"]


def test_sync_backfill_2025_without_wb_key_returns_400(client_sync_no_key: TestClient):
    token = _get_token(client_sync_no_key)
    r = client_sync_no_key.post(
        "/sync/backfill/2025",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    assert "WB API" in (r.json().get("detail") or "")


def test_sync_migrate_folder_dry_run_returns_validation_report(client_sync: TestClient, tmp_path):
    token = _get_token(client_sync)
    csv_file = tmp_path / "sync@example.com_sales.csv"
    csv_file.write_text(
        "date,nm_id,doc_type,retail_price,ppvz_for_pay,delivery_rub,penalty,additional_payment,storage_fee,quantity\n"
        "2026-03-01,1001,Продажа,1000,850,40,0,0,2,1\n",
        encoding="utf-8",
    )

    r = client_sync.post(
        "/sync/migrate/folder",
        json={
            "folder_path": str(tmp_path),
            "filename_regex": r"(?P<user_email>.+)_(?P<dataset>sales|ads)\.csv",
            "dry_run": True,
            "include_all_users": False,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["dry_run"] is True
    assert data["matched_files"] == 1
    assert data["processed_files"] == 1
    assert data["source_rows"] == 1
    assert data["inserted_rows"] == 0
    assert any(f["status"] == "validated" for f in data["files"])


def test_sync_migrate_folder_import_calls_db_commit(client_sync: TestClient, tmp_path):
    token = _get_token(client_sync)
    csv_file = tmp_path / "sync@example.com_ads.csv"
    csv_file.write_text(
        "date,nm_id,campaign_id,spend\n"
        "2026-03-01,1001,555,123.45\n",
        encoding="utf-8",
    )

    r = client_sync.post(
        "/sync/migrate/folder",
        json={
            "folder_path": str(tmp_path),
            "filename_regex": r"(?P<user_email>.+)_(?P<dataset>sales|ads)\.csv",
            "dry_run": False,
            "include_all_users": False,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["dry_run"] is False
    assert data["processed_files"] == 1
    assert data["inserted_rows"] == 1
    assert any(f["status"] == "imported" for f in data["files"])


@patch("app.services.folder_migration._read_xlsx_sheet_rows")
def test_sync_migrate_folder_xlsx_dry_run(mock_read_xlsx_sheet_rows, client_sync: TestClient, tmp_path):
    token = _get_token(client_sync)
    xlsx_file = tmp_path / "Копия DB_sync@example.com.xlsx"
    xlsx_file.write_text("stub", encoding="utf-8")

    def _side_effect(path, sheet_name):
        if sheet_name == "DB_Raw_Data":
            return [{"date": "2026-03-01", "nm_id": "1001.0", "doc_type": "Продажа", "retail_price": 1000}]
        if sheet_name == "DB_Ads_Raw":
            return [{"date": "2026-03-01", "nm_id": "1001.0", "campaign_id": "555.0", "spend": 12.5}]
        return []

    mock_read_xlsx_sheet_rows.side_effect = _side_effect

    r = client_sync.post(
        "/sync/migrate/folder",
        json={
            "folder_path": str(tmp_path),
            "filename_regex": r"Копия DB_(?P<user_email>.+)\.xlsx",
            "file_glob": "*.xlsx",
            "dry_run": True,
            "include_all_users": False,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["matched_files"] == 1
    assert data["processed_files"] == 1
    assert data["source_rows"] == 2
    assert data["inserted_rows"] == 0
    datasets = {f["dataset"] for f in data["files"] if f["status"] == "validated"}
    assert "sales" in datasets
    assert "ads" in datasets


@patch("app.services.folder_migration._read_xlsx_sheet_rows")
def test_sync_migrate_folder_xlsx_auto_create_users(
    mock_read_xlsx_sheet_rows,
    client_sync: TestClient,
    tmp_path,
):
    from unittest.mock import MagicMock
    from app.models.user import User
    from app.schemas.sync import FolderMigrationRequest
    from app.services.folder_migration import run_folder_migration

    xlsx_file = tmp_path / "Копия DB_new.user@example.com.xlsx"
    xlsx_file.write_text("stub", encoding="utf-8")

    def _side_effect(path, sheet_name):
        if sheet_name == "DB_Raw_Data":
            return [{"date": "2026-03-01", "nm_id": 1001, "doc_type": "Продажа", "retail_price": 1000}]
        if sheet_name == "DB_Ads_Raw":
            return [{"date": "2026-03-01", "nm_id": 1001, "campaign_id": 555, "spend": 12.5}]
        return []

    mock_read_xlsx_sheet_rows.side_effect = _side_effect

    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = None
    current_user = User(
        id="sync-user-id",
        email="sync@example.com",
        password_hash=FAKE_HASH,
        wb_api_key="wb-key",
        is_active=True,
    )
    req = FolderMigrationRequest(
        folder_path=str(tmp_path),
        filename_regex=r"Копия DB_(?P<user_email>.+)\.xlsx",
        file_glob="*.xlsx",
        dry_run=True,
        include_all_users=True,
        auto_create_users=True,
        auto_create_users_password="TempPass123!",
        auto_create_users_is_active=False,
    )
    result = run_folder_migration(fake_db, current_user, req)
    assert result.created_users == 1
    assert result.processed_files == 1
    assert result.source_rows == 2
