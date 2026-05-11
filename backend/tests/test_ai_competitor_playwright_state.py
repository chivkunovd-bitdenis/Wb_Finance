from __future__ import annotations

from pathlib import Path

import pytest

from app.services.ai_competitor_playwright import PlaywrightBlockedError, _new_context_kwargs


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

