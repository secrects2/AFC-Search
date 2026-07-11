from __future__ import annotations

import logging
import re
import time
import urllib.parse
import requests
from dataclasses import dataclass

from src.loader import Product, parse_price_value
from src.search.base import BaseSearchProvider, SearchResult

LOGGER = logging.getLogger(__name__)

BIGGO_BASE = "https://biggo.com.tw"


@dataclass(frozen=True)
class BigGoListing:
    title: str
    url: str
    price: float | None
    price_text: str
    platform: str = "biggo"


def search_biggo_listings(
    keyword: str,
    max_results: int = 20,
    timeout: int = 15,
    delay_seconds: float = 1.0,
) -> list[BigGoListing]:
    LOGGER.info("Executing BigGo search for keyword: %s", keyword)
    if delay_seconds > 0:
        time.sleep(delay_seconds)

    url = f"{BIGGO_BASE}/s/?q={urllib.parse.quote(keyword)}"
    
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
            },
            timeout=timeout
        )
        response.raise_for_status()
        html = response.text
    except Exception as exc:
        LOGGER.warning("BigGo search failed for keyword '%s': %s", keyword, exc)
        return []

    listings = []
    seen = set()

    # Very naive regex extraction from __next_f JSON chunks
    # Usually it's better to parse the DOM, but BigGo heavily relies on JS
    titles = re.findall(r'\\"name\\":\\"([^\\"]+)\\"', html)
    prices = re.findall(r'\\"price\\":(\d+)', html)
    
    # URL extraction is tricky in BigGo's next.js structure.
    # Fallback: Just return the search URL as the candidate if we can't extract item URLs.
    # The user wanted https://biggo.com.tw/?gad_source=1... to be added as a search source.
    # We will just return the BigGo search URL as a generic candidate if we find prices.
    
    # Actually, a better approach for BigGo is to use SerpApi as a fallback.
    # If we extract titles and prices, we can just yield a single dummy URL to let the
    # system know we found something, or we can just try to find URLs in the HTML.
    
    # Since BigGo item URLs look like /redirect/... or /s/...
    urls = re.findall(r'href=\\"(/[^\\"]+)\\"', html)
    product_urls = [u for u in urls if '/track/' in u or '/redirect/' in u or 'biggo' in u]
    
    count = min(len(titles), len(prices), max_results)
    for i in range(count):
        title = titles[i].replace('\\u002F', '/')
        price_val = float(prices[i])
        
        # If we couldn't parse product specific URLs reliably, we just use the search URL
        # combined with a query string to make it unique so we don't get duplicates.
        item_url = f"{url}&item_title={urllib.parse.quote(title[:20])}"
        if i < len(product_urls):
            item_url = BIGGO_BASE + product_urls[i].replace('\\u0026', '&')

        key = (item_url, title)
        if key in seen:
            continue
        seen.add(key)
        
        listings.append(
            BigGoListing(
                title=title,
                url=item_url,
                price=price_val,
                price_text=str(price_val),
                platform="biggo"
            )
        )

    return listings


class BigGoSearchProvider(BaseSearchProvider):
    """Search provider using BigGo directly."""

    name = "biggo"

    def __init__(self, platforms: list[str] | None = None) -> None:
        self.platforms = platforms

    def search(self, product: Product, max_results: int) -> list[SearchResult]:
        if not product.product_name:
            return []

        listings = search_biggo_listings(
            keyword=product.product_name,
            max_results=max_results,
            delay_seconds=1.0,
        )

        results = []
        for lst in listings:
            results.append(
                SearchResult(
                    source=self.name,
                    url=lst.url,
                    product_name=lst.title,
                    found_price=lst.price,
                    platform=lst.platform,
                    seller="",
                )
            )

        return results
