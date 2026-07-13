from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.loader import Product


@dataclass(frozen=True)
class SearchResult:
    """Unified search result returned by all search providers."""
    product_name: str
    url: str
    snippet: str = ""
    platform: str = "manual"
    source: str = "manual"
    rank: int = 0
    cached: bool = False
    searched_at: str = ""
    found_price: float | None = None
    seller: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)


class BaseSearchProvider:
    """Base class for all search providers."""

    name: str = "base"

    def search(self, product: Product, max_results: int) -> list[SearchResult]:
        raise NotImplementedError
