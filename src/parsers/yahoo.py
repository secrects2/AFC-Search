from __future__ import annotations

import html
import json
import re

from src.loader import parse_price_value
from src.parsers.generic import GenericParser


def _parse_positive_price(value: object) -> float | None:
    price = parse_price_value(value)
    if price is None or price < 50 or price > 500000:
        return None
    return price


def _extract_yahoo_item_price(html_text: str) -> float | None:
    """Read Yahoo auction's structured item.price from the page state."""
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html_text, "html.parser")
        state_node = soup.select_one("#isoredux-data")
        if state_node is not None:
            raw_state = state_node.string or state_node.get_text()
            payload = json.loads(html.unescape(raw_state or ""))
            item = payload.get("item") if isinstance(payload, dict) else None
            if isinstance(item, dict):
                price = _parse_positive_price(item.get("price"))
                if price is not None:
                    return price
    except Exception:
        pass

    # Keep a narrow fallback for Yahoo responses that expose the item state
    # without the expected script id.
    patterns = (
        r'"listingId"\s*:\s*"[^"]+"\s*,\s*"price"\s*:\s*([0-9][0-9,]*(?:\.\d+)?)',
        r'"price"\s*:\s*([0-9][0-9,]*(?:\.\d+)?)\s*,\s*"title"\s*:',
    )
    for pattern in patterns:
        match = re.search(pattern, html_text, re.IGNORECASE | re.DOTALL)
        if match:
            price = _parse_positive_price(match.group(1))
            if price is not None:
                return price
    return None


class YahooParser(GenericParser):
    platform = "yahoo"

    @classmethod
    def extract_price(cls, html_text: str, raw_text: str) -> tuple[float | None, str]:
        item_price = _extract_yahoo_item_price(html_text)
        if item_price is not None:
            return item_price, "yahoo embedded item.price"
        return super().extract_price(html_text, raw_text)
