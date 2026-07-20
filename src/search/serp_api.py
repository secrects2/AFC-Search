"""SerpAPI search provider.

Uses the SerpAPI REST API (https://serpapi.com) to search Google
for product listings on Taiwan e-commerce platforms.
Free tier: 250 searches/month, no credit card required.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from src.loader import Product
from src.search.base import BaseSearchProvider, SearchResult

LOGGER = logging.getLogger(__name__)

SERP_ENDPOINT = "https://serpapi.com/search.json"

# Platform detection: URL pattern -> platform name
PLATFORM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"shopee\.tw", re.IGNORECASE), "shopee"),
    (re.compile(r"momo\.com\.tw|momoshop\.com\.tw", re.IGNORECASE), "momo"),
    (re.compile(r"vivatv\.com\.tw", re.IGNORECASE), "viva"),
    (re.compile(r"tw\.bid\.yahoo\.com|tw\.buy\.yahoo\.com|yahoo\.com\.tw", re.IGNORECASE), "yahoo"),
    (re.compile(r"24h\.pchome\.com\.tw|ecshweb\.pchome\.com\.tw|pchome\.com\.tw", re.IGNORECASE), "pchome"),
    (re.compile(r"ruten\.com\.tw", re.IGNORECASE), "ruten"),
    (re.compile(r"rakuten\.com\.tw", re.IGNORECASE), "rakuten"),
    (re.compile(r"coupang\.onelink\.me|coupang\.com", re.IGNORECASE), "coupang"),
    (re.compile(r"biggo\.com\.tw", re.IGNORECASE), "biggo"),
    (re.compile(r"lbj\.tw", re.IGNORECASE), "lbj"),
]

# URL patterns that indicate a NON-product page (category, search, collection, etc.)
_NON_PRODUCT_PATTERNS: list[re.Pattern[str]] = [
    # Yahoo: search, category, collection, rushbuy, activity, event pages
    re.compile(r"yahoo\.com.*/search", re.IGNORECASE),
    re.compile(r"yahoo\.com.*/category/", re.IGNORECASE),
    re.compile(r"yahoo\.com.*/smartcollection", re.IGNORECASE),
    re.compile(r"yahoo\.com.*/rushbuy", re.IGNORECASE),
    re.compile(r"yahoo\.com.*/activity", re.IGNORECASE),
    re.compile(r"yahoo\.com.*/event", re.IGNORECASE),
    # PChome: search, category, store pages
    re.compile(r"pchome\.com\.tw/search", re.IGNORECASE),
    re.compile(r"pchome\.com\.tw.*/category", re.IGNORECASE),
    re.compile(r"pchome\.com\.tw.*/store/", re.IGNORECASE),
    # Shopee: search, collection pages
    re.compile(r"shopee\.tw/search", re.IGNORECASE),
    re.compile(r"shopee\.tw/collection/", re.IGNORECASE),
    re.compile(r"shopee\.tw/mall/", re.IGNORECASE),
    # Momo: search, category
    re.compile(r"momo\.com\.tw.*/search", re.IGNORECASE),
    re.compile(r"momo\.com\.tw.*/category", re.IGNORECASE),
    re.compile(r"momo\.com\.tw.*/cateSearch", re.IGNORECASE),
    # Ruten: search
    re.compile(r"ruten\.com\.tw/find/", re.IGNORECASE),
    re.compile(r"ruten\.com\.tw/category/", re.IGNORECASE),
    # Generic: any URL ending with just a query string containing common search params
    re.compile(r"[?&]keyword=", re.IGNORECASE),
    re.compile(r"[?&]q=", re.IGNORECASE),
]


def detect_platform(url: str) -> str:
    """Detect e-commerce platform from URL."""
    for pattern, platform in PLATFORM_PATTERNS:
        if pattern.search(url):
            return platform
    return "other"


def is_product_page(url: str) -> bool:
    """Check if a URL is likely a product detail page (not search/category/collection)."""
    for pattern in _NON_PRODUCT_PATTERNS:
        if pattern.search(url):
            return False
    return True


def _build_site_restriction(platforms: list[str]) -> str:
    """Build OR-joined site: restriction for Google query."""
    site_map = {
        "shopee": "shopee.tw",
        "momo": "momo.com.tw",
        "viva": "vivatv.com.tw",
        "yahoo": "tw.buy.yahoo.com",
        "pchome": "24h.pchome.com.tw",
        "ruten": "ruten.com.tw",
        "rakuten": "rakuten.com.tw",
        "biggo": "biggo.com.tw",
        "lbj": "lbj.tw",
    }
    sites = [f"site:{site_map[p]}" for p in platforms if p in site_map]
    if not sites:
        return ""
    return " " + " OR ".join(sites)


class SerpAPIProvider(BaseSearchProvider):
    """Search provider using SerpAPI (Google Search)."""

    name = "serpapi"

    def __init__(
        self,
        api_key: str,
        platforms: list[str],
        timeout: float = 15,
    ) -> None:
        self.api_key = api_key
        self.platforms = platforms
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def search(self, product: Product, max_results: int) -> list[SearchResult]:
        if not self.enabled:
            return []

        site_restriction = _build_site_restriction(self.platforms)
        query = f'"{product.product_name}"{site_restriction}'
        cap = min(max_results, 10)

        params = urllib.parse.urlencode({
            "engine": "google",
            "q": query,
            "api_key": self.api_key,
            "gl": "tw",
            "hl": "zh-TW",
            "num": cap,
        })
        url = f"{SERP_ENDPOINT}?{params}"
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        LOGGER.info("SerpAPI 搜尋：%s", product.product_name)

        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            LOGGER.warning("SerpAPI error %s: %s", exc.code, body)
            return []
        except Exception as exc:
            LOGGER.warning("SerpAPI request failed: %s", exc)
            return []

        return self._parse_results(data, cap, now)

    def _parse_results(
        self, data: dict[str, Any], cap: int, searched_at: str
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen: set[str] = set()
        allowed = set(self.platforms) if self.platforms else None

        for rank, item in enumerate(data.get("organic_results", []), start=1):
            link = item.get("link", "").strip()
            title = item.get("title", "").strip()
            snippet = item.get("snippet", "").strip()
            if not link or link in seen:
                continue
            if not is_product_page(link):
                LOGGER.debug("跳過非商品頁：%s", link[:80])
                continue
            platform = detect_platform(link)
            if allowed and platform not in allowed:
                continue
            seen.add(link)
            results.append(SearchResult(
                product_name=title,
                url=link,
                snippet=snippet,
                platform=platform,
                source="serpapi",
                rank=rank,
                searched_at=searched_at,
            ))
            if len(results) >= cap:
                break

        return results
