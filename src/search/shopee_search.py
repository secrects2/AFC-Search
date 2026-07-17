import logging
import urllib.parse
from pathlib import Path

from src.loader import Product
from src.search.base import BaseSearchProvider, SearchResult

LOGGER = logging.getLogger(__name__)

class ShopeeSearchProvider(BaseSearchProvider):
    name = "shopee"

    def __init__(
        self,
        timeout: int = 15,
        profile_dir: str | Path = "data/browser_profiles/shopee",
        headless: bool = False,
    ) -> None:
        self.timeout = timeout
        self.profile_dir = str(profile_dir or "")
        self.headless = headless
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
            try:
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
                        args=["--lang=zh-TW"],
                        **browser_options,
                    )
                else:
                    browser = pw.chromium.launch(headless=self.headless)
                    context = browser.new_context(**browser_options)

                page = context.new_page()
                url = f"https://shopee.tw/search?keyword={urllib.parse.quote(keyword)}"
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)

                # Wait a bit for JS to load
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

                # Find all <a> tags that look like shopee item links
                links = page.locator('a[href*="-i."]').all()
                for link in links:
                    if len(results) >= max_results:
                        break
                        
                    href = link.get_attribute("href")
                    if not href or '-i.' not in href:
                        continue
                    
                    # Try to get inner text
                    text = link.inner_text().strip()
                    lines = text.split('\n')
                    title = lines[0] if lines else ""
                    
                    if not title or len(title) < 5:
                        continue
                        
                    # Filter out ads if necessary, but we can keep them
                    full_url = "https://shopee.tw" + href if href.startswith('/') else href
                    
                    results.append(SearchResult(
                        url=full_url,
                        product_name=title,
                        source=self.name,
                        platform="shopee",
                    ))

                self.last_status = "success" if results else "no_results"
            except Exception as exc:
                self.last_status = "error"
                self.last_error = str(exc)
                LOGGER.warning("Shopee search failed for '%s': %s", keyword, exc)
            finally:
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()
                
        return results
