"""Brave Search API provider.

Uses the Brave Search REST API (https://api.search.brave.com) as a
fallback search provider when SerpAPI is unavailable.
Free tier: ~1000 queries/month ($5 monthly credit).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from src.loader import Product
from src.search.base import BaseSearchProvider, SearchResult
from src.search.serp_api import detect_platform, is_product_page

LOGGER = logging.getLogger(__name__)

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class BraveSearchProvider(BaseSearchProvider):
    """Search provider using Brave Search API."""

    name = "brave"

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

        sites = " OR ".join(
            f"site:{s}" for s in self._platform_domains()
        )
        query = f'"{product.product_name}" {sites}' if sites else f'"{product.product_name}"'
        cap = min(max_results, 20)

        params = urllib.parse.urlencode({
            "q": query,
            "country": "tw",
            "search_lang": "zh-hant",
            "count": cap,
        })
        url = f"{BRAVE_ENDPOINT}?{params}"
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        LOGGER.info("Brave Search 搜尋：%s", product.product_name)

        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
            })
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            LOGGER.warning("Brave Search error %s: %s", exc.code, body)
            return []
        except Exception as exc:
            LOGGER.warning("Brave Search request failed: %s", exc)
            return []

        return self._parse_results(data, cap, now)

    def _platform_domains(self) -> list[str]:
        site_map = {
            "shopee": "shopee.tw",
            "momo": "momo.com.tw",
            "yahoo": "tw.buy.yahoo.com",
            "pchome": "24h.pchome.com.tw",
            "ruten": "ruten.com.tw",
            "rakuten": "rakuten.com.tw",
        }
        return [site_map[p] for p in self.platforms if p in site_map]

    def _parse_results(
        self, data: dict[str, Any], cap: int, searched_at: str
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen: set[str] = set()
        allowed = set(self.platforms) if self.platforms else None

        web_results = data.get("web", {}).get("results", [])
        for rank, item in enumerate(web_results, start=1):
            link = item.get("url", "").strip()
            title = item.get("title", "").strip()
            snippet = item.get("description", "").strip()
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
                source="brave",
                rank=rank,
                searched_at=searched_at,
            ))
            if len(results) >= cap:
                break

        return results
