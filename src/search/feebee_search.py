"""Feebee-based search provider."""

from __future__ import annotations

import logging

from src.loader import Product
from src.search.base import BaseSearchProvider, SearchResult
from src.search.feebee_api import search_feebee_listings

LOGGER = logging.getLogger(__name__)


class FeebeeSearchProvider(BaseSearchProvider):
    """Searches Feebee (feebee.com.tw) for products."""

    name = "feebee"

    def __init__(self, platforms: list[str] | None = None) -> None:
        self.platforms = platforms

    def search(self, product: Product, max_results: int) -> list[SearchResult]:
        keyword = product.product_name
        listings = search_feebee_listings(
            keyword=keyword,
            max_results=max_results,
            timeout=15,
            delay_seconds=1.0,
        )

        results: list[SearchResult] = []
        for listing in listings:
            if self.platforms and listing.platform not in self.platforms:
                continue

            # Skip results that are just keyword spam (e.g. "Title containing keyword but not the product")
            # The API matching score can filter bad results, but we rely on downstream logic to weed them out.
            # In Discovery we prefer recall over precision.

            results.append(
                SearchResult(
                    url=listing.url,
                    product_name=listing.title,
                    found_price=listing.price,
                    source="feebee",
                    platform=listing.platform,
                    seller=listing.seller or "",
                )
            )

        return results
