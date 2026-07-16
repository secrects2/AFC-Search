"""Chain search provider with automatic fallback, aggregation, and caching."""
from __future__ import annotations

import logging
from pathlib import Path

from src.loader import Product
from src.search.base import BaseSearchProvider, SearchResult
from src.search.brave_search import BraveSearchProvider
from src.search.cache import SearchCache
from src.search.findprice_api import FindPriceProvider
from src.search.lbj_api import LbjSearchProvider
from src.search.biggo_api import BigGoSearchProvider
from src.search.feebee_search import FeebeeSearchProvider
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
        self._last_attempts: list[dict[str, object]] = []

    @property
    def enabled(self) -> bool:
        return len(self.providers) > 0

    @property
    def last_provider(self) -> str:
        return self._last_provider

    @property
    def last_attempts(self) -> list[dict[str, object]]:
        """Return a redacted audit trail for the most recent search."""
        return [dict(attempt) for attempt in self._last_attempts]

    @staticmethod
    def _redact_error(exc: Exception) -> str:
        """Keep provider diagnostics useful without persisting credentials."""
        import re

        message = str(exc)
        message = re.sub(
            r"(?i)(api[_-]?key|access[_-]?token|token|secret|password)=([^&\s]+)",
            r"\1=[REDACTED]",
            message,
        )
        return message[:300]

    def search(self, product: Product, max_results: int) -> list[SearchResult]:
        self._last_attempts = []

        # Check cache first
        if self.cache:
            cached = self.cache.get(product.product_name)
            if cached is not None:
                self._last_provider = f"{cached[0].source}(cached)" if cached else "cache"
                self._last_attempts.append({
                    "provider": "cache",
                    "status": "cached",
                    "result_count": len(cached),
                })
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
            attempt: dict[str, object] = {
                "provider": provider.name,
                "status": "started",
                "result_count": 0,
            }
            try:
                results = provider.search(product, max_results)
                if results:
                    attempt["status"] = "success"
                    attempt["result_count"] = len(results)
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
                provider_status = str(getattr(provider, "last_status", "") or "").strip()
                attempt["status"] = (
                    provider_status
                    if provider_status in {"blocked", "error", "unavailable"}
                    else "no_results"
                )
                provider_error = str(getattr(provider, "last_error", "") or "").strip()
                if provider_error:
                    attempt["error"] = self._redact_error(RuntimeError(provider_error))
                LOGGER.info(
                    "%s 無結果，嘗試下一個：%s",
                    provider.name,
                    product.product_name,
                )
            except Exception as exc:
                attempt["status"] = "error"
                attempt["error"] = self._redact_error(exc)
                LOGGER.warning(
                    "%s 搜尋失敗，嘗試下一個：%s - %s",
                    provider.name,
                    product.product_name,
                    exc,
                )
                continue
            finally:
                self._last_attempts.append(attempt)

        if combined:
            self._last_provider = "+".join(used_providers)
            if self.cache:
                self.cache.put(product.product_name, combined)
            return combined

        self._last_provider = "none"
        return []


def build_chain_provider(
    serpapi_key: str = "",
    brave_key: str = "",
    platforms: list[str] | None = None,
    cache_path: Path | None = None,
    cache_hours: int = 24,
    timeout: float = 15.0,
) -> ChainSearchProvider:
    """Helper to build the default search chain."""
    providers: list[BaseSearchProvider] = []

    # 1. Specialized providers (most likely to have accurate, e-commerce specific results)
    providers.append(ShopeeSearchProvider())
    providers.append(FeebeeSearchProvider(platforms=platforms))
    providers.append(FindPriceProvider())
    providers.append(BigGoSearchProvider(platforms=platforms))
    providers.append(LbjSearchProvider(platforms=platforms))

    # 2. Paid / High-quality generic search engines
    if serpapi_key:
        providers.append(SerpAPIProvider(api_key=serpapi_key, platforms=platforms, timeout=timeout))

    if brave_key:
        providers.append(BraveSearchProvider(api_key=brave_key, platforms=platforms, timeout=timeout))

    cache = SearchCache(cache_path, ttl_hours=cache_hours) if cache_path else None

    return ChainSearchProvider(providers=providers, cache=cache)
