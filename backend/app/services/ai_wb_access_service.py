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
            return {"status": "ok", "storage_state_path": str(out)}
        except Exception as exc:  # noqa: BLE001
            raise InteractiveAuthFailedError(str(exc)) from exc
        finally:
            context.close()
            browser.close()

