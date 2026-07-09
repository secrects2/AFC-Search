"""ThirdPartyShopeeProvider — delegate Shopee price fetching to an external API.

Supports any third-party scraping service (Apify, Scrapeless, Bright Data,
Oxylabs, etc.) by sending a generic JSON request and normalizing the response.

Configuration via .env:
    SHOPEE_THIRD_PARTY_API_URL=https://api.example.com/shopee/product
    SHOPEE_THIRD_PARTY_API_KEY=your_api_key
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from src.parsers.shopee_provider import (
    ShopeePriceProvider,
    ShopeePriceResult,
    parse_shopee_url,
    _now_iso,
)

LOGGER = logging.getLogger(__name__)


class ThirdPartyShopeeProvider(ShopeePriceProvider):
    """Call a third-party API to get Shopee product price."""

    name = "third_party"

    def __init__(
        self,
        api_url: str = "",
        api_key: str = "",
        timeout: int = 60,
        max_retries: int = 1,
    ) -> None:
        self.api_url = api_url.strip()
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.max_retries = max_retries

    @property
    def enabled(self) -> bool:
        return bool(self.api_url)

    def get_product_price(self, url: str) -> ShopeePriceResult:
        shop_id, item_id = parse_shopee_url(url)
        base = ShopeePriceResult(
            url=url,
            shop_id=shop_id,
            item_id=item_id,
            source=self.name,
            checked_at=_now_iso(),
        )

        if not self.enabled:
            base.status = "error"
            base.error_message = "Third-party API URL not configured"
            return base

        if not shop_id or not item_id:
            base.status = "error"
            base.error_message = f"Cannot parse Shopee URL: {url}"
            return base

        payload = {
            "url": url,
            "shop_id": shop_id,
            "item_id": item_id,
        }
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-API-Key"] = self.api_key

        last_error: str = ""
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return self._normalize(data, base)

                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                LOGGER.warning(
                    "Third-party API attempt %d/%d failed: %s",
                    attempt + 1, self.max_retries + 1, last_error,
                )
            except requests.Timeout:
                last_error = f"Timeout after {self.timeout}s"
                LOGGER.warning("Third-party API timeout (attempt %d)", attempt + 1)
            except Exception as exc:
                last_error = str(exc)
                LOGGER.warning("Third-party API error: %s", exc)

        base.status = "error"
        base.error_message = f"Third-party API failed: {last_error}"
        return base

    @staticmethod
    def _normalize(data: dict[str, Any], base: ShopeePriceResult) -> ShopeePriceResult:
        """Normalize a third-party API response into ShopeePriceResult.

        Tries common field names that different providers might use.
        """
        # Unwrap nested data envelopes
        inner = data
        for key in ("data", "result", "product", "item"):
            if key in inner and isinstance(inner[key], dict):
                inner = inner[key]
                break

        # Title
        base.title = str(
            inner.get("name")
            or inner.get("title")
            or inner.get("product_name")
            or ""
        ).strip()

        # Price — try several field names; some APIs use cents
        price = _extract_price(inner, "price")
        if price is None:
            price = _extract_price(inner, "current_price")
        if price is None:
            price = _extract_price(inner, "sale_price")
        base.price = price

        base.price_min = _extract_price(inner, "price_min")
        base.price_max = _extract_price(inner, "price_max")

        # If main price is missing but min is available, use min
        if base.price is None and base.price_min is not None:
            base.price = base.price_min

        # Seller
        seller_data = inner.get("shop") or inner.get("seller") or inner.get("shop_info") or {}
        if isinstance(seller_data, dict):
            base.seller = str(
                seller_data.get("name")
                or seller_data.get("shop_name")
                or seller_data.get("username")
                or ""
            ).strip()
        elif isinstance(seller_data, str):
            base.seller = seller_data.strip()

        # Stock
        stock = inner.get("stock") or inner.get("quantity") or inner.get("available_stock")
        if stock is not None:
            try:
                base.stock = int(stock)
            except (ValueError, TypeError):
                pass

        # Currency
        base.currency = str(inner.get("currency") or "TWD").upper()

        # Status
        base.status = "ok" if base.price is not None else "price_unknown"
        base.raw_data = data

        return base


def _extract_price(data: dict, field_name: str) -> float | None:
    """Extract a price value from a dict field, handling Shopee microcent format."""
    raw = data.get(field_name)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (ValueError, TypeError):
        return None

    if value <= 0:
        return None

    # Shopee internal APIs use microcents (÷100000).
    # If the value is unreasonably large, assume it's in microcents.
    if value > 1_000_000:
        value = value / 100_000

    return round(value, 2)
