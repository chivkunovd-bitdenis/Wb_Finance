from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.feature_flags import (
    get_ai_module_allowlist_emails,
    is_ai_module_enabled_for_user,
    is_ai_module_product_gen_enabled_for_user,
)
from app.dependencies import get_current_user
from app.main import app


def _user(*, email: str, is_admin: bool = False) -> MagicMock:
    return MagicMock(
        email=email,
        is_admin=is_admin,
        is_active=True,
        id="u1",
        wb_api_key=None,
    )


def test_allowlist_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_MODULE_ALLOWLIST_EMAILS", " Vitalik-hors@mail.ru , other@x.com ")
    assert get_ai_module_allowlist_emails() == {"vitalik-hors@mail.ru", "other@x.com"}


def test_default_allowlist_is_vitalik_when_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_MODULE_ALLOWLIST_EMAILS", raising=False)
    assert get_ai_module_allowlist_emails() == {"vitalik-hors@mail.ru"}
    assert is_ai_module_enabled_for_user(_user(email="Vitalik-hors@mail.ru")) is True
    assert is_ai_module_enabled_for_user(_user(email="any@x.com")) is False
    assert is_ai_module_enabled_for_user(_user(email="any@x.com", is_admin=True)) is True


def test_admin_and_allowlisted_have_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_MODULE_ALLOWLIST_EMAILS", "vitalik-hors@mail.ru")
    vitalik = _user(email="Vitalik-hors@mail.ru")
    admin = _user(email="other@x.com", is_admin=True)
    stranger = _user(email="stranger@x.com")
    assert is_ai_module_enabled_for_user(vitalik) is True
    assert is_ai_module_enabled_for_user(admin) is True
    assert is_ai_module_enabled_for_user(stranger) is False
    assert is_ai_module_product_gen_enabled_for_user(vitalik) is True
    assert is_ai_module_product_gen_enabled_for_user(admin) is True
    assert is_ai_module_product_gen_enabled_for_user(stranger) is False


def test_ai_tasks_forbidden_for_stranger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_MODULE_ALLOWLIST_EMAILS", "vitalik-hors@mail.ru")
    app.dependency_overrides[get_current_user] = lambda: _user(email="stranger@x.com")
    try:
        with TestClient(app) as client:
            r = client.get("/ai/tasks")
        assert r.status_code == 403
        assert "недоступен" in r.text.lower()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_auth_me_reports_ai_module_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_MODULE_ALLOWLIST_EMAILS", "vitalik-hors@mail.ru")
    app.dependency_overrides[get_current_user] = lambda: _user(email="vitalik-hors@mail.ru")
    try:
        with TestClient(app) as client:
            r = client.get("/auth/me")
        assert r.status_code == 200
        data = r.json()
        assert data["ai_module_enabled"] is True
        assert data["ai_module_product_gen_enabled"] is True
    finally:
        app.dependency_overrides.pop(get_current_user, None)
