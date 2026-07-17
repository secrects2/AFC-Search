import logging
import os
import re
import urllib.parse
from pathlib import Path

from src.loader import Product, parse_price_value
from src.search.base import BaseSearchProvider, SearchResult

LOGGER = logging.getLogger(__name__)


class ShopeeSearchProvider(BaseSearchProvider):
    name = "shopee"

    SEARCH_INPUT_SELECTORS = (
        "input.shopee-searchbar-input__input",
        "input.shopee-searchbar-input",
        "input[placeholder*='搜尋']",
        "input[aria-label*='搜尋']",
    )

    def __init__(
        self,
        timeout: int = 15,
        profile_dir: str | Path = "data/browser_profiles/shopee",
        headless: bool = False,
        browser_channel: str = "chrome",
        cdp_url: str = "",
    ) -> None:
        self.timeout = timeout
        self.profile_dir = str(profile_dir or "")
        self.headless = headless
        self.browser_channel = browser_channel.strip()
        self.cdp_url = cdp_url.strip() or os.environ.get("SHOPEE_CDP_URL", "").strip()
        self._playwright_available = None
        self.last_status = "idle"
        self.last_error = ""

    def _profile_path(self) -> Path | None:
        if not self.profile_dir:
            return None
        path = Path(self.profile_dir).expanduser()
        return path if path.is_absolute() else Path.cwd() / path

    @staticmethod
    def _is_verification_page(url: str, body_text: str = "") -> bool:
        """Identify Shopee's traffic verification page without logging its ID."""
        url_lower = (url or "").casefold()
        body_lower = (body_text or "").casefold()
        return any(
            marker in url_lower or marker in body_lower
            for marker in ("/verify/", "traffic/error", "verify traffic", "頁面無法顯示")
        )

    @staticmethod
    def _is_manual_verification_body(body_text: str) -> bool:
        combined = (body_text or "").casefold()
        return any(
            marker in combined
            for marker in ("安全性驗證", "滑動以完成拼圖", "captcha")
        )

    @staticmethod
    def _canonical_item_url(href: str) -> str:
        absolute = urllib.parse.urljoin("https://shopee.tw/", href)
        parsed = urllib.parse.urlsplit(absolute)
        if not parsed.netloc:
            return ""
        return urllib.parse.urlunsplit(
            (parsed.scheme or "https", parsed.netloc, parsed.path, "", "")
        )

    @staticmethod
    def _extract_result_price(text: str) -> float | None:
        for match in re.finditer(r"\$\s*([0-9][0-9,]*(?:\.\d+)?)", text or ""):
            price = parse_price_value(match.group(1))
            if price is not None and 50 <= price <= 500_000:
                return price
        return None

    @staticmethod
    def _extract_result_title(text: str) -> str:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        for line in lines:
            if re.search(r"\$\s*[0-9]", line) or "已售出" in line:
                continue
            return line
        return lines[0] if lines else ""

    @classmethod
    def _find_search_input(cls, page):
        for selector in cls.SEARCH_INPUT_SELECTORS:
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible(timeout=1000):
                    return locator
            except Exception:
                continue
        return None

    @staticmethod
    def _select_cdp_page(context):
        shopee_pages = []
        for candidate in context.pages:
            try:
                if "shopee.tw" in (candidate.url or "").casefold():
                    shopee_pages.append(candidate)
            except Exception:
                continue

        for candidate in shopee_pages:
            try:
                if "/verify/" not in (candidate.url or "").casefold():
                    return candidate, False
            except Exception:
                return candidate, False

        if shopee_pages:
            return shopee_pages[0], False
        return context.new_page(), True

    @property
    def enabled(self) -> bool:
        if self._playwright_available is None:
            try:
                import playwright.sync_api
                self._playwright_available = True
            except ImportError:
                self._playwright_available = False
        return self._playwright_available

    def search(self, product: Product, max_results: int) -> list[SearchResult]:
        self.last_status = "started"
        self.last_error = ""
        if not self.enabled:
            self.last_status = "unavailable"
            return []

        keyword = product.product_name
        LOGGER.info("Executing Shopee Playwright search for: %s", keyword)
        results = []

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = None
            context = None
            page = None
            owns_browser = False
            owns_context = False
            owns_page = False
            try:
                if self.cdp_url:
                    browser = pw.chromium.connect_over_cdp(
                        self.cdp_url,
                        timeout=self.timeout * 1000,
                    )
                    if not browser.contexts:
                        raise RuntimeError("Chrome CDP has no browser context")
                    context = browser.contexts[0]
                    page, owns_page = self._select_cdp_page(context)
                    try:
                        page.bring_to_front()
                    except Exception:
                        pass

                    existing_body = ""
                    try:
                        existing_body = page.locator("body").inner_text(timeout=3000)
                    except Exception:
                        pass
                    if (
                        self._is_verification_page(page.url, existing_body)
                        or self._is_manual_verification_body(existing_body)
                    ):
                        self.last_status = "blocked"
                        self.last_error = "Shopee traffic verification page"
                        return []

                    search_input = self._find_search_input(page)
                    if search_input is None:
                        self.last_status = "error"
                        self.last_error = "Shopee search input not found"
                        return []

                    search_input.fill("")
                    search_input.fill(keyword)
                    search_input.press("Enter")
                    page.wait_for_timeout(3000)
                    response = None
                else:
                    browser_options = {
                        "viewport": {"width": 1366, "height": 900},
                        "locale": "zh-TW",
                        "timezone_id": "Asia/Taipei",
                        "user_agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        ),
                    }
                    profile_path = self._profile_path()
                    if profile_path:
                        profile_path.mkdir(parents=True, exist_ok=True)
                        context = pw.chromium.launch_persistent_context(
                            user_data_dir=str(profile_path),
                            headless=self.headless,
                            channel=self.browser_channel or None,
                            chromium_sandbox=True,
                            args=["--lang=zh-TW"],
                            **browser_options,
                        )
                    else:
                        browser = pw.chromium.launch(
                            headless=self.headless,
                            chromium_sandbox=True,
                        )
                        owns_browser = True
                        context = browser.new_context(**browser_options)
                    owns_context = True
                    page = context.new_page()
                    owns_page = True

                if not self.cdp_url:
                    url = f"https://shopee.tw/search?keyword={urllib.parse.quote(keyword)}"
                    response = page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=self.timeout * 1000,
                    )
                if response is not None and response.status >= 400:
                    self.last_status = "blocked"
                    self.last_error = f"Shopee HTTP {response.status}"
                    return []

                if not self.cdp_url:
                    page.wait_for_timeout(3000)

                body_text = ""
                try:
                    body_text = page.locator("body").inner_text(timeout=5000)
                except Exception:
                    pass
                if self._is_verification_page(page.url, body_text):
                    self.last_status = "blocked"
                    self.last_error = "Shopee traffic verification page"
                    LOGGER.warning(
                        "Shopee search blocked by traffic verification for '%s'; using fallback providers",
                        keyword,
                    )
                    return []

                if self._is_manual_verification_body(body_text):
                    self.last_status = "blocked"
                    self.last_error = "Shopee traffic verification page"
                    return []

                # Find all result cards that expose a canonical Shopee item URL.
                try:
                    page.wait_for_selector(
                        'a[href*="-i."]',
                        timeout=min(self.timeout * 1000, 10000),
                    )
                except Exception:
                    pass
                links = page.locator('a[href*="-i."]').all()
                seen_urls: set[str] = set()
                for link in links:
                    if len(results) >= max_results:
                        break

                    href = link.get_attribute("href")
                    if not href or '-i.' not in href:
                        continue

                    full_url = self._canonical_item_url(href)
                    if not full_url or full_url in seen_urls:
                        continue

                    card_text = link.inner_text().strip()
                    title = self._extract_result_title(card_text)
                    if not title or len(title) < 5:
                        continue

                    seen_urls.add(full_url)
                    results.append(SearchResult(
                        url=full_url,
                        product_name=title,
                        source=self.name,
                        platform="shopee",
                        rank=len(results) + 1,
                        found_price=self._extract_result_price(card_text),
                        raw_data={
                            "search_mode": "cdp_search_box" if self.cdp_url else "playwright_search",
                            "search_keyword": keyword,
                            "search_card_text": card_text[:1000],
                        },
                    ))

                self.last_status = "success" if results else "no_results"
            except Exception as exc:
                self.last_status = "error"
                self.last_error = str(exc)
                LOGGER.warning("Shopee search failed for '%s': %s", keyword, exc)
            finally:
                # A CDP connection belongs to the user's running Chrome. Only
                # close the page created by this provider; never close the
                # user's context, browser, or existing tabs.
                if owns_page and page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass
                if owns_context and context is not None:
                    context.close()
                if owns_browser and browser is not None:
                    browser.close()
                
        return results
