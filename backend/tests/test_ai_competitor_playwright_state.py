from __future__ import annotations

from pathlib import Path

import pytest

from app.services.ai_competitor_playwright import (
    PlaywrightBlockedError,
    _new_context_kwargs,
    _list_url,
    _period_dropdown_selector,
    _row_click_selector,
    _row_text,
)


def test_new_context_kwargs_without_storage_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WB_PLAYWRIGHT_STORAGE_STATE_PATH", raising=False)
    kw = _new_context_kwargs()
    assert kw["accept_downloads"] is True
    assert "storage_state" not in kw


def test_new_context_kwargs_with_storage_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    p.write_text('{"cookies":[],"origins":[]}', encoding="utf-8")
    monkeypatch.setenv("WB_PLAYWRIGHT_STORAGE_STATE_PATH", str(p))
    kw = _new_context_kwargs()
    assert kw["accept_downloads"] is True
    assert kw["storage_state"] == str(p)


def test_new_context_kwargs_raises_when_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "missing.json"
    monkeypatch.setenv("WB_PLAYWRIGHT_STORAGE_STATE_PATH", str(p))
    with pytest.raises(PlaywrightBlockedError):
        _ = _new_context_kwargs()


def test_config_validators_raise_on_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WB_COMPETITOR_LIST_URL", raising=False)
    monkeypatch.delenv("WB_COMPETITOR_ROW_TEXT", raising=False)
    monkeypatch.delenv("WB_COMPETITOR_ROW_CLICK_SELECTOR", raising=False)
    monkeypatch.delenv("WB_COMPETITOR_PERIOD_DROPDOWN_SELECTOR", raising=False)
    with pytest.raises(PlaywrightBlockedError):
        _ = _list_url()
    with pytest.raises(PlaywrightBlockedError):
        _ = _row_text()
    with pytest.raises(PlaywrightBlockedError):
        _ = _row_click_selector()
    with pytest.raises(PlaywrightBlockedError):
        _ = _period_dropdown_selector()

