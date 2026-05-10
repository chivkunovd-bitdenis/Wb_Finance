from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class PlaywrightAuthError(Exception):
    message: str


@dataclass(frozen=True)
class PlaywrightBlockedError(Exception):
    message: str


def fetch_comparison_excel_bytes(*, login: str, password: str, period: str) -> tuple[bytes, dict[str, Any]]:
    """
    Best-effort Playwright automation for WB Seller cabinet.

    IMPORTANT:
    - Real WB UI can change frequently.
    - This implementation is intentionally conservative and returns actionable errors.
    - Tests should monkeypatch this function; CI must not hit real WB.

    Returns: (excel_bytes, raw_meta)
    """
    period = (period or "").strip().lower()
    if period not in {"week", "month", "quarter"}:
        raise ValueError("invalid period")

    # Allow disabling Playwright on server builds that don't ship browsers.
    if (os.getenv("AI_COMPETITOR_PLAYWRIGHT_ENABLED") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        raise PlaywrightBlockedError("Playwright fetch is disabled (AI_COMPETITOR_PLAYWRIGHT_ENABLED=0)")

    # Lazy import: Playwright is heavy; keep API fast.
    from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

    started_at = datetime.now(timezone.utc).isoformat()
    meta: dict[str, Any] = {"started_at": started_at, "period": period}

    # NOTE: Real selectors/flow may require updates. We try a minimal safe flow.
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        try:
            page.goto("https://seller.wildberries.ru/", wait_until="domcontentloaded", timeout=60_000)

            # Heuristic: if already logged in, cabinet should show something; otherwise login form.
            # We cannot guarantee selectors; detect common auth fields.
            if page.locator("input[type='password']").count() > 0:
                # Try to fill login/password if there are visible inputs.
                # Selector strategy is intentionally broad; may need refinement.
                page.locator("input").first.fill(login, timeout=10_000)
                page.locator("input[type='password']").first.fill(password, timeout=10_000)
                # Find a submit-like button.
                btn = page.locator("button").filter(has_text="Войти").first
                if btn.count() == 0:
                    btn = page.locator("button[type='submit']").first
                if btn.count() == 0:
                    raise PlaywrightBlockedError("WB login form detected, but submit button not found (UI changed)")
                btn.click(timeout=10_000)
                page.wait_for_load_state("networkidle", timeout=60_000)

            # Navigate to competitor comparison page.
            # WB deep links can change; keep as env override.
            report_url = (os.getenv("WB_COMPETITOR_REPORT_URL") or "").strip() or "https://seller.wildberries.ru/"
            page.goto(report_url, wait_until="domcontentloaded", timeout=60_000)

            # Download excel: require explicit URL in env for now.
            # This makes behavior deterministic for deployments: operator sets correct URL once.
            download_btn_selector = (os.getenv("WB_COMPETITOR_REPORT_DOWNLOAD_SELECTOR") or "").strip()
            if not download_btn_selector:
                raise PlaywrightBlockedError(
                    "WB competitor download selector is not configured (WB_COMPETITOR_REPORT_DOWNLOAD_SELECTOR)"
                )

            with page.expect_download(timeout=90_000) as dl_info:
                page.locator(download_btn_selector).first.click()
            download = dl_info.value
            content = download.path().read_bytes()  # type: ignore[union-attr]
            if not content:
                raise PlaywrightBlockedError("Downloaded Excel is empty")
            meta.update({"download_url": download.url, "suggested_filename": download.suggested_filename})
            return content, meta
        except PlaywrightBlockedError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Heuristic: treat as auth failure if page shows 401/forbidden hints.
            raise PlaywrightAuthError(str(exc)) from exc
        finally:
            context.close()
            browser.close()

