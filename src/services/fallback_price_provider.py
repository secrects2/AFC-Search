"""Universal Fallback price provider for daily observations."""
from __future__ import annotations

import logging
from typing import Any

from src.database import Database, ProductRow, CandidateRow, is_disabled_platform
from src.loader import Product
from src.matcher import match_score
from src.search.base import SearchResult
from src.search.brave_search import BraveSearchProvider
from src.search.findprice_api import FindPriceProvider
from src.search.feebee_search import FeebeeSearchProvider
from src.search.biggo_api import BigGoSearchProvider
from src.search.lbj_api import LbjSearchProvider
from src.search.search_api import ChainSearchProvider
from src.search.serp_api import SerpAPIProvider
from src.search.shopee_search import ShopeeSearchProvider

LOGGER = logging.getLogger(__name__)

RETAIL_PLATFORMS = frozenset({
    "shopee", "momo", "yahoo", "pchome", "ruten", "rakuten",
    "coupang", "etmall", "books", "friday",
})
AGGREGATOR_PLATFORMS = frozenset({"feebee", "findprice", "biggo", "lbj", "other"})


def _config_value(config: Any, name: str, default: Any = "") -> Any:
    if isinstance(config, dict):
        return config.get(name, default)
    return getattr(config, name, default)


class FallbackPriceProvider:
    """Query multiple fallback aggregators for daily price observations."""

    def __init__(self, config: dict[str, Any], db: Database):
        self._config = config
        self._db = db
        self._request_timeout = float(
            _config_value(config, "request_timeout_seconds", 15)
        )
        self._serpapi_key = str(_config_value(config, "serpapi_api_key", "") or "")
        self._brave_key = str(_config_value(config, "brave_api_key", "") or "")
        self._platforms = list(_config_value(config, "platforms", []) or [])
        self._max_results = int(_config_value(config, "max_results_per_product", 30) or 30)
        self._shopee_profile_dir = str(
            _config_value(config, "shopee_profile_dir", "data/browser_profiles/shopee")
            or "data/browser_profiles/shopee"
        )
        self._shopee_headless = self._as_bool(
            _config_value(config, "shopee_headless", False)
        )

        # A daily run may have many candidates for one product. Reuse the
        # comparison-page results within that run instead of querying the same
        # aggregator page once per candidate.
        self._result_cache: dict[str, list[SearchResult]] = {}
        self._last_audit: dict[str, Any] = {}

    @property
    def last_audit(self) -> dict[str, Any]:
        """Return the audit trail for the most recent fallback attempt."""
        return dict(self._last_audit)

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _result_platform(result: SearchResult) -> str:
        raw_platform = (result.platform or "").strip().lower()
        if raw_platform in RETAIL_PLATFORMS:
            return raw_platform

        url = (result.url or "").lower()
        domains = {
            "shopee": "shopee.tw",
            "momo": "momo.com.tw",
            "yahoo": "yahoo.com.tw",
            "pchome": "pchome.com.tw",
            "ruten": "ruten.com.tw",
            "rakuten": "rakuten.com.tw",
            "coupang": "coupang.com",
            "etmall": "etmall.com.tw",
        }
        for platform, domain in domains.items():
            if domain in url:
                return platform
        return raw_platform if raw_platform in AGGREGATOR_PLATFORMS else ""

    @classmethod
    def _matches_expected_platform(
        cls, result: SearchResult, expected_platform: str
    ) -> tuple[bool, str]:
        matched_platform = cls._result_platform(result)
        if (
            not expected_platform
            or not matched_platform
            or matched_platform in AGGREGATOR_PLATFORMS
        ):
            return True, matched_platform
        return matched_platform == expected_platform, matched_platform

    def _provider_specs(self, expected_platform: str):
        search_platforms = [expected_platform] if expected_platform else self._platforms
        specs = [
            ("feebee", lambda: FeebeeSearchProvider()),
            ("findprice", lambda: FindPriceProvider()),
            ("biggo", lambda: BigGoSearchProvider()),
            ("lbj", lambda: LbjSearchProvider()),
        ]

        if self._serpapi_key:
            specs.append(
                (
                    "serpapi",
                    lambda: SerpAPIProvider(
                        api_key=self._serpapi_key,
                        platforms=search_platforms,
                        timeout=self._request_timeout,
                    ),
                )
            )
        if self._brave_key:
            specs.append(
                (
                    "brave",
                    lambda: BraveSearchProvider(
                        api_key=self._brave_key,
                        platforms=search_platforms,
                        timeout=self._request_timeout,
                    ),
                )
            )

        # Shopee is deliberately last because the site may challenge automated
        # browsers. It remains available as a final platform-specific backup.
        if expected_platform == "shopee":
            specs.append(
                (
                    "shopee",
                    lambda: ShopeeSearchProvider(
                        timeout=int(self._request_timeout),
                        profile_dir=self._shopee_profile_dir,
                        headless=self._shopee_headless,
                    ),
                )
            )
        return specs

    def observe(self, product: ProductRow, candidate: CandidateRow) -> dict[str, Any] | None:
        """
        Query fallback platforms to find a matching price for this candidate.
        Returns a dict representing a price_observation, or None if failed.
        """
        if is_disabled_platform(candidate.platform):
            self._last_audit = {
                "expected_platform": candidate.platform,
                "provider_chain": "disabled_platform",
                "match_status": "platform_disabled",
                "attempts": [],
            }
            return None

        # Determine search keyword
        if product.keywords:
            first_kw = product.keywords.split(",")[0].strip()
            brand_prefix = product.brand or "AFC"
            if brand_prefix.lower() in first_kw.lower():
                keyword = first_kw
            else:
                keyword = f"{brand_prefix} {first_kw}"
        else:
            keyword = product.product_name
            keyword_lower = keyword.lower()
            if "afc" not in keyword_lower and "genki" not in keyword_lower:
                keyword = f"AFC {keyword}"

        # Only accept a known retail platform when the search result identifies
        # one. Aggregator-only results remain eligible but are marked for review.
        expected_platform = (candidate.platform or "").strip().lower()

        temp_product = Product(
            suggested_price=product.suggested_price or 0,
            product_name=keyword,
            row_index=product.id,
            raw_suggested_price=str(product.suggested_price or ""),
        )

        cache_key = f"{expected_platform or '*'}::{keyword.casefold().strip()}"
        if cache_key in self._result_cache:
            results = self._result_cache[cache_key]
            self._last_audit = {
                "expected_platform": expected_platform,
                "search_keyword": keyword,
                "provider_chain": "cache",
                "match_status": "cached",
                "attempts": [{
                    "provider": "cache",
                    "status": "cached",
                    "result_count": len(results),
                }],
            }
            LOGGER.info("使用本次監控的 fallback 快取：%s (%d 筆)", keyword, len(results))
        else:
            from src.services.source_health import SourceHealthTracker
            health = SourceHealthTracker(self._db)

            active_providers = []
            skipped_attempts: list[dict[str, Any]] = []
            for source_name, factory in self._provider_specs(expected_platform):
                if health.should_skip(source_name, expected_platform):
                    skipped_attempts.append({
                        "provider": source_name,
                        "status": "cooldown",
                        "result_count": 0,
                    })
                    LOGGER.info("%s is on cooldown, skipping fallback.", source_name)
                    continue
                active_providers.append(factory())

            if not active_providers:
                LOGGER.warning("All fallback providers are blocked/cooldown for %s", expected_platform)
                self._last_audit = {
                    "expected_platform": expected_platform,
                    "search_keyword": keyword,
                    "provider_chain": "none",
                    "match_status": "all_sources_unavailable",
                    "attempts": skipped_attempts,
                }
                return None

            search_provider = ChainSearchProvider(providers=active_providers)
            results = search_provider.search(temp_product, max_results=self._max_results)
            self._result_cache[cache_key] = results
            provider_chain = getattr(search_provider, "last_provider", "")
            provider_attempts = getattr(search_provider, "last_attempts", [])
            self._last_audit = {
                "expected_platform": expected_platform,
                "search_keyword": keyword,
                "provider_chain": provider_chain,
                "match_status": "search_results" if results else "no_search_results",
                "attempts": skipped_attempts + list(provider_attempts),
            }
        
        if not results:
            return None

        from src.database import _matches_keyword
        exclusion_keywords = self._db.get_all_exclusion_keywords()

        best_listing = None
        best_score = -1
        best_rank: tuple[int, int, int] | None = None
        best_matched_platform = ""
        best_platform_match = False

        for res in results:
            if res.found_price is None:
                continue

            matched_result_platform = self._result_platform(res)
            if is_disabled_platform(matched_result_platform):
                LOGGER.info("Ignoring fallback result from disabled platform: %s", res.url[:100])
                continue

            platform_match, matched_platform = self._matches_expected_platform(
                res, expected_platform
            )
            if not platform_match:
                continue

            # Check exclusion keywords
            values_to_check = (res.product_name, res.seller, res.url)
            excluded = False
            for ex in exclusion_keywords:
                if _matches_keyword(ex, values_to_check):
                    excluded = True
                    LOGGER.info("Fallback observation excluded by keyword '%s': %s", ex, res.product_name)
                    break
            if excluded:
                continue

            # Target score using candidate title if available, else product name
            target_title = candidate.title or product.product_name
            score = match_score(target_title, res.product_name)
            exact_platform = int(bool(expected_platform and matched_platform == expected_platform))
            rank = (score, exact_platform, -int(res.rank or 0))

            if best_rank is None or rank > best_rank:
                best_rank = rank
                best_score = score
                best_listing = res
                best_matched_platform = matched_platform
                best_platform_match = bool(
                    not expected_platform
                    or matched_platform in ({"", expected_platform} | AGGREGATOR_PLATFORMS)
                )

        if not best_listing:
            self._last_audit["match_status"] = "no_usable_match"
            return None

        # Calculate confidence
        confidence = 0.8
        if best_score < 60:
            confidence = 0.3
        elif best_score < 80:
            confidence = 0.5

        # Normalize platform
        platform = candidate.platform
        if "shopee" in (best_listing.url or "").lower():
            platform = "shopee"

        fallback_raw_data = dict(best_listing.raw_data or {})
        fallback_raw_data.update({
            "evidence_text": str(best_listing.found_price),
            "fallback_provider": best_listing.source,
            "search_keyword": keyword,
            "matched_platform": best_matched_platform or best_listing.platform,
            "expected_platform": expected_platform,
            "platform_match": best_platform_match,
            "direct_verification_required": True,
            "fallback_attempts": self._last_audit.get("attempts", []),
        })

        return {
            "source": best_listing.source,  # will be 'feebee', 'biggo', or 'lbj'
            "platform": platform,
            "url": best_listing.url,
            "title": best_listing.product_name,
            "seller": best_listing.seller,
            "price": best_listing.found_price,
            "currency": "TWD",
            "match_score": best_score,
            "confidence": confidence,
            "status": "success",
            "error_message": "",
            "raw_data": fallback_raw_data,
        }
