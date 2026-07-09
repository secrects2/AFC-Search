import logging
import urllib.parse
from src.loader import Product
from src.search.base import BaseSearchProvider, SearchResult
import time

LOGGER = logging.getLogger(__name__)

class ShopeeSearchProvider(BaseSearchProvider):
    name = "shopee"

    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout
        self._playwright_available = None

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
        if not self.enabled:
            return []

        keyword = product.product_name
        LOGGER.info("Executing Shopee Playwright search for: %s", keyword)
        results = []

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
            )
            page = context.new_page()

            url = f"https://shopee.tw/search?keyword={urllib.parse.quote(keyword)}"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                
                # Wait a bit for JS to load
                page.wait_for_timeout(3000)
                
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
                    
            except Exception as exc:
                LOGGER.warning("Shopee search failed for '%s': %s", keyword, exc)
            finally:
                browser.close()
                
        return results
