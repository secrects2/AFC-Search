"""Chain search provider with automatic fallback, aggregation, and caching."""
from __future__ import annotations

import logging
from pathlib import Path

from src.loader import Product
from src.search.base import BaseSearchProvider, SearchResult
from src.search.brave_search import BraveSearchProvider
from src.search.cache import SearchCache
from src.search.findprice_api import FindPriceProvider
from src.search.serp_api import SerpAPIProvider
from src.search.shopee_search import ShopeeSearchProvider

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

        combined: list[SearchResult] = []
        seen_urls: set[str] = set()
        used_providers: list[str] = []

        # Try each provider in order and merge results. This keeps Shopee and
        # FindPrice candidates from being skipped when SerpAPI returns only
        # non-Shopee results first.
        for provider in self.providers:
            try:
                results = provider.search(product, max_results)
                if results:
                    used_providers.append(provider.name)
                    LOGGER.info(
                        "%s 搜尋成功：%s (%d 筆)",
                        provider.name,
                        product.product_name,
                        len(results),
                    )
                    for result in results:
                        normalized_url = result.url.strip().lower()
                        if not normalized_url or normalized_url in seen_urls:
                            continue
                        seen_urls.add(normalized_url)
                        combined.append(result)
                    continue
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

        if combined:
            self._last_provider = "+".join(used_providers)
            if self.cache:
                self.cache.put(product.product_name, combined)
            return combined

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
    """Build a ChainSearchProvider with API search plus free fallbacks."""
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

    # Always append FindPrice as a free fallback.
    providers.append(FindPriceProvider())

    # Append Shopee direct search (using Playwright) as the last resort.
    if not platforms or "shopee" in {platform.lower() for platform in platforms}:
        providers.append(ShopeeSearchProvider(timeout=int(timeout)))

    cache = SearchCache(cache_path, ttl_hours=cache_hours) if cache_path else None

    return ChainSearchProvider(providers=providers, cache=cache)
