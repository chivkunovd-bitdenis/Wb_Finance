from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InteractiveAuthDisabledError(Exception):
    message: str


@dataclass(frozen=True)
class InteractiveAuthFailedError(Exception):
    message: str


def user_storage_state_path(*, user_id: str) -> Path:
    """
    Per-store-owner Playwright storage_state snapshot.

    IMPORTANT: this file contains auth cookies/state and must be treated as a secret.
    """
    base = (os.getenv("WB_PLAYWRIGHT_STORAGE_STATE_DIR") or "").strip() or "tmp/wb_storage_states"
    p = Path(base) / f"{user_id}.json"
    return p


def user_wb_reconnect_flag_path(*, user_id: str) -> Path:
    """
    Sentinel: storage_state файл есть, но headless-забор отчёта попросил человека перелогиниться.
    """
    base = (os.getenv("WB_PLAYWRIGHT_STORAGE_STATE_DIR") or "").strip() or "tmp/wb_storage_states"
    return Path(base) / f"{user_id}.reconnect_required"


def set_wb_access_reconnect_required(*, user_id: str) -> None:
    p = user_wb_reconnect_flag_path(user_id=user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("1\n", encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def clear_wb_access_reconnect_required(*, user_id: str) -> None:
    user_wb_reconnect_flag_path(user_id=user_id).unlink(missing_ok=True)


def wb_reconnect_required(*, user_id: str) -> bool:
    return user_wb_reconnect_flag_path(user_id=user_id).is_file()


def wb_headless_access_effective(*, user_id: str) -> bool:
    """Файл storage_state валиден и нет маркера принудительного переподключения."""
    p = user_storage_state_path(user_id=user_id)
    if not p.is_file() or p.stat().st_size < 50:
        return False
    return not wb_reconnect_required(user_id=user_id)


def interactive_grant_wb_access(*, user_id: str) -> dict[str, Any]:
    """
    Interactive, headed Playwright login flow:
    - opens a real browser window (on the machine where the API runs)
    - user logs in manually (phone/code/captcha/etc)
    - storage_state is saved to per-user file
    - browser closes

    This is intentionally env-gated; production servers often have no display.
    """
    enabled = (os.getenv("AI_WB_INTERACTIVE_AUTH_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        raise InteractiveAuthDisabledError("Interactive WB auth is disabled (AI_WB_INTERACTIVE_AUTH_ENABLED=0)")

    # Docker on mac/linux typically has no X server; headed mode will crash.
    # Prefer uploading a "access file" (storage_state) in such environments.
    if Path("/.dockerenv").exists() or not (os.getenv("DISPLAY") or "").strip():
        raise InteractiveAuthDisabledError(
            "Interactive WB auth is not available in this environment (no display). "
            "Use storage_state upload flow instead."
        )

    # Lazy import: Playwright is heavy and optional.
    from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

    timeout_sec = int((os.getenv("AI_WB_INTERACTIVE_AUTH_TIMEOUT_SEC") or "240") or "240")
    out = user_storage_state_path(user_id=user_id)
    out.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        try:
            page.goto("https://seller.wildberries.ru/", wait_until="domcontentloaded", timeout=60_000)

            # Wait until the password input disappears => most basic "logged in" heuristic.
            # (WB can change; this is best-effort and should stay human-driven.)
            page.wait_for_function(
                "() => document.querySelector(\"input[type='password']\") === null",
                timeout=timeout_sec * 1000,
            )

            # Persist session state for future headless fetches.
            context.storage_state(path=str(out))
            clear_wb_access_reconnect_required(user_id=user_id)
            return {"status": "ok", "storage_state_path": str(out)}
        except Exception as exc:  # noqa: BLE001
            raise InteractiveAuthFailedError(str(exc)) from exc
        finally:
            context.close()
            browser.close()

