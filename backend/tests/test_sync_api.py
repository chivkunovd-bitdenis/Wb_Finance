"""
Тесты эндпоинтов постановки задач синка в очередь: POST /sync/sales, /ads, /funnel, /recalculate, /initial.
Celery подменён: .delay() не ставит задачу в Redis, возвращает фейковый result.id.
Витрины: pnl_daily (по дням), sku_daily (артикул×день) заполняются задачами recalculate_*.
hash_password/verify_password мокаем, чтобы не дергать bcrypt в контейнере.
"""
from datetime import date
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
        if model is FunnelBackfillState:
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
        if model is FunnelBackfillState:
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


@patch("app.routers.sync.sync_sales")
def test_sync_sales_for_non_owner_returns_403(mock_sync_sales, client_sync_granted: TestClient):
    mock_sync_sales.delay.return_value = MagicMock(id="task-sales-should-not-run")
    token = _get_token(client_sync_granted)
    r = client_sync_granted.post(
        "/sync/sales",
        json={"date_from": "2025-03-01", "date_to": "2025-03-05"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    mock_sync_sales.delay.assert_called_once()


@patch("app.routers.sync.sync_sales")
def test_sync_sales_returns_202_and_task_id(mock_sync_sales, client_sync: TestClient):
    mock_sync_sales.delay.return_value = MagicMock(id="task-sales-123")
    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/sales",
        json={"date_from": "2025-03-01", "date_to": "2025-03-05"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == "task-sales-123"
    mock_sync_sales.delay.assert_called_once()
    call_args = mock_sync_sales.delay.call_args[0]
    assert call_args[0] == "sync-user-id"
    assert call_args[1] == "2025-03-01"
    assert call_args[2] == "2025-03-05"


@patch("app.routers.sync.sync_ads")
def test_sync_ads_returns_200_and_task_id(mock_sync_ads, client_sync: TestClient):
    mock_sync_ads.delay.return_value = MagicMock(id="task-ads-456")
    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/ads",
        json={"date_from": "2025-03-01", "date_to": "2025-03-05"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == "task-ads-456"
    mock_sync_ads.delay.assert_called_once()


@patch("app.routers.sync.sync_funnel")
def test_sync_funnel_returns_200_and_task_id(mock_sync_funnel, client_sync: TestClient):
    mock_sync_funnel.delay.return_value = MagicMock(id="task-funnel-789")
    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/funnel",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == "task-funnel-789"
    mock_sync_funnel.delay.assert_called_once()
    # Без дат — передаются None, None (дефолтное окно 7 дней в задаче)
    call_args = mock_sync_funnel.delay.call_args[0]
    assert call_args[0] == "sync-user-id"
    assert call_args[1] is None
    assert call_args[2] is None


@patch("app.routers.sync.after_period_sync_enqueue_funnel")
@patch("app.routers.sync.chord")
@patch("app.routers.sync.sync_ads")
@patch("app.routers.sync.sync_sales")
def test_sync_period_enqueues_chord_sales_ads_then_funnel(
    mock_sync_sales,
    mock_sync_ads,
    mock_chord,
    mock_after_period,
    client_sync: TestClient,
):
    mock_async_result = MagicMock(id="task-period-chord")
    mock_chord.return_value = MagicMock(return_value=mock_async_result)

    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/period",
        json={"date_from": "2025-03-01", "date_to": "2025-03-07"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["task_id"] == "task-period-chord"

    mock_sync_sales.delay.assert_not_called()
    mock_sync_ads.delay.assert_not_called()
    mock_sync_sales.s.assert_called_once_with("sync-user-id", "2025-03-01", "2025-03-07")
    mock_sync_ads.s.assert_called_once_with("sync-user-id", "2025-03-01", "2025-03-07")
    mock_after_period.s.assert_called_once_with("sync-user-id", "2025-03-01", "2025-03-07")
    mock_chord.assert_called_once()


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


@patch("app.routers.sync.sync_funnel")
def test_sync_funnel_with_dates_passes_to_task(mock_sync_funnel, client_sync: TestClient):
    mock_sync_funnel.delay.return_value = MagicMock(id="task-funnel-dates")
    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/funnel",
        json={"date_from": "2025-03-01", "date_to": "2025-03-07"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    mock_sync_funnel.delay.assert_called_once_with(
        "sync-user-id", "2025-03-01", "2025-03-07"
    )


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


@patch("app.routers.sync.after_initial_sync_enqueue_funnel")
@patch("app.routers.sync.chord")
@patch("app.routers.sync.sync_ads")
@patch("app.routers.sync.sync_sales")
@patch("app.routers.sync.date")
def test_sync_initial_uses_last_30_days(
    mock_date,
    mock_sync_sales,
    mock_sync_ads,
    mock_chord,
    mock_after_funnel,
    client_sync: TestClient,
):
    """Проверяем, что /sync/initial ставит chord(sync_sales, sync_ads) на последние 30 дней и затем воронку."""
    # today = 2025‑03‑31 -> date_to = 2025‑03‑30, date_from = 2025‑03‑01
    mock_date.today.return_value = date(2025, 3, 31)

    def _date_ctor(*args, **kwargs):
        return date(*args, **kwargs)

    mock_date.side_effect = _date_ctor

    mock_async_result = MagicMock(id="task-initial-chord")
    mock_chord.return_value = MagicMock(return_value=mock_async_result)

    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/initial",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == "task-initial-chord"

    mock_sync_sales.delay.assert_not_called()
    mock_sync_ads.delay.assert_not_called()
    mock_sync_sales.s.assert_called_once_with("sync-user-id", "2025-03-01", "2025-03-30")
    mock_sync_ads.s.assert_called_once_with("sync-user-id", "2025-03-01", "2025-03-30")
    mock_chord.assert_called_once()
    header = mock_chord.call_args[0][0]
    assert len(header) == 2
    mock_after_funnel.s.assert_called_once_with("sync-user-id")


def test_sync_initial_without_wb_key_returns_400(client_sync_no_key: TestClient):
    token = _get_token(client_sync_no_key)
    r = client_sync_no_key.post(
        "/sync/initial",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    assert "WB API" in (r.json().get("detail") or "")


@patch("app.routers.sync.after_period_sync_enqueue_funnel")
@patch("app.routers.sync.chord")
@patch("app.routers.sync.sync_ads")
@patch("app.routers.sync.sync_sales")
@patch("app.routers.sync.date")
def test_sync_recent_updates_last_7_days(
    mock_date,
    mock_sync_sales,
    mock_sync_ads,
    mock_chord,
    mock_after_period,
    client_sync: TestClient,
):
    """
    /sync/recent — автосинк для «не первого входа»:
    обновляет последние 7 дней (включая вчера) и ставит в очередь продажи, рекламу и воронку.
    """
    # today = 2025‑04‑08 -> date_to = 2025‑04‑07, date_from = 2025‑04‑01
    mock_date.today.return_value = date(2025, 4, 8)

    def _date_ctor(*args, **kwargs):
        return date(*args, **kwargs)

    mock_date.side_effect = _date_ctor

    mock_async_result = MagicMock(id="task-recent-chord")
    mock_chord.return_value = MagicMock(return_value=mock_async_result)

    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/recent",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == "task-recent-chord"

    # Проверяем вычисление дат окна 7 дней
    mock_sync_sales.delay.assert_not_called()
    mock_sync_ads.delay.assert_not_called()
    mock_sync_sales.s.assert_called_once_with("sync-user-id", "2025-04-01", "2025-04-07")
    mock_sync_ads.s.assert_called_once_with("sync-user-id", "2025-04-01", "2025-04-07")
    mock_after_period.s.assert_called_once_with("sync-user-id", "2025-04-01", "2025-04-07")
    mock_chord.assert_called_once()


@patch("app.routers.sync.sync_funnel")
@patch("app.routers.sync.sync_ads")
@patch("app.routers.sync.sync_sales")
@patch("app.routers.sync.date")
def test_sync_backfill_2026_enqueues_month_chunks_and_funnel(
    mock_date,
    mock_sync_sales,
    mock_sync_ads,
    mock_sync_funnel,
    client_sync: TestClient,
):
    """
    /sync/backfill/2026:
    - продажи и реклама по месяцам с 2026-01-01 до вчера
    - воронка отдельно (None, None)
    """
    # today = 2026‑03‑05 -> date_to = 2026‑03‑04
    mock_date.today.return_value = date(2026, 3, 5)

    def _date_ctor(*args, **kwargs):
        return date(*args, **kwargs)

    mock_date.side_effect = _date_ctor

    # Для удобства — разные id
    mock_sync_sales.delay.side_effect = [
        MagicMock(id="sales-jan"),
        MagicMock(id="sales-feb"),
        MagicMock(id="sales-mar"),
    ]
    mock_sync_ads.delay.side_effect = [
        MagicMock(id="ads-jan"),
        MagicMock(id="ads-feb"),
        MagicMock(id="ads-mar"),
    ]
    mock_sync_funnel.delay.return_value = MagicMock(id="funnel-7d")

    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/backfill/2026",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "task_ids" in data and isinstance(data["task_ids"], list)

    # Янв 2026
    # Фев 2026
    # Мар 2026 (до 04 числа)
    expected_calls = [
        ("sync-user-id", "2026-01-01", "2026-01-31"),
        ("sync-user-id", "2026-02-01", "2026-02-28"),
        ("sync-user-id", "2026-03-01", "2026-03-04"),
    ]
    assert [c.args for c in mock_sync_sales.delay.call_args_list] == expected_calls
    assert [c.args for c in mock_sync_ads.delay.call_args_list] == expected_calls
    mock_sync_funnel.delay.assert_called_once_with("sync-user-id", None, None)

    # Возвращаемый список task_ids содержит все id (sales+ads по чанкам + funnel)
    assert data["task_ids"] == [
        "sales-jan", "ads-jan",
        "sales-feb", "ads-feb",
        "sales-mar", "ads-mar",
        "funnel-7d",
    ]


@patch("app.routers.sync.sync_ads")
@patch("app.routers.sync.sync_sales")
def test_sync_backfill_2025_enqueues_twelve_months(
    mock_sync_sales,
    mock_sync_ads,
    client_sync: TestClient,
):
    """POST /sync/backfill/2025 ставит в очередь продажи и рекламу по каждому месяцу 2025."""
    mock_sync_sales.delay.return_value = MagicMock(id="s1")
    mock_sync_ads.delay.return_value = MagicMock(id="a1")

    token = _get_token(client_sync)
    r = client_sync.post(
        "/sync/backfill/2025",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "task_ids" in data and isinstance(data["task_ids"], list)
    # 12 месяцев × 2 (sales + ads) = 24 вызова
    assert mock_sync_sales.delay.call_count == 12
    assert mock_sync_ads.delay.call_count == 12
    sales_calls = [c.args for c in mock_sync_sales.delay.call_args_list]
    assert sales_calls[0] == ("sync-user-id", "2025-01-01", "2025-01-31")
    assert sales_calls[11] == ("sync-user-id", "2025-12-01", "2025-12-31")
    assert len(data["task_ids"]) == 24


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
