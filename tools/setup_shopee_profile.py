"""Interactive tool to set up a persistent Shopee browser profile.

Run this once before using the Shopee parser:

    python tools/setup_shopee_profile.py

This opens a visible Chromium browser and navigates to shopee.tw.
Manually select your preferred language and region, then press Enter
in the terminal to save the profile and close the browser.

Subsequent Shopee parser runs will reuse the saved profile so that
the language selection page no longer appears.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path so src.* imports work when run directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: Playwright is not installed.")
        print("Please run:  pip install playwright && playwright install chromium")
        sys.exit(1)

    profile_dir = PROJECT_ROOT / "data" / "browser_profiles" / "shopee"
    profile_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("  Shopee Browser Profile Setup")
    print("=" * 60)
    print()
    print(f"  Profile directory: {profile_dir}")
    print()
    print("  A Chromium browser window will open and navigate to shopee.tw.")
    print("  Please manually:")
    print("    1. Select your preferred language (繁體中文)")
    print("    2. Select your region if prompted")
    print("    3. Wait for the homepage to fully load")
    print()
    print("  When finished, come back here and press Enter to save & close.")
    print("=" * 60)
    print()

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

        print("Opening https://shopee.tw/ ...")
        try:
            page.goto("https://shopee.tw/", wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            print(f"Warning: Navigation had an issue: {exc}")
            print("The browser is still open — you can navigate manually.")

        print()
        input(">>> Press Enter when you have finished selecting the language... ")
        print()
        print("Saving profile and closing browser...")

        context.close()

    print()
    print("Done! Profile saved to:")
    print(f"  {profile_dir}")
    print()
    print("The Shopee parser will now reuse this profile automatically.")
    print("If Shopee shows the language page again, re-run this script.")


if __name__ == "__main__":
    main()
