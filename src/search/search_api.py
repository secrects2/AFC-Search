"""Chain search provider with automatic fallback and caching.

Tries search providers in order (SerpAPI → Brave) and caches results.
"""
from __future__ import annotations

import logging
from pathlib import Path

from src.loader import Product
from src.search.base import BaseSearchProvider, SearchResult
from src.search.brave_search import BraveSearchProvider
from src.search.cache import SearchCache
from src.search.serp_api import SerpAPIProvider

LOGGER = logging.getLogger(__name__)


class ChainSearchProvider(BaseSearchProvider):
    """Tries multiple search providers in order with caching."""

    name = "chain"

    def __init__(
        self,
        providers: list[BaseSearchProvider],
        cache: SearchCache | None = None,
    ) -> None:
        self.providers = [p for p in providers if getattr(p, "enabled", True)]
        self.cache = cache
        self._last_provider: str = ""

    @property
    def enabled(self) -> bool:
        return len(self.providers) > 0

    @property
    def last_provider(self) -> str:
        return self._last_provider

    def search(self, product: Product, max_results: int) -> list[SearchResult]:
        # Check cache first
        if self.cache:
            cached = self.cache.get(product.product_name)
            if cached is not None:
                self._last_provider = f"{cached[0].source}(cached)" if cached else "cache"
                LOGGER.info(
                    "使用快取結果：%s (%d 筆)",
                    product.product_name,
                    len(cached),
                )
                return cached

        # Try each provider in order
        for provider in self.providers:
            try:
                results = provider.search(product, max_results)
                if results:
                    self._last_provider = provider.name
                    LOGGER.info(
                        "%s 搜尋成功：%s (%d 筆)",
                        provider.name,
                        product.product_name,
                        len(results),
                    )
                    # Cache the results
                    if self.cache:
                        self.cache.put(product.product_name, results)
                    return results
                LOGGER.info(
                    "%s 無結果，嘗試下一個：%s",
                    provider.name,
                    product.product_name,
                )
            except Exception as exc:
                LOGGER.warning(
                    "%s 搜尋失敗，嘗試下一個：%s - %s",
                    provider.name,
                    product.product_name,
                    exc,
                )
                continue

        self._last_provider = "none"
        return []


def build_chain_provider(
    serpapi_key: str,
    brave_key: str,
    platforms: list[str],
    cache_path: Path,
    cache_hours: int = 24,
    timeout: float = 15,
) -> ChainSearchProvider:
    """Build a ChainSearchProvider with SerpAPI → Brave fallback."""
    providers: list[BaseSearchProvider] = []

    if serpapi_key:
        providers.append(SerpAPIProvider(
            api_key=serpapi_key,
            platforms=platforms,
            timeout=timeout,
        ))

    if brave_key:
        providers.append(BraveSearchProvider(
            api_key=brave_key,
            platforms=platforms,
            timeout=timeout,
        ))

    cache = SearchCache(cache_path, ttl_hours=cache_hours) if cache_path else None

    return ChainSearchProvider(providers=providers, cache=cache)
