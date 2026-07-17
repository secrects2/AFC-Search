"""Create a persistent local browser profile for Shopee.

Run this once and manually complete Shopee login, OTP, and any CAPTCHA in the
visible browser window. The password is entered only in the browser; this tool
does not read, print, or store credentials itself.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the persistent Shopee login profile."
    )
    parser.add_argument(
        "--url",
        default="https://shopee.tw/",
        help="URL to open while signing in (default: Shopee homepage)",
    )
    parser.add_argument(
        "--profile-dir",
        default="",
        help="Optional profile directory; defaults to config.yaml",
    )
    return parser.parse_args()


def _resolve_profile_dir(cli_value: str) -> Path:
    if cli_value:
        configured = Path(cli_value).expanduser()
    else:
        try:
            from src.config import load_config

            configured = Path(
                load_config(PROJECT_ROOT / "config.yaml").shopee_profile_dir
            )
        except Exception:
            configured = Path("data/browser_profiles/shopee")
    return configured if configured.is_absolute() else PROJECT_ROOT / configured


def main() -> None:
    args = _parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: Playwright is not installed.")
        print("Please run: pip install playwright && playwright install chromium")
        sys.exit(1)

    profile_dir = _resolve_profile_dir(args.profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("  Shopee Browser Profile Setup")
    print("=" * 60)
    print(f"Profile directory: {profile_dir}")
    print(f"Opening: {args.url}")
    print()
    print("In the browser window:")
    print("  1. Log in to your Shopee account.")
    print("  2. Complete any OTP or CAPTCHA shown by Shopee.")
    print("  3. Confirm that the page loads normally.")
    print()
    print("Return here and press Enter after login is complete.")
    print("=" * 60)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            locale="zh-TW",
            timezone_id="Asia/Taipei",
            viewport={"width": 1366, "height": 900},
            args=["--lang=zh-TW"],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            print(f"Navigation warning: {exc}")
            print("The browser remains open; navigate manually if needed.")

        input(">>> Press Enter after you have finished logging in... ")
        print("Saving profile and closing browser...")
        context.close()

    print(f"Profile saved: {profile_dir}")
    print("The Shopee search and price providers will reuse this profile.")


if __name__ == "__main__":
    main()
