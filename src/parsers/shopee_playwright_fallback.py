"""ShopeePlaywrightFallbackProvider — last-resort Playwright-based extraction.

Uses Playwright with normal browser settings (no stealth, no bypass).
If Shopee blocks the request (language page, 403, captcha, "頁面無法顯示"),
it marks the result as blocked and does NOT retry.
"""
from __future__ import annotations

import logging
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

# Patterns that indicate Shopee has blocked us
BLOCKED_INDICATORS = [
    "選擇語言",
    "頁面無法顯示",
    "發生錯誤",
    "請登入",
    "captcha",
    "verify",
    "robot",
]


class ShopeePlaywrightFallbackProvider(ShopeePriceProvider):
    """Last-resort provider using Playwright — no stealth, no bypass."""

    name = "playwright"

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout
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
        """Open URL in Playwright, extract price from rendered DOM."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1366, "height": 900},
                locale="zh-TW",
                timezone_id="Asia/Taipei",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                extra_http_headers={
                    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
                },
            )

            page = context.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            except Exception as exc:
                browser.close()
                result.status = "error"
                result.error_message = f"Navigation failed: {exc}"
                return result

            # Wait a moment for dynamic content
            try:
                page.wait_for_selector(
                    "div[class*='price'], span[class*='price']",
                    timeout=10000,
                )
            except Exception:
                page.wait_for_timeout(3000)

            # Check if blocked
            body_text = ""
            try:
                body_text = (page.inner_text("body") or "")[:3000]
            except Exception:
                pass

            title = page.title() or ""

            if self._is_blocked(title, body_text):
                browser.close()
                result.status = "blocked"
                result.title = title
                result.error_message = "Shopee blocked: language page / 403 / captcha"
                LOGGER.info("Shopee Playwright blocked: %s", title[:60])
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

            browser.close()

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
