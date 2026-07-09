"""Shopee Price Provider — abstract interface and data model.

This module defines the ShopeePriceResult dataclass, the ShopeePriceProvider
ABC, and utility functions for Shopee URL parsing and provider chain building.
"""
from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

LOGGER = logging.getLogger(__name__)

# Regex to extract shop_id and item_id from Shopee URLs.
# Format: https://shopee.tw/product-name-i.SHOPID.ITEMID
#   or:   https://shopee.tw/product/SHOPID/ITEMID
SHOPEE_URL_PATTERNS = [
    re.compile(r"i\.(\d+)\.(\d+)"),
    re.compile(r"/product/(\d+)/(\d+)"),
]


@dataclass
class ShopeePriceResult:
    """Normalized result from any Shopee price provider."""

    url: str = ""
    shop_id: str = ""
    item_id: str = ""
    title: str = ""
    price: float | None = None
    price_min: float | None = None
    price_max: float | None = None
    seller: str = ""
    stock: int | None = None
    currency: str = "TWD"
    source: str = ""              # "third_party" / "html_fallback" / "playwright"
    status: str = "ok"            # "ok" / "price_unknown" / "blocked" / "error"
    error_message: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)
    checked_at: str = ""          # ISO 8601


class ShopeePriceProvider(ABC):
    """Abstract interface for all Shopee price providers."""

    name: str = "base"

    @abstractmethod
    def get_product_price(self, url: str) -> ShopeePriceResult:
        """Fetch price for a Shopee product URL.

        Must never raise — errors are encoded in ShopeePriceResult.status.
        """
        ...

    @property
    def enabled(self) -> bool:
        """Whether this provider is configured and ready to use."""
        return True


def parse_shopee_url(url: str) -> tuple[str, str]:
    """Extract (shop_id, item_id) from a Shopee product URL.

    Returns ('', '') if the URL cannot be parsed.
    """
    for pattern in SHOPEE_URL_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1), match.group(2)
    return "", ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_shopee_provider_chain() -> list[ShopeePriceProvider]:
    """Build an ordered list of Shopee price providers based on .env settings.

    Provider selection is controlled by SHOPEE_PROVIDER env var:
    - "third_party": only ThirdPartyShopeeProvider
    - "html":        only ShopeeHtmlFallbackProvider
    - "playwright":  only ShopeePlaywrightFallbackProvider
    - "chain" (default): all three in order
    """
    from src.parsers.shopee_third_party import ThirdPartyShopeeProvider
    from src.parsers.shopee_html_fallback import ShopeeHtmlFallbackProvider
    from src.parsers.shopee_playwright_fallback import ShopeePlaywrightFallbackProvider

    mode = os.environ.get("SHOPEE_PROVIDER", "chain").strip().lower()
    timeout = int(os.environ.get("SHOPEE_TIMEOUT_SECONDS", "60"))
    max_retries = int(os.environ.get("SHOPEE_MAX_RETRIES", "1"))

    providers: list[ShopeePriceProvider] = []

    if mode in ("third_party", "chain"):
        tp = ThirdPartyShopeeProvider(
            api_url=os.environ.get("SHOPEE_THIRD_PARTY_API_URL", ""),
            api_key=os.environ.get("SHOPEE_THIRD_PARTY_API_KEY", ""),
            timeout=timeout,
            max_retries=max_retries,
        )
        if tp.enabled:
            providers.append(tp)
        elif mode == "third_party":
            LOGGER.warning(
                "SHOPEE_PROVIDER=third_party but SHOPEE_THIRD_PARTY_API_URL is not set"
            )

    if mode in ("html", "chain"):
        providers.append(ShopeeHtmlFallbackProvider(timeout=timeout))

    if mode in ("playwright", "chain"):
        providers.append(ShopeePlaywrightFallbackProvider(timeout=timeout))

    if not providers:
        LOGGER.warning("No Shopee providers configured; Shopee URLs will return price_unknown")

    return providers
