"""ShopeePlaywrightFallbackProvider — last-resort Playwright-based extraction.

Uses Playwright with a persistent browser profile so that language/region
preferences are preserved across runs.  Users must run
``tools/setup_shopee_profile.py`` once to initialise the profile.

If Shopee blocks the request (403, captcha, login wall) the result is
marked as blocked and is NOT retried.  This provider does NOT attempt to
bypass any anti-bot measures.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from src.loader import parse_price_value
from src.parsers.shopee_provider import (
    ShopeePriceProvider,
    ShopeePriceResult,
    parse_shopee_url,
    _now_iso,
)
from src.utils import sanitize_filename
from src.visual_price import VisualPriceExtractor

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language page detection
# ---------------------------------------------------------------------------

# Indicators that the page is a Shopee *language selection* page.
# We treat this separately from generic blocking because the fix is simple:
# run tools/setup_shopee_profile.py once to set the language preference.
LANGUAGE_PAGE_INDICATORS = [
    "選擇語言",
    "choose language",
    "select language",
    "ภาษาไทย",
    "tiếng việt",
]

# If *multiple* of these tokens appear together on the same page it is very
# likely a language picker page (e.g. the Shopee homepage language overlay
# that lists "繁體中文 · English · Bahasa Indonesia · …").
LANGUAGE_LIST_TOKENS = [
    "繁體中文",
    "english",
    "bahasa",
]

# ---------------------------------------------------------------------------
# Blocked page detection
# ---------------------------------------------------------------------------

BLOCKED_INDICATORS = [
    "頁面無法顯示",
    "發生錯誤",
    "請登入",
    "access denied",
    "403",
    "captcha",
    "verify",
    "robot",
    "驗證",
]


def is_shopee_language_page(page) -> bool:
    """Return True if *page* is the Shopee language / region selection page.

    This inspects the page title and visible body text.  The function is
    intentionally conservative: it should NOT match normal product pages.
    """
    title = ""
    body = ""
    try:
        title = (page.title() or "").lower()
    except Exception:
        pass
    try:
        body = (page.inner_text("body") or "")[:3000].lower()
    except Exception:
        pass

    combined = title + " " + body

    # Direct keyword match
    if any(indicator in combined for indicator in LANGUAGE_PAGE_INDICATORS):
        return True

    # Heuristic: if at least 2 of the language-list tokens appear, the page
    # is almost certainly the language picker overlay.
    matches = sum(1 for token in LANGUAGE_LIST_TOKENS if token in combined)
    if matches >= 2:
        return True

    return False


class ShopeePlaywrightFallbackProvider(ShopeePriceProvider):
    """Last-resort provider using Playwright with a persistent browser profile."""

    name = "playwright"

    def __init__(
        self,
        timeout: int = 30,
        profile_dir: str = "",
        headless: bool = False,
        browser_channel: str = "chrome",
    ) -> None:
        self.timeout = timeout
        self.profile_dir = profile_dir or os.environ.get(
            "SHOPEE_PROFILE_DIR", "data/browser_profiles/shopee"
        )
        self.headless = headless if profile_dir else (
            os.environ.get("SHOPEE_HEADLESS", "false").lower() in ("true", "1", "yes")
        )
        self.browser_channel = browser_channel or os.environ.get(
            "SHOPEE_BROWSER_CHANNEL", "chrome"
        )
        self._playwright_available: bool | None = None

    @property
    def enabled(self) -> bool:
        if self._playwright_available is None:
            try:
                import playwright.sync_api  # noqa: F401
                self._playwright_available = True
            except ImportError:
                self._playwright_available = False
        return self._playwright_available

    def get_product_price(self, url: str) -> ShopeePriceResult:
        shop_id, item_id = parse_shopee_url(url)
        result = ShopeePriceResult(
            url=url,
            shop_id=shop_id,
            item_id=item_id,
            source=self.name,
            checked_at=_now_iso(),
        )

        if not self.enabled:
            result.status = "error"
            result.error_message = "Playwright not installed"
            return result

        if not shop_id or not item_id:
            result.status = "error"
            result.error_message = f"Cannot parse Shopee URL: {url}"
            return result

        try:
            return self._fetch_with_playwright(url, result)
        except Exception as exc:
            LOGGER.warning("Shopee Playwright fallback failed: %s", exc)
            result.status = "error"
            result.error_message = str(exc)
            return result

    def _fetch_with_playwright(
        self, url: str, result: ShopeePriceResult,
    ) -> ShopeePriceResult:
        """Open URL in Playwright persistent context, extract price from DOM."""
        from playwright.sync_api import sync_playwright

        # Resolve profile directory relative to project root
        profile_path = Path(self.profile_dir)
        if not profile_path.is_absolute():
            profile_path = Path.cwd() / profile_path
        profile_path.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                headless=self.headless,
                channel=self.browser_channel or None,
                chromium_sandbox=True,
                viewport={"width": 1366, "height": 900},
                locale="zh-TW",
                timezone_id="Asia/Taipei",
                args=["--lang=zh-TW"],
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )

            page = context.pages[0] if context.pages else context.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            except Exception as exc:
                context.close()
                result.status = "error"
                result.error_message = f"Navigation failed: {exc}"
                return result

            # After navigation, Shopee may have redirected and the original
            # page reference may be stale.  Always use the latest active page.
            try:
                pages = context.pages
                page = pages[-1] if pages else page
            except Exception:
                pass

            # Wait a moment for dynamic content
            try:
                page.wait_for_selector(
                    "div[class*='price'], span[class*='price']",
                    timeout=10000,
                )
            except Exception:
                try:
                    page.wait_for_timeout(3000)
                except Exception:
                    pass

            # --- Language page detection (separate from generic blocking) ---
            if is_shopee_language_page(page):
                title = ""
                try:
                    title = page.title() or ""
                except Exception:
                    pass
                result.status = "language_required"
                result.title = title
                result.error_message = (
                    "Shopee opened language selection page. "
                    "Please run tools/setup_shopee_profile.py once."
                )
                LOGGER.warning(
                    "Shopee language page detected. Run tools/setup_shopee_profile.py to fix."
                )
                context.close()
                return result

            # --- Generic blocked detection ---
            body_text = ""
            try:
                body_text = (page.inner_text("body") or "")[:3000]
            except Exception:
                pass

            title = ""
            try:
                title = page.title() or ""
            except Exception:
                pass

            if self._is_blocked(title, body_text):
                result.status = "blocked"
                result.title = title
                result.error_message = "Shopee blocked: 403 / captcha / login wall"
                LOGGER.info("Shopee Playwright blocked: %s", title[:60])
                context.close()
                return result

            # Extract title
            if "|" in title:
                result.title = title.split("|")[0].strip()
            else:
                result.title = title

            # Try to extract price from DOM
            result.price = self._extract_price_from_dom(page)
            if result.price is None and body_text:
                result.price = self._extract_price_from_text(body_text)

            # If still None, take screenshot and try OCR
            if result.price is None:
                try:
                    screenshot_dir = Path("output/screenshots")
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    screenshot_path = screenshot_dir / f"{sanitize_filename(result.title or 'shopee')}.png"
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    
                    extractor = VisualPriceExtractor()
                    if extractor.enabled:
                        visual_result = extractor.extract_from_screenshot(str(screenshot_path), platform="shopee")
                        if visual_result.price is not None:
                            result.price = visual_result.price
                            result.raw_data = result.raw_data or {}
                            result.raw_data["price_source"] = "visual_ocr"
                            result.raw_data["visual_price_used"] = True
                            result.raw_data["visual_price_method"] = visual_result.method
                            result.raw_data["visual_price_confidence"] = visual_result.confidence
                            result.raw_data["visual_price_raw_text"] = visual_result.raw_text
                except Exception as e:
                    LOGGER.warning("Shopee Playwright OCR fallback failed: %s", e)

            context.close()

            result.status = "ok" if result.price is not None else "price_unknown"
            if result.price is None:
                result.error_message = "Price not found in rendered page"

            return result

    @staticmethod
    def _is_blocked(title: str, body_text: str) -> bool:
        """Check if the page indicates Shopee has blocked us."""
        combined = (title + " " + body_text).lower()
        return any(indicator in combined for indicator in BLOCKED_INDICATORS)

    @staticmethod
    def _extract_price_from_dom(page) -> float | None:
        """Extract price from rendered DOM elements."""
        selectors = [
            "div[class*='price'] span",
            "span[class*='price']",
            "div[class*='Price'] span",
            "span[class*='Price']",
        ]
        for selector in selectors:
            try:
                elements = page.query_selector_all(selector)
                for el in elements:
                    text = (el.inner_text() or "").strip()
                    match = re.search(r'\$?\s*([0-9][0-9,]+(?:\.\d+)?)', text)
                    if match:
                        price = parse_price_value(match.group(1))
                        if price and 50 <= price <= 500_000:
                            return price
            except Exception:
                continue
        return None

    @staticmethod
    def _extract_price_from_text(text: str) -> float | None:
        """Extract price from body text using regex."""
        for match in re.finditer(r'\$\s*([0-9][0-9,]+(?:\.\d+)?)', text):
            price = parse_price_value(match.group(1))
            if price and 50 <= price <= 500_000:
                return price
        return None
