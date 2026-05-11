"""
Generate Playwright storage_state for WB Seller cabinet.

Usage (local, interactive):
  cd backend
  python scripts/generate_wb_storage_state.py --out ./wb_storage_state.json

Then copy the resulting file to the server and set:
  WB_PLAYWRIGHT_STORAGE_STATE_PATH=/path/to/wb_storage_state.json

SECURITY: storage_state contains auth cookies/session data. Treat as a secret. Do NOT commit.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate WB storage_state for Playwright automation.")
    parser.add_argument("--out", required=True, help="Output path for storage_state JSON")
    parser.add_argument(
        "--url",
        default="https://seller.wildberries.ru/",
        help="Start URL (default: WB seller cabinet root)",
    )
    args = parser.parse_args()

    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Lazy import: optional dependency in some deployments.
    from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

    with sync_playwright() as p:
        # Headed mode: user passes 2FA/captcha manually once.
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(str(args.url), wait_until="domcontentloaded", timeout=60_000)

        print()
        print("1) Пройди логин/2FA вручную в открывшемся окне.")
        print("2) Убедись, что ты залогинен (виден кабинет WB).")
        print("3) Вернись в терминал и нажми Enter, чтобы сохранить storage_state.")
        print()
        try:
            input()
        except KeyboardInterrupt:
            print("\nCancelled.")
            context.close()
            browser.close()
            return 1

        context.storage_state(path=str(out_path))
        context.close()
        browser.close()

    # Best-effort: set strict perms locally; server may manage secrets differently.
    try:
        os.chmod(out_path, 0o600)
    except OSError:
        pass

    print(f"ok: storage_state saved to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

