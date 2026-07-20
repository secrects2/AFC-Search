from __future__ import annotations

import re

from src.loader import parse_price_value
from src.parsers.generic import GenericParser


_STATE_PRICE_KEYS = (
    "sale_price",
    "special_price",
    "obPrice",
    "salePrice",
    "specialPrice",
)


def _parse_positive_price(value: object) -> float | None:
    price = parse_price_value(value)
    if price is None or price < 50 or price > 500000:
        return None
    return price


def _extract_viva_visible_price(html_text: str) -> float | None:
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html_text, "html.parser")
        for node in soup.select(".p-price"):
            price = _parse_positive_price(node.get_text(" ", strip=True))
            if price is not None:
                return price
    except Exception:
        pass
    return None


def _extract_viva_state_price(html_text: str) -> tuple[float | None, str]:
    """Read ViVa's SSR state price fields before generic page numbers."""
    for key in _STATE_PRICE_KEYS:
        pattern = re.compile(
            rf"(?:\"{re.escape(key)}\"|'" + re.escape(key) + rf"'|{re.escape(key)})"
            r"\s*:\s*[\"']?([0-9][0-9,]*(?:\.\d+)?)[\"']?",
            re.IGNORECASE,
        )
        for match in pattern.finditer(html_text):
            price = _parse_positive_price(match.group(1))
            if price is not None:
                return price, f"viva embedded {key}"
    return None, ""


class VivaParser(GenericParser):
    platform = "viva"

    @classmethod
    def extract_price(cls, html_text: str, raw_text: str) -> tuple[float | None, str]:
        visible_price = _extract_viva_visible_price(html_text)
        if visible_price is not None:
            return visible_price, "viva visible .p-price"

        state_price, evidence = _extract_viva_state_price(html_text)
        if state_price is not None:
            return state_price, evidence

        return super().extract_price(html_text, raw_text)
