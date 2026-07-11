"""Feebee price provider for daily observations."""
from __future__ import annotations

import logging
from typing import Any

from src.database import Database, ProductRow, CandidateRow
from src.search.feebee_api import find_best_feebee_listing
from src.matcher import match_score

LOGGER = logging.getLogger(__name__)


class FeebeePriceProvider:
    """Query Feebee for daily price observations."""

    def __init__(self, config: dict[str, Any], db: Database):
        self._config = config
        self._db = db

    def observe(self, product: ProductRow, candidate: CandidateRow) -> dict[str, Any] | None:
        """
        Query Feebee to find a matching price for this candidate.
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
            # Feebee groups Shopee differently (shopee, shopeemall, etc.)
            expected_platform = "shopee"

        listing = find_best_feebee_listing(
            keyword=keyword,
            expected_title=candidate.title or product.product_name,
            preferred_platform=expected_platform,
            timeout=int(self._config.get("request_timeout_seconds", 20)),
        )

        if not listing or listing.price is None:
            return None

        # Check exclusion keywords
        from src.database import _matches_keyword
        exclusion_keywords = self._db.get_all_exclusion_keywords()
        values_to_check = (listing.title, listing.seller, listing.url)
        for ex in exclusion_keywords:
            if _matches_keyword(ex, values_to_check):
                LOGGER.info("Feebee observation excluded by keyword '%s': %s", ex, listing.title)
                return None

        # Calculate match score
        score = match_score(product.product_name, listing.title)
        
        # Calculate confidence
        confidence = 0.8
        if score < 60:
            confidence = 0.3
        elif score < 80:
            confidence = 0.5

        # Normalize platform if Feebee found Shopee
        platform = candidate.platform
        if "shopee" in (listing.url or "").lower():
            platform = "shopee"

        return {
            "source": "feebee",
            "platform": platform,
            "url": listing.url,
            "title": listing.title,
            "seller": listing.seller,
            "price": listing.price,
            "currency": "TWD",
            "match_score": score,
            "confidence": confidence,
            "status": "success",
            "error_message": "",
            "raw_data": {
                "evidence_text": listing.price_text,
            }
        }
