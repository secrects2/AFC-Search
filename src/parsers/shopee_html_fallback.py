"""ShopeeHtmlFallbackProvider — extract price from Shopee HTML without bypass.

This provider fetches the raw HTML from a Shopee product URL and attempts to
extract price data from structured markup (JSON-LD, meta tags, embedded JSON).

It does NOT attempt to:
- Bypass 403 responses
- Solve CAPTCHAs
- Handle login walls
- Use stealth or anti-detection techniques
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re

import requests

from src.parsers.shopee_provider import (
    ShopeePriceProvider,
    ShopeePriceResult,
    parse_shopee_url,
    _now_iso,
)

LOGGER = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class ShopeeHtmlFallbackProvider(ShopeePriceProvider):
    """Parse price from Shopee HTML — no anti-bot bypass."""

    name = "html_fallback"

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout

    def get_product_price(self, url: str) -> ShopeePriceResult:
        shop_id, item_id = parse_shopee_url(url)
        result = ShopeePriceResult(
            url=url,
            shop_id=shop_id,
            item_id=item_id,
            source=self.name,
            checked_at=_now_iso(),
        )

        if not shop_id or not item_id:
            result.status = "error"
            result.error_message = f"Cannot parse Shopee URL: {url}"
            return result

        # Fetch HTML
        try:
            resp = requests.get(
                url,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept-Language": "zh-TW,zh;q=0.9",
                },
                timeout=self.timeout,
                allow_redirects=True,
            )
        except requests.Timeout:
            result.status = "error"
            result.error_message = f"Timeout after {self.timeout}s"
            return result
        except Exception as exc:
            result.status = "error"
            result.error_message = str(exc)
            return result

        if resp.status_code in (401, 403, 429):
            result.status = "blocked"
            result.error_message = f"HTTP {resp.status_code}"
            return result

        if resp.status_code != 200:
            result.status = "error"
            result.error_message = f"HTTP {resp.status_code}"
            return result

        html_text = resp.text

        # Try extraction methods in order
        extractors = [
            ("json-ld", self._extract_json_ld),
            ("__NEXT_DATA__", self._extract_next_data),
            ("__INITIAL_STATE__", self._extract_initial_state),
            ("meta", self._extract_meta_price),
            ("regex", self._extract_regex_price),
        ]

        for method_name, extractor in extractors:
            try:
                price, title, raw = extractor(html_text)
                if price is not None:
                    result.price = price
                    result.title = title or result.title
                    result.status = "ok"
                    result.raw_data = {"method": method_name, "raw": raw}
                    LOGGER.info(
                        "Shopee HTML fallback found price via %s: $%.0f",
                        method_name, price,
                    )
                    return result
            except Exception as exc:
                LOGGER.debug("Shopee HTML %s extraction failed: %s", method_name, exc)

        # Extract title from <title> tag even if price not found
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
        if title_match:
            result.title = html_lib.unescape(title_match.group(1)).strip()
            if "|" in result.title:
                result.title = result.title.split("|")[0].strip()

        result.status = "price_unknown"
        result.error_message = "No price found in HTML"
        return result

    @staticmethod
    def _extract_json_ld(html_text: str) -> tuple[float | None, str, dict]:
        """Extract price from application/ld+json script blocks."""
        for match in re.finditer(
            r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
            html_text, re.I | re.S,
        ):
            try:
                data = json.loads(html_lib.unescape(match.group(1).strip()))
                price, title = _find_price_in_ld(data)
                if price is not None:
                    return price, title, data
            except (json.JSONDecodeError, ValueError):
                continue
        return None, "", {}

    @staticmethod
    def _extract_next_data(html_text: str) -> tuple[float | None, str, dict]:
        """Extract price from __NEXT_DATA__ script block."""
        match = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html_text, re.I | re.S,
        )
        if not match:
            return None, "", {}

        data = json.loads(match.group(1))
        price = _deep_find_price(data)
        title = _deep_find_field(data, "name") or _deep_find_field(data, "title") or ""
        return price, title, {"source": "__NEXT_DATA__"}

    @staticmethod
    def _extract_initial_state(html_text: str) -> tuple[float | None, str, dict]:
        """Extract price from window.__INITIAL_STATE__ or similar globals."""
        patterns = [
            r"window\.__INITIAL_STATE__\s*=\s*({.*?});",
            r"window\.__INITIAL_DATA__\s*=\s*({.*?});",
            r"window\.__PRELOADED_STATE__\s*=\s*({.*?});",
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, re.S)
            if match:
                try:
                    data = json.loads(match.group(1))
                    price = _deep_find_price(data)
                    title = _deep_find_field(data, "name") or ""
                    if price is not None:
                        return price, title, {"source": pattern.split("=")[0].strip()}
                except (json.JSONDecodeError, ValueError):
                    continue
        return None, "", {}

    @staticmethod
    def _extract_meta_price(html_text: str) -> tuple[float | None, str, dict]:
        """Extract price from <meta property='product:price:amount'>."""
        patterns = [
            r'<meta[^>]+property=["\']product:price:amount["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']product:price:amount["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, re.I)
            if match:
                try:
                    price = float(match.group(1).replace(",", ""))
                    if 50 <= price <= 500_000:
                        return price, "", {"source": "meta"}
                except ValueError:
                    continue
        return None, "", {}

    @staticmethod
    def _extract_regex_price(html_text: str) -> tuple[float | None, str, dict]:
        """Extract price from raw HTML text using regex patterns."""
        price_patterns = [
            re.compile(r'"price"\s*:\s*"?(\d+)"?'),
            re.compile(r'"price_min"\s*:\s*"?(\d+)"?'),
            re.compile(r'"price_max"\s*:\s*"?(\d+)"?'),
        ]
        for pattern in price_patterns:
            match = pattern.search(html_text)
            if match:
                raw_value = int(match.group(1))
                # Shopee uses microcents (÷100000)
                if raw_value > 1_000_000:
                    price = raw_value / 100_000
                else:
                    price = float(raw_value)
                if 50 <= price <= 500_000:
                    return price, "", {"source": "regex", "raw_value": raw_value}
        return None, "", {}


def _find_price_in_ld(data: dict | list) -> tuple[float | None, str]:
    """Recursively find price and name in JSON-LD data."""
    if isinstance(data, dict):
        name = data.get("name", "")
        offers = data.get("offers")
        if offers:
            if isinstance(offers, dict) and "price" in offers:
                try:
                    return float(offers["price"]), str(name)
                except (ValueError, TypeError):
                    pass
            elif isinstance(offers, list):
                for offer in offers:
                    if isinstance(offer, dict) and "price" in offer:
                        try:
                            return float(offer["price"]), str(name)
                        except (ValueError, TypeError):
                            continue
        for value in data.values():
            result = _find_price_in_ld(value)
            if result[0] is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _find_price_in_ld(item)
            if result[0] is not None:
                return result
    return None, ""


def _deep_find_price(data: dict | list, depth: int = 0) -> float | None:
    """Recursively find a price field in nested data."""
    if depth > 10:
        return None
    if isinstance(data, dict):
        for key in ("price", "price_min", "current_price", "sale_price"):
            if key in data:
                try:
                    val = float(data[key])
                    if val > 1_000_000:
                        val = val / 100_000
                    if 50 <= val <= 500_000:
                        return val
                except (ValueError, TypeError):
                    pass
        for value in data.values():
            result = _deep_find_price(value, depth + 1)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data[:20]:  # Limit list traversal
            result = _deep_find_price(item, depth + 1)
            if result is not None:
                return result
    return None


def _deep_find_field(data: dict | list, field: str, depth: int = 0) -> str:
    """Recursively find a string field in nested data."""
    if depth > 8:
        return ""
    if isinstance(data, dict):
        if field in data and isinstance(data[field], str) and data[field].strip():
            return data[field].strip()
        for value in data.values():
            result = _deep_find_field(value, field, depth + 1)
            if result:
                return result
    elif isinstance(data, list):
        for item in data[:10]:
            result = _deep_find_field(item, field, depth + 1)
            if result:
                return result
    return ""
