"""
Integration-тесты роутера daily_brief:
  GET  /dashboard/daily-brief
  POST /dashboard/daily-brief/generate

БД подменена на mock; Celery-задача мокируется через patch.
"""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import get_db

FAKE_HASH = "$2b$12$faketesthash"
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


@pytest.fixture(autouse=True)
def _daily_brief_enabled_by_default(monkeypatch):
    # В API-тестах по умолчанию считаем фичу включённой.
    # Отдельные тесты могут переопределить на "0".
    monkeypatch.setenv("DAILY_BRIEF_ENABLED", "1")


# ─── Mock-фабрика для БД ─────────────────────────────────────────────────────

def _make_db_with_brief(brief_obj):
    """Сессия: один пользователь, заданная DailyBrief-запись (или None)."""
    from app.models.user import User
    from app.models.daily_brief import DailyBrief

    user = User(
        id="brief-user-id",
        email="brief@example.com",
        password_hash=FAKE_HASH,
        wb_api_key="wb-key",
        is_active=True,
    )
    session = MagicMock()
    session.get.return_value = user

    user_chain = MagicMock()
    user_chain.filter.return_value = user_chain
    user_chain.first.return_value = user

    brief_chain = MagicMock()
    brief_chain.filter.return_value = brief_chain
    brief_chain.first.return_value = brief_obj

    def _query(model):
        if model is User:
            return user_chain
        if model is DailyBrief:
            return brief_chain
        return MagicMock()

    session.query.side_effect = _query
    session.add.return_value = None
    session.commit.return_value = None
    session.refresh.return_value = None
    return session


def _get_db_no_brief():
    yield _make_db_with_brief(None)


def _get_db_ready_brief():
    from app.models.daily_brief import DailyBrief
    brief = MagicMock(spec=DailyBrief)
    brief.status = "ready"
    brief.text = "## Итог дня\n\nТест-сводка"
    brief.error_message = None
    brief.generated_at = None
    brief.date_for = date.today() - timedelta(days=1)
    yield _make_db_with_brief(brief)


def _get_db_generating_brief():
    from app.models.daily_brief import DailyBrief
    brief = MagicMock(spec=DailyBrief)
    brief.status = "generating"
    brief.text = None
    brief.error_message = None
    brief.generated_at = None
    brief.date_for = date.today() - timedelta(days=1)
    yield _make_db_with_brief(brief)


# ─── Фикстуры клиентов ────────────────────────────────────────────────────────

def _make_client(db_override) -> TestClient:
    def _fake_verify(plain: str, hashed: str) -> bool:
        return plain == "pass" and hashed == FAKE_HASH

    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.verify.side_effect = _fake_verify
        app.dependency_overrides[get_db] = db_override
        client = TestClient(app)
        return client


@pytest.fixture
def client_no_brief():
    def _fake_verify(plain, hashed):
        return plain == "pass" and hashed == FAKE_HASH

    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.verify.side_effect = _fake_verify
        app.dependency_overrides[get_db] = _get_db_no_brief
        try:
            with TestClient(app) as c:
                yield c
        finally:
            app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client_ready_brief():
    def _fake_verify(plain, hashed):
        return plain == "pass" and hashed == FAKE_HASH

    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.verify.side_effect = _fake_verify
        app.dependency_overrides[get_db] = _get_db_ready_brief
        try:
            with TestClient(app) as c:
                yield c
        finally:
            app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client_generating():
    def _fake_verify(plain, hashed):
        return plain == "pass" and hashed == FAKE_HASH

    with patch("app.core.security.pwd_context") as mock_pwd:
        mock_pwd.verify.side_effect = _fake_verify
        app.dependency_overrides[get_db] = _get_db_generating_brief
        try:
            with TestClient(app) as c:
                yield c
        finally:
            app.dependency_overrides.pop(get_db, None)


def _login(client: TestClient) -> str:
    r = client.post("/auth/login", json={"email": "brief@example.com", "password": "pass"})
    assert r.status_code == 200, f"login failed: {r.text}"
    return r.json()["access_token"]


# ─── GET /dashboard/daily-brief ───────────────────────────────────────────────

def test_get_daily_brief_unauthorized_returns_401(client_no_brief):
    r = client_no_brief.get("/dashboard/daily-brief")
    assert r.status_code == 401


def test_get_daily_brief_no_brief_returns_pending(client_no_brief):
    """Если сводки нет в БД — возвращаем status=pending, не запускаем генерацию."""
    token = _login(client_no_brief)
    r = client_no_brief.get(
        "/dashboard/daily-brief",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "pending"
    assert data["text"] is None
    assert data["date_for"] == YESTERDAY


def test_get_daily_brief_ready_returns_text(client_ready_brief):
    """Если сводка готова (status=ready) — возвращаем текст."""
    token = _login(client_ready_brief)
    r = client_ready_brief.get(
        "/dashboard/daily-brief",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ready"
    assert data["text"] is not None
    assert "Итог дня" in data["text"]


# ─── POST /dashboard/daily-brief/generate ────────────────────────────────────

def test_trigger_generate_unauthorized_returns_401(client_no_brief):
    r = client_no_brief.post("/dashboard/daily-brief/generate")
    assert r.status_code == 401


def test_trigger_generate_already_ready_returns_ready_without_celery(client_ready_brief):
    """Если сводка уже готова — возвращаем ready без повторного запуска Celery."""
    token = _login(client_ready_brief)
    with patch("celery_app.tasks.generate_daily_brief") as mock_task:
        r = client_ready_brief.post(
            "/dashboard/daily-brief/generate",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ready"
        mock_task.delay.assert_not_called()


def test_trigger_generate_already_generating_returns_generating(client_generating):
    """Если генерация уже идёт — не запускаем дублирующую задачу."""
    token = _login(client_generating)
    with patch("celery_app.tasks.generate_daily_brief") as mock_task:
        r = client_generating.post(
            "/dashboard/daily-brief/generate",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "generating"
        mock_task.delay.assert_not_called()


def test_trigger_generate_no_brief_starts_celery(client_no_brief):
    """Если сводки нет — создаём запись и ставим задачу в Celery."""
    token = _login(client_no_brief)
    # Задача импортируется внутри роута динамически, патчим в исходном модуле.
    with patch("celery_app.tasks.generate_daily_brief") as mock_task:
        mock_delay = MagicMock()
        mock_task.delay = mock_delay
        r = client_no_brief.post(
            "/dashboard/daily-brief/generate",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "generating"
        mock_delay.assert_called_once()
        # Проверяем, что задача вызвана с user_id и строкой даты
        call_args = mock_delay.call_args[0]
        assert call_args[0] == "brief-user-id"
        assert call_args[1] == YESTERDAY


def test_trigger_generate_disabled_returns_503_and_does_not_enqueue(client_no_brief, monkeypatch):
    token = _login(client_no_brief)
    monkeypatch.setenv("DAILY_BRIEF_ENABLED", "0")
    with patch("celery_app.tasks.generate_daily_brief") as mock_task:
        r = client_no_brief.post(
            "/dashboard/daily-brief/generate",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 503
        mock_task.delay.assert_not_called()
