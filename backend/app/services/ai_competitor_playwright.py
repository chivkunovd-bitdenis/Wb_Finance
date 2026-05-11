from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PlaywrightAuthError(Exception):
    message: str


@dataclass(frozen=True)
class PlaywrightBlockedError(Exception):
    message: str


def _storage_state_path() -> str | None:
    """
    Optional Playwright storage state snapshot to reuse existing WB session.

    IMPORTANT: this file contains auth cookies/state and must be treated as a secret.
    """
    p = (os.getenv("WB_PLAYWRIGHT_STORAGE_STATE_PATH") or "").strip()
    return p or None


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _list_url() -> str:
    url = _env("WB_COMPETITOR_LIST_URL")
    if not url:
        raise PlaywrightBlockedError("WB competitor list URL is not configured (WB_COMPETITOR_LIST_URL)")
    return url


def _row_text() -> str:
    t = _env("WB_COMPETITOR_ROW_TEXT")
    if not t:
        raise PlaywrightBlockedError("WB competitor row text is not configured (WB_COMPETITOR_ROW_TEXT)")
    return t


def _row_nm_id() -> str | None:
    raw = _env("WB_COMPETITOR_ROW_NM_ID")
    return raw or None


def _row_selector() -> str | None:
    s = _env("WB_COMPETITOR_ROW_SELECTOR")
    return s or None


def _row_click_selector() -> str:
    """
    Selector for a clickable element inside the row that navigates to report detail page.
    We keep it configurable because WB DOM is volatile.
    """
    s = _env("WB_COMPETITOR_ROW_CLICK_SELECTOR")
    if not s:
        raise PlaywrightBlockedError("WB competitor row click selector is not configured (WB_COMPETITOR_ROW_CLICK_SELECTOR)")
    return s


def _period_dropdown_selector() -> str:
    s = _env("WB_COMPETITOR_PERIOD_DROPDOWN_SELECTOR")
    if not s:
        raise PlaywrightBlockedError("WB competitor period dropdown selector is not configured (WB_COMPETITOR_PERIOD_DROPDOWN_SELECTOR)")
    return s


def _period_option_text(period: str) -> str:
    key = f"WB_COMPETITOR_PERIOD_OPTION_TEXT_{period.upper()}"
    t = _env(key)
    if not t:
        raise PlaywrightBlockedError(f"WB competitor period option text is not configured ({key})")
    return t


def _period_tab_selector(period: str) -> str | None:
    key = f"WB_COMPETITOR_PERIOD_TAB_SELECTOR_{period.upper()}"
    s = _env(key)
    return s or None


def _period_tab_label(period: str) -> str:
    """
    Fallback label for period tabs in WB UI (Russian).
    Can be overridden via WB_COMPETITOR_PERIOD_TAB_LABEL_<PERIOD>.
    """
    key = f"WB_COMPETITOR_PERIOD_TAB_LABEL_{period.upper()}"
    override = _env(key)
    if override:
        return override
    labels = {"today": "Сегодня", "week": "Неделя", "month": "Месяц", "quarter": "Квартал"}
    return labels.get(period, period)


def _generate_selector() -> str:
    s = _env("WB_COMPETITOR_GENERATE_SELECTOR")
    if not s:
        raise PlaywrightBlockedError("WB competitor generate button selector is not configured (WB_COMPETITOR_GENERATE_SELECTOR)")
    return s


def _export_menu_selector() -> str:
    s = _env("WB_COMPETITOR_EXPORT_MENU_SELECTOR")
    if not s:
        raise PlaywrightBlockedError("WB competitor export menu selector is not configured (WB_COMPETITOR_EXPORT_MENU_SELECTOR)")
    return s


def _export_excel_selector() -> str:
    s = _env("WB_COMPETITOR_EXPORT_EXCEL_SELECTOR")
    if not s:
        raise PlaywrightBlockedError("WB competitor export excel selector is not configured (WB_COMPETITOR_EXPORT_EXCEL_SELECTOR)")
    return s


def _create_excel_button_selector() -> str:
    s = _env("WB_COMPETITOR_CREATE_EXCEL_SELECTOR")
    if not s:
        raise PlaywrightBlockedError("WB competitor create excel selector is not configured (WB_COMPETITOR_CREATE_EXCEL_SELECTOR)")
    return s


def _excel_name_input_selector() -> str:
    s = _env("WB_COMPETITOR_EXCEL_NAME_INPUT_SELECTOR")
    if not s:
        raise PlaywrightBlockedError("WB competitor excel name input selector is not configured (WB_COMPETITOR_EXCEL_NAME_INPUT_SELECTOR)")
    return s


def _excel_generate_confirm_selector() -> str:
    s = _env("WB_COMPETITOR_EXCEL_GENERATE_CONFIRM_SELECTOR")
    if not s:
        raise PlaywrightBlockedError(
            "WB competitor excel generate confirm selector is not configured (WB_COMPETITOR_EXCEL_GENERATE_CONFIRM_SELECTOR)"
        )
    return s


def _reports_button_selector() -> str:
    s = _env("WB_COMPETITOR_REPORTS_BUTTON_SELECTOR")
    if not s:
        raise PlaywrightBlockedError("WB competitor reports button selector is not configured (WB_COMPETITOR_REPORTS_BUTTON_SELECTOR)")
    return s


def _report_download_button_selector() -> str:
    s = _env("WB_COMPETITOR_REPORT_DOWNLOAD_BUTTON_SELECTOR")
    if not s:
        raise PlaywrightBlockedError(
            "WB competitor report download button selector is not configured (WB_COMPETITOR_REPORT_DOWNLOAD_BUTTON_SELECTOR)"
        )
    return s


def _new_context_kwargs() -> dict[str, Any]:
    """
    Build kwargs for browser.new_context without importing Playwright types.
    """
    kw: dict[str, Any] = {"accept_downloads": True}
    p = _storage_state_path()
    if p:
        if not Path(p).is_file():
            raise PlaywrightBlockedError(
                f"Playwright storage_state file not found: {p!r} (WB_PLAYWRIGHT_STORAGE_STATE_PATH)"
            )
        kw["storage_state"] = p
    return kw


def _select_period(*, page: Any, period: str) -> None:
    """
    Select period inside report detail page via tabs (preferred) or dropdown (fallback).
    """
    tab_sel = _period_tab_selector(period)
    if tab_sel:
        page.locator(tab_sel).first.click(timeout=15_000)
        return
    # Fallback: click by visible label if possible
    label = _period_tab_label(period)
    try:
        page.get_by_text(label, exact=True).click(timeout=10_000)
        return
    except Exception:  # noqa: BLE001
        pass
    # Last resort: dropdown mode (older config)
    dd = _period_dropdown_selector()
    page.locator(dd).first.click(timeout=15_000)
    option_text = _period_option_text(period)
    page.get_by_text(option_text, exact=True).click(timeout=15_000)


def _create_and_download_excel(*, page: Any, period: str) -> tuple[bytes, dict[str, Any]]:
    """
    WB UI flow (as observed):
      - Click create Excel button (icon)
      - Fill file name
      - Click "Сформировать"
      - Wait a bit for generation, open "Отчёты"
      - Download the generated Excel from reports list
    """
    # Create Excel
    file_name = f"wb-cards-{period}-{int(datetime.now(timezone.utc).timestamp())}"
    page.locator(_create_excel_button_selector()).first.click(timeout=20_000)
    # Wait for modal input to appear before interacting (WB UI can render asynchronously).
    page.locator(_excel_name_input_selector()).first.wait_for(state="visible", timeout=20_000)
    page.locator(_excel_name_input_selector()).first.fill(file_name, timeout=20_000)
    try:
        btn = page.locator(_excel_generate_confirm_selector()).first
        btn.wait_for(state="visible", timeout=20_000)
        btn.click(timeout=20_000)
    except Exception:  # noqa: BLE001
        # Fallback to visible text inside modal ("Сформировать") — selector hashes often change.
        page.get_by_text("Сформировать", exact=False).first.click(timeout=20_000)

    # Open reports list
    page.wait_for_timeout(6_000)  # WB generation is async; give it a short head-start
    page.locator(_reports_button_selector()).first.click(timeout=20_000)

    # Prefer waiting by file name appearance (less flaky than fixed sleep).
    try:
        page.get_by_text(file_name, exact=False).first.wait_for(state="visible", timeout=180_000)
    except Exception:  # noqa: BLE001
        # If WB doesn't show name, continue with a best-effort click on the first download button.
        pass

    with page.expect_download(timeout=180_000) as dl_info:
        page.locator(_report_download_button_selector()).first.click(timeout=20_000)
    download = dl_info.value
    content = download.path().read_bytes()  # type: ignore[union-attr]
    if not content:
        raise PlaywrightBlockedError("Downloaded Excel is empty")
    meta = {"generated_file_name": file_name, "download_url": download.url, "suggested_filename": download.suggested_filename}
    return content, meta


def _open_report_from_list(*, page: Any) -> None:
    """
    Navigate to list URL, find row by selector or nm_id/text, click to open report detail.
    """
    page.goto(_list_url(), wait_until="domcontentloaded", timeout=60_000)
    click_sel = _row_click_selector()

    row = None
    sel = _row_selector()
    if sel:
        row = page.locator(sel).first
        if row.count() == 0:
            raise PlaywrightBlockedError(f"WB competitor row not found by selector: {sel!r}")
    else:
        nm = _row_nm_id()
        if nm:
            target = str(nm).strip()
            if not target.isdigit():
                raise PlaywrightBlockedError(f"WB competitor nm_id must be digits: {nm!r}")

            def _pick() -> Any | None:
                loc = page.get_by_text(target, exact=False)
                if loc.count() == 0:
                    return None
                # If there are multiple matches while scrolling/virtualization, pick the last one:
                # WB list is usually sorted by recency (top is newest); virtualization can render chunks.
                return loc.last

            found = _pick()
            if found is None:
                # Virtualized table: scroll down in steps until nm_id appears.
                # We keep this deterministic (bounded attempts) and avoid relying on fragile selectors.
                max_steps = int((_env("WB_COMPETITOR_SCROLL_STEPS") or "30") or "30")
                step_px = int((_env("WB_COMPETITOR_SCROLL_PX") or "900") or "900")
                for _ in range(max_steps):
                    page.mouse.wheel(0, step_px)
                    page.wait_for_timeout(200)
                    found = _pick()
                    if found is not None:
                        break
            if found is None:
                raise PlaywrightBlockedError(f"WB competitor row not found by nm_id: {nm!r}")
            row = found
        else:
            row_text = _row_text()
            row = page.get_by_text(row_text).first
            if row.count() == 0:
                raise PlaywrightBlockedError(f"WB competitor row not found by text: {row_text!r}")

    container = row.locator("xpath=ancestor-or-self::*[self::tr or self::div][1]")
    if container.count() == 0:
        container = row
    if click_sel.strip().lower() == "self":
        container.first.click(timeout=20_000)
    else:
        container.locator(click_sel).first.click(timeout=20_000)
    page.wait_for_load_state("domcontentloaded", timeout=60_000)


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
        context = browser.new_context(**_new_context_kwargs())
        page = context.new_page()
        try:
            page.goto("https://seller.wildberries.ru/", wait_until="domcontentloaded", timeout=60_000)

            # Heuristic: if already logged in, cabinet should show something; otherwise login form.
            # We cannot guarantee selectors; detect common auth fields.
            if page.locator("input[type='password']").count() > 0:
                meta["auth_mode"] = "password"
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
                # If password prompt remains, it's likely 2FA or captcha; automation cannot proceed.
                if page.locator("input[type='password']").count() > 0:
                    raise PlaywrightAuthError(
                        "WB login requires additional confirmation (2FA/captcha). "
                        "Generate a storage_state snapshot and set WB_PLAYWRIGHT_STORAGE_STATE_PATH."
                    )
            else:
                meta["auth_mode"] = "storage_state" if _storage_state_path() else "existing_session"

            # Flow: list -> open report -> choose period -> create excel -> download from reports list
            _open_report_from_list(page=page)
            _select_period(page=page, period=period)
            excel_bytes, gen_meta = _create_and_download_excel(page=page, period=period)
            meta.update(gen_meta)
            return excel_bytes, meta
        except PlaywrightBlockedError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Heuristic: treat as auth failure if page shows 401/forbidden hints.
            raise PlaywrightAuthError(str(exc)) from exc
        finally:
            context.close()
            browser.close()

