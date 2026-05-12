from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel

from app.services.ai_wb_access_service import clear_wb_access_reconnect_required, user_storage_state_path


app = FastAPI(title="WB Auth Manager", version="1.0.0")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _require_internal_token(token: str | None) -> None:
    expected = (os.getenv("WB_AUTH_INTERNAL_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="WB auth manager is not configured")
    if (token or "").strip() != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")


class StartRequest(BaseModel):
    user_id: str
    force: bool = False


class SaveRequest(BaseModel):
    user_id: str


class StatusRequest(BaseModel):
    user_id: str


@dataclass
class _Session:
    playwright: Any
    browser: Any
    context: Any
    page: Any


_lock = asyncio.Lock()
_sessions: dict[str, _Session] = {}


async def _keepalive_loop() -> None:
    """
    Keep remote WB sessions warm so WB doesn't log out / VNC doesn't go stale.
    Best-effort: we ping the active page periodically.
    """
    interval_sec = int((os.getenv("WB_AUTH_KEEPALIVE_SECONDS") or "60") or "60")
    interval_sec = max(10, interval_sec)
    while True:
        await asyncio.sleep(interval_sec)
        async with _lock:
            sessions = list(_sessions.items())
        for user_id, sess in sessions:
            try:
                with contextlib.suppress(Exception):
                    await sess.page.evaluate("() => 1")
                with contextlib.suppress(Exception):
                    await sess.page.bring_to_front()
            except Exception:
                logger.exception("wb_auth keepalive failed user_id=%s", user_id)


@app.on_event("startup")
async def _startup() -> None:
    asyncio.create_task(_keepalive_loop())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/status")
async def status_(
    req: StatusRequest,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> dict[str, str | bool]:
    _require_internal_token(x_internal_token)
    user_id = (req.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    async with _lock:
        sess = _sessions.get(user_id)
        has = sess is not None
        url = ""
        if sess is not None:
            try:
                with contextlib.suppress(Exception):
                    await sess.page.evaluate("() => 1")
                with contextlib.suppress(Exception):
                    await sess.page.bring_to_front()
                url = str(sess.page.url or "")
            except Exception:
                url = ""

    return {"status": "ok", "active": has, "url": url}


@app.post("/start")
async def start(
    req: StartRequest,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> dict[str, str]:
    _require_internal_token(x_internal_token)
    user_id = (req.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    # If session already exists, reuse it by default (so we don't lose WB login cookies/state).
    # Caller can pass force=true to explicitly recreate.
    async with _lock:
        existing = _sessions.get(user_id)
        if existing is not None and not req.force:
            url = ""
            try:
                url = str(existing.page.url or "")
            except Exception:
                url = ""
            return {"status": "ok", "url": url, "reused": "1"}
        existing = _sessions.pop(user_id, None)

    if existing is not None:
        with contextlib.suppress(Exception):
            await existing.context.close()
        with contextlib.suppress(Exception):
            await existing.browser.close()
        with contextlib.suppress(Exception):
            await existing.playwright.stop()

    # Ensure no stale chromium windows remain on the shared Xvfb display.
    # Best-effort: if pkill is unavailable, we proceed anyway.
    with contextlib.suppress(Exception):
        proc = await asyncio.create_subprocess_exec("pkill", "-f", "chromium", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()

    # Lazy import to keep startup light.
    from playwright.async_api import async_playwright  # type: ignore[import-not-found]

    # Must run headed on virtual display (Xvfb provides DISPLAY inside container).
    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=False,
        args=[
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--window-position=0,0",
            "--window-size=1440,900",
            "--new-window",
        ],
    )
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://seller.wildberries.ru/", wait_until="domcontentloaded", timeout=60_000)
    # Make sure the WB tab is the visible/focused window in noVNC.
    with contextlib.suppress(Exception):
        await page.bring_to_front()
    try:
        logger.info("wb_auth start: user_id=%s url=%s pages=%s", user_id, page.url, len(context.pages))
    except Exception:
        pass

    async with _lock:
        _sessions[user_id] = _Session(playwright=p, browser=browser, context=context, page=page)
    return {"status": "ok", "url": page.url}


@app.post("/save")
async def save(
    req: SaveRequest,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> dict[str, str]:
    _require_internal_token(x_internal_token)
    user_id = (req.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    async with _lock:
        sess = _sessions.get(user_id)
        if sess is None:
            raise HTTPException(status_code=409, detail="session_not_started")

    out = user_storage_state_path(user_id=user_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    await sess.context.storage_state(path=str(out))

    # Ensure file exists and is not empty.
    if not out.is_file() or out.stat().st_size < 50:
        raise HTTPException(status_code=500, detail="failed_to_save_storage_state")

    # Lock down perms best-effort (works on linux).
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass

    clear_wb_access_reconnect_required(user_id=user_id)

    return {"status": "ok", "path": str(out)}

