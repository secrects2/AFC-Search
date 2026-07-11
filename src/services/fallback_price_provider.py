"""Universal Fallback price provider for daily observations."""
from __future__ import annotations

import logging
from typing import Any

from src.database import Database, ProductRow, CandidateRow
from src.loader import Product
from src.matcher import match_score
from src.search.search_api import ChainSearchProvider
from src.search.feebee_search import FeebeeSearchProvider
from src.search.biggo_api import BigGoSearchProvider
from src.search.lbj_api import LbjSearchProvider

LOGGER = logging.getLogger(__name__)


class FallbackPriceProvider:
    """Query multiple fallback aggregators for daily price observations."""

    def __init__(self, config: dict[str, Any], db: Database):
        self._config = config
        self._db = db

        # Initialize the fallback search chain with our free aggregators
        self._search_provider = ChainSearchProvider(
            providers=[
                FeebeeSearchProvider(),
                BigGoSearchProvider(),
                LbjSearchProvider(),
            ]
        )

    def observe(self, product: ProductRow, candidate: CandidateRow) -> dict[str, Any] | None:
        """
        Query fallback platforms to find a matching price for this candidate.
        Returns a dict representing a price_observation, or None if failed.
        """
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

        # Only look for listings on the target platform if possible
        expected_platform = candidate.platform or None
        if expected_platform and expected_platform.lower() == "shopee":
            expected_platform = "shopee"

        temp_product = Product(
            suggested_price=product.suggested_price or 0,
            product_name=keyword,
            row_index=product.id,
            raw_suggested_price=str(product.suggested_price or ""),
        )

        from src.services.source_health import SourceHealthTracker
        health = SourceHealthTracker(self._db)
        
        active_providers = []
        if not health.should_skip("feebee", expected_platform or ""):
            active_providers.append(FeebeeSearchProvider())
        else:
            LOGGER.info("Feebee is on cooldown, skipping fallback.")
            
        if not health.should_skip("biggo", expected_platform or ""):
            active_providers.append(BigGoSearchProvider())
        else:
            LOGGER.info("BigGo is on cooldown, skipping fallback.")
            
        if not health.should_skip("lbj", expected_platform or ""):
            active_providers.append(LbjSearchProvider())
        else:
            LOGGER.info("LBJ is on cooldown, skipping fallback.")

        if not active_providers:
            LOGGER.warning("All fallback providers are blocked/cooldown for %s", expected_platform)
            return None

        search_provider = ChainSearchProvider(providers=active_providers)
        results = search_provider.search(temp_product, max_results=30)
        
        if not results:
            return None

        from src.database import _matches_keyword
        exclusion_keywords = self._db.get_all_exclusion_keywords()

        best_listing = None
        best_score = -1

        for res in results:
            if res.found_price is None:
                continue
                
            # Skip if platform is completely wrong (but allow 'feebee' or 'biggo' if they don't specify the underlying platform well)
            res_platform = res.platform.lower() if res.platform else ""
            if expected_platform and res_platform not in (expected_platform, "feebee", "biggo", "lbj"):
                # E.g. expected shopee but found momo
                if res_platform in ("shopee", "momo", "pchome", "yahoo", "ruten", "rakuten", "coupang"):
                    if res_platform != expected_platform:
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
            
            if score > best_score:
                best_score = score
                best_listing = res

        if not best_listing:
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
            "raw_data": {
                "evidence_text": str(best_listing.found_price),
            }
        }
