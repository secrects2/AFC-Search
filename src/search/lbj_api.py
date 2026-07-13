from __future__ import annotations

import logging
import re
import time
import urllib.parse
import requests
from dataclasses import dataclass

from bs4 import BeautifulSoup

from src.loader import Product, parse_price_value
from src.search.base import BaseSearchProvider, SearchResult

LOGGER = logging.getLogger(__name__)

LBJ_BASE = "https://www.lbj.tw/BJ"


@dataclass(frozen=True)
class LbjListing:
    title: str
    url: str
    price: float | None
    price_text: str
    platform: str = "lbj"
    seller: str = ""
    product_id: str = ""
    search_url: str = ""


def _detect_platform_from_site(site: str) -> str:
    value = (site or "").lower()
    if "蝦皮" in site or "shopee" in value:
        return "shopee"
    if "momo" in value or "富邦購物" in site:
        return "momo"
    if "pchome" in value:
        return "pchome"
    if "yahoo" in value or "雅虎" in site:
        return "yahoo"
    if "露天" in site or "ruten" in value:
        return "ruten"
    if "樂天" in site or "rakuten" in value:
        return "rakuten"
    if "東森" in site or "etmall" in value:
        return "etmall"
    return "lbj"


def parse_lbj_html(
    html: str,
    query_url: str,
    max_results: int = 100,
) -> list[LbjListing]:
    """Parse each product card from an LBJ comparison search page."""
    soup = BeautifulSoup(html, "html.parser")
    listings: list[LbjListing] = []
    seen: set[str] = set()

    # Each card has two buttons with the same data-pid. The first button
    # carries the complete product metadata, so deduplicate by data-pid.
    for item in soup.select("[data-pid][data-gn][data-url][data-price]"):
        product_id = (item.get("data-pid") or "").strip()
        title = " ".join((item.get("data-gn") or "").split())
        href = urllib.parse.urljoin(query_url, (item.get("data-url") or "").strip())
        price_text = (item.get("data-price") or "").strip()
        site = " ".join((item.get("data-site") or "").split())
        key = product_id or f"{href}|{title}"

        if not title or not href or key in seen:
            continue
        seen.add(key)

        listings.append(
            LbjListing(
                title=title,
                url=href,
                price=parse_price_value(price_text),
                price_text=price_text,
                platform=_detect_platform_from_site(site),
                seller=site,
                product_id=product_id,
                search_url=query_url,
            )
        )
        if len(listings) >= max_results:
            break

    return listings


def search_lbj_listings(
    keyword: str,
    max_results: int = 20,
    timeout: int = 15,
    delay_seconds: float = 1.0,
) -> list[LbjListing]:
    LOGGER.info("Executing LBJ search for keyword: %s", keyword)
    if delay_seconds > 0:
        time.sleep(delay_seconds)

    url = f"{LBJ_BASE}/Query.aspx?k={urllib.parse.quote(keyword)}"
    
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
        LOGGER.warning("LBJ search failed for keyword '%s': %s", keyword, exc)
        return []

    return parse_lbj_html(html, url, max_results=max_results)


class LbjSearchProvider(BaseSearchProvider):
    """Search provider using LBJ directly."""

    name = "lbj"

    def __init__(self, platforms: list[str] | None = None) -> None:
        self.platforms = platforms

    def search(self, product: Product, max_results: int) -> list[SearchResult]:
        if not product.product_name:
            return []

        listings = search_lbj_listings(
            keyword=product.product_name,
            # LBJ's page is a comparison table; retain the whole page even
            # when the general discovery limit is smaller.
            max_results=max(max_results, 100),
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
                    seller=lst.seller,
                    raw_data={
                        "source": "lbj_search",
                        "lbj_product_id": lst.product_id,
                        "lbj_search_url": lst.search_url,
                        "price_text": lst.price_text,
                        "lbj_price": lst.price,
                        "site": lst.seller,
                    },
                )
            )

        return results
