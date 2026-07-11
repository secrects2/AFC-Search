"""Shopee parser — thin wrapper that delegates to ShopeePriceProvider chain.

This module integrates the new Shopee provider architecture with the existing
BaseParser / ParserOutput interface used by the rest of the system.
"""
from __future__ import annotations

import logging
from pathlib import Path

from src.parsers.base import BaseParser, ParserOutput
from src.parsers.shopee_provider import (
    ShopeePriceResult,
    build_shopee_provider_chain,
    parse_shopee_url,
)

LOGGER = logging.getLogger(__name__)


class ShopeeParser(BaseParser):
    platform = "shopee"

    def parse(self, url: str, output_dir: Path) -> ParserOutput:
        """Parse a Shopee product URL using the provider chain.

        Tries each configured provider in order (ThirdParty → HTML → Playwright)
        and returns the first successful result. Never raises.
        """
        providers = build_shopee_provider_chain()

        if not providers:
            LOGGER.warning("No Shopee providers available")
            return ParserOutput(
                platform=self.platform,
                url=url,
                parse_status="price_not_found",
                evidence_text="No Shopee providers configured",
            )

        last_result: ShopeePriceResult | None = None

        for provider in providers:
            LOGGER.info(
                "Shopee: trying provider [%s] for %s",
                provider.name, url[:80],
            )
            result = provider.get_product_price(url)
            last_result = result

            if result.status == "ok" and result.price is not None:
                LOGGER.info(
                    "Shopee [%s]: found price $%.0f for %s",
                    provider.name, result.price, result.title[:40],
                )
                return self._to_parser_output(result)

            LOGGER.info(
                "Shopee [%s]: %s — %s",
                provider.name, result.status, result.error_message[:80],
            )

        # All providers failed — return the last result
        return self._to_parser_output(last_result) if last_result else ParserOutput(
            platform=self.platform,
            url=url,
            parse_status="price_not_found",
            evidence_text="All Shopee providers failed",
        )

    @staticmethod
    def _to_parser_output(result: ShopeePriceResult) -> ParserOutput:
        """Convert ShopeePriceResult to the standard ParserOutput format."""
        # Map Shopee status to parser status
        status_map = {
            "ok": "ok",
            "price_unknown": "price_not_found",
            "blocked": "page_blocked",
            "language_required": "language_required",
            "error": "search_failed",
        }
        parse_status = status_map.get(result.status, "search_failed")

        evidence_parts = []
        if result.source:
            evidence_parts.append(f"provider={result.source}")
        if result.error_message:
            evidence_parts.append(result.error_message)

        return ParserOutput(
            platform="shopee",
            url=result.url,
            title=result.title,
            price=result.price,
            seller=result.seller,
            raw_text=f"shop_id={result.shop_id} item_id={result.item_id}",
            parse_status=parse_status,
            evidence_text=" | ".join(evidence_parts),
        )
