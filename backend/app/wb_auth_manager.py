from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from app.services.ai_wb_access_service import user_storage_state_path


app = FastAPI(title="WB Auth Manager", version="1.0.0")


def _require_internal_token(token: str | None) -> None:
    expected = (os.getenv("WB_AUTH_INTERNAL_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="WB auth manager is not configured")
    if (token or "").strip() != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


class StartRequest(BaseModel):
    user_id: str


class SaveRequest(BaseModel):
    user_id: str


@dataclass
class _Session:
    browser: Any
    context: Any
    page: Any


_lock = threading.Lock()
_sessions: dict[str, _Session] = {}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/start")
def start(req: StartRequest, x_internal_token: str | None = None) -> dict[str, str]:
    _require_internal_token(x_internal_token)
    user_id = (req.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    with _lock:
        if user_id in _sessions:
            return {"status": "ok", "message": "already_started"}

    # Lazy import to keep startup light.
    from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

    # Must run headed on virtual display (Xvfb provides DISPLAY inside container).
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://seller.wildberries.ru/", wait_until="domcontentloaded", timeout=60_000)

    with _lock:
        _sessions[user_id] = _Session(browser=browser, context=context, page=page)
    return {"status": "ok"}


@app.post("/save")
def save(req: SaveRequest, x_internal_token: str | None = None) -> dict[str, str]:
    _require_internal_token(x_internal_token)
    user_id = (req.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    with _lock:
        sess = _sessions.get(user_id)
        if sess is None:
            raise HTTPException(status_code=409, detail="session_not_started")

    out = user_storage_state_path(user_id=user_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    sess.context.storage_state(path=str(out))

    # Close session (best-effort)
    try:
        sess.context.close()
    except Exception:
        pass
    try:
        sess.browser.close()
    except Exception:
        pass

    with _lock:
        _sessions.pop(user_id, None)

    # Ensure file exists and is not empty.
    if not out.is_file() or out.stat().st_size < 50:
        raise HTTPException(status_code=500, detail="failed_to_save_storage_state")

    # Lock down perms best-effort (works on linux).
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass

    return {"status": "ok", "path": str(out)}

