from __future__ import annotations

import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from bs4 import BeautifulSoup

from src.loader import Product, parse_price_value
from src.matcher import match_score
from src.search.base import BaseSearchProvider, SearchResult

LOGGER = logging.getLogger(__name__)

FINDPRICE_BASE = "https://www.findprice.com.tw"


@dataclass(frozen=True)
class FindPriceListing:
    title: str
    url: str
    platform: str
    seller: str
    price: float | None
    price_text: str
    image_url: str = ""


def _detect_platform_from_merchant(text: str, url: str = "") -> str:
    value = f"{text} {url}".lower()
    if "蝦皮" in text or "shopee" in value:
        return "shopee"
    if "momo" in value or "momo購物" in text:
        return "momo"
    if "pchome" in value or "pchome" in text.lower():
        return "pchome"
    if "yahoo" in value or "雅虎" in text:
        return "yahoo"
    if "露天" in text or "ruten" in value:
        return "ruten"
    if "樂天" in text or "rakuten" in value:
        return "rakuten"
    if "coupang" in value or "酷澎" in text:
        return "coupang"
    return "findprice"


def _absolute_url(href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return FINDPRICE_BASE + href
    return href


def _unwrap_url(href: str) -> str:
    absolute = _absolute_url(href)
    parsed = urllib.parse.urlparse(absolute)
    if "url.aspx" not in parsed.path.lower():
        return absolute

    query = urllib.parse.parse_qs(parsed.query)
    real_url = query.get("u", [""])[0]
    return _absolute_url(real_url) if real_url else absolute


def _parse_price_text(text: str) -> float | None:
    prices = []
    for raw in re.findall(r"\$?\s*([0-9][0-9,]*(?:\.\d+)?)", text):
        price = parse_price_value(raw)
        if price is not None and 50 <= price <= 500_000:
            prices.append(float(price))
    return min(prices) if prices else None


def _row_to_listing(row) -> FindPriceListing | None:
    title_link = row.select_one(".GoodsGname a[href]") or row.select_one("a[href*='/go/']")
    if not title_link:
        return None

    title = (title_link.get("title") or title_link.get_text(" ", strip=True) or "").strip()
    if not title:
        return None

    href = title_link.get("href", "")
    if not href:
        return None

    price_node = row.select_one(".rec-price-20")
    price_text = price_node.get_text(" ", strip=True) if price_node else ""
    merchant_node = row.select_one(".GoodsMname")
    seller = merchant_node.get_text(" ", strip=True).replace("\xa0", " ") if merchant_node else ""
    image_node = row.select_one("img.searchImg")
    image_url = _absolute_url(image_node.get("src", "")) if image_node else ""
    url = _unwrap_url(href)
    platform = _detect_platform_from_merchant(seller, url)

    return FindPriceListing(
        title=" ".join(title.split()),
        url=url,
        platform=platform,
        seller=" ".join(seller.split()),
        price=_parse_price_text(price_text),
        price_text=" ".join(price_text.split()),
        image_url=image_url,
    )


def parse_findprice_html(html: str, max_results: int = 20) -> list[FindPriceListing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[FindPriceListing] = []
    seen: set[tuple[str, str, str]] = set()

    for row in soup.select(".divPromoGoods, .divGoods"):
        listing = _row_to_listing(row)
        if not listing:
            continue
        key = (listing.url, listing.title, listing.platform)
        if key in seen:
            continue
        seen.add(key)
        listings.append(listing)
        if len(listings) >= max_results:
            break

    return listings


def search_findprice_listings(
    keyword: str,
    max_results: int = 20,
    timeout: int = 15,
    delay_seconds: float = 1.0,
) -> list[FindPriceListing]:
    LOGGER.info("Executing FindPrice search for keyword: %s", keyword)
    if delay_seconds > 0:
        time.sleep(delay_seconds)

    url = f"{FINDPRICE_BASE}/g/{urllib.parse.quote(keyword)}"
    request = urllib.request.Request(
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
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        LOGGER.warning("FindPrice search failed for keyword '%s': %s", keyword, exc)
        return []

    listings = parse_findprice_html(html, max_results=max_results)
    LOGGER.info("FindPrice found %d candidate listings for %s", len(listings), keyword)
    return listings


def find_best_findprice_listing(
    keyword: str,
    expected_title: str = "",
    preferred_platform: str = "",
    min_score: int = 50,
    timeout: int = 15,
) -> FindPriceListing | None:
    listings = [
        listing
        for listing in search_findprice_listings(keyword, max_results=30, timeout=timeout)
        if listing.price is not None
    ]
    if not listings:
        return None

    scored: list[tuple[int, FindPriceListing]] = []
    for listing in listings:
        base_score = match_score(keyword, listing.title)
        if expected_title:
            base_score = max(base_score, match_score(expected_title, listing.title))
        if preferred_platform and listing.platform == preferred_platform:
            base_score += 20
        scored.append((base_score, listing))

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]
    if best_score < min_score:
        LOGGER.info(
            "FindPrice best match below threshold: score=%d keyword=%s title=%s",
            best_score,
            keyword,
            best.title,
        )
        return None
    return best


class FindPriceProvider(BaseSearchProvider):
    """Search implementation using FindPrice public comparison pages."""

    name = "findprice"

    def search(self, product: Product, max_results: int) -> list[SearchResult]:
        listings = search_findprice_listings(product.product_name, max_results=max_results)
        results: list[SearchResult] = []
        for rank, listing in enumerate(listings, start=1):
            results.append(
                SearchResult(
                    product_name=listing.title,
                    url=listing.url,
                    snippet=listing.price_text,
                    platform=listing.platform,
                    source=self.name,
                    rank=rank,
                    found_price=listing.price,
                    seller=listing.seller,
                )
            )
        return results
