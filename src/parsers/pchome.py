from __future__ import annotations

import re

from src.loader import parse_price_value
from src.parsers.generic import GenericParser


_PRICE_ATTRIBUTES = (
    ("data-gtm-price", "pchome data-gtm-price"),
    ("data-sale-price", "pchome data-sale-price"),
    ("data-saleprice", "pchome data-saleprice"),
    ("data-product-price", "pchome data-product-price"),
    ("data-price", "pchome data-price"),
)

_EMBEDDED_SALE_PRICE_KEYS = (
    "salePrice",
    "sale_price",
    "sellingPrice",
    "currentPrice",
)


def _parse_positive_price(value: object) -> float | None:
    price = parse_price_value(value)
    if price is None or price <= 0 or price > 500000:
        return None
    return price


def _extract_pchome_main_price(html_text: str) -> tuple[float | None, str]:
    """Read PChome's sale price markers before generic page prices.

    PChome has served the same product through several DOM versions. The
    GTM marker is the most reliable one, while the other data attributes and
    embedded sale-price keys cover newer and cached page variants.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html_text, "html.parser")
        product_root = soup.select_one("#ProdBriefing")
        roots = [product_root] if product_root else []
        if product_root is not soup:
            roots.append(soup)

        seen_nodes: set[int] = set()
        for root in roots:
            for attribute, evidence in _PRICE_ATTRIBUTES:
                for node in root.select(f"[{attribute}]"):
                    node_id = id(node)
                    if node_id in seen_nodes:
                        continue
                    seen_nodes.add(node_id)
                    price = _parse_positive_price(node.get(attribute, ""))
                    if price is not None:
                        return price, evidence

        # Some responses keep the rendered price in a JSON state blob rather
        # than a data-* attribute. Prefer sale-price keys over original price.
        for key in _EMBEDDED_SALE_PRICE_KEYS:
            pattern = re.compile(
                rf"[\"']{re.escape(key)}[\"']\s*:\s*[\"']?([0-9][0-9,]*(?:\.\d+)?)[\"']?",
                re.IGNORECASE,
            )
            match = pattern.search(html_text)
            if match:
                price = _parse_positive_price(match.group(1))
                if price is not None:
                    return price, f"pchome embedded {key}"
    except Exception:
        pass

    return None, ""


class PChomeParser(GenericParser):
    platform = "pchome"

    @classmethod
    def extract_price(cls, html_text: str, raw_text: str) -> tuple[float | None, str]:
        price, evidence = _extract_pchome_main_price(html_text)
        if price is not None:
            return price, evidence
        return super().extract_price(html_text, raw_text)
