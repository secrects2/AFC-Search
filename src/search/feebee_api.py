"""Feebee price comparison parsing and search logic."""

import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup
from src.matcher import match_score, normalize_name

LOGGER = logging.getLogger(__name__)
FEEBEE_BASE = "https://feebee.com.tw"

@dataclass
class FeebeeListing:
    title: str
    url: str
    platform: str
    seller: str
    price: float | None
    price_text: str

def _detect_platform_from_merchant(merchant: str, url: str) -> str:
    merchant_lower = merchant.lower()
    url_lower = url.lower()

    if "shopee" in url_lower or "蝦皮" in merchant_lower:
        return "shopee"
    if "coupang" in url_lower or "酷澎" in merchant_lower:
        return "coupang"
    if "pchome" in url_lower or "pchome" in merchant_lower:
        return "pchome"
    if "momo" in url_lower or "momo" in merchant_lower:
        return "momo"
    if "yahoo" in url_lower or "雅虎" in merchant_lower or "奇摩" in merchant_lower:
        return "yahoo"
    if "rakuten" in url_lower or "樂天" in merchant_lower:
        return "rakuten"
    if "ruten" in url_lower or "露天" in merchant_lower:
        return "ruten"
    if "payeasy" in url_lower or "payeasy" in merchant_lower:
        return "payeasy"
    if "etmall" in url_lower or "東森" in merchant_lower:
        return "etmall"
    if "friday" in url_lower or "friday" in merchant_lower:
        return "friday"
    if "pcone" in url_lower or "松果" in merchant_lower:
        return "pcone"

    return "other"

def _parse_price_text(text: str) -> float | None:
    digits = re.sub(r"[^\d]", "", text)
    if digits:
        try:
            return float(digits)
        except ValueError:
            return None
    return None

def parse_feebee_html(html: str, max_results: int = 20) -> list[FeebeeListing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[FeebeeListing] = []
    seen: set[tuple[str, str, str]] = set()

    # Feebee items are typically in li.js-item-container or similar
    for item in soup.select("li.js-item-container, li.items"):
        title = item.get("data-title")
        if not title:
            # Fallback to text inside .items-title or h3
            title_el = item.select_one(".items-title, h3.items-title, h3")
            if title_el:
                title = title_el.get_text(strip=True)
                
        if not title:
            continue

        url = item.get("data-url")
        if not url:
            link_el = item.select_one("a[href]")
            if link_el:
                url = link_el.get("href")
                
        if not url:
            continue
            
        if url.startswith("/"):
            url = f"{FEEBEE_BASE}{url}"

        price_text = item.get("data-price")
        if not price_text:
            price_el = item.select_one(".price, .items-price")
            if price_el:
                price_text = price_el.get_text(strip=True)
                
        price = _parse_price_text(price_text or "")

        seller = item.get("data-store")
        if not seller:
            shop_el = item.select_one(".shop, .items-shop")
            if shop_el:
                seller = shop_el.get_text(strip=True)
                
        seller = seller or ""
        platform = _detect_platform_from_merchant(seller, url)

        listing = FeebeeListing(
            title=" ".join(title.split()),
            url=url,
            platform=platform,
            seller=" ".join(seller.split()),
            price=price,
            price_text=" ".join((price_text or "").split()),
        )

        key = (listing.url, listing.title, listing.platform)
        if key in seen:
            continue
        seen.add(key)
        listings.append(listing)
        if len(listings) >= max_results:
            break

    return listings

def search_feebee_listings(
    keyword: str,
    max_results: int = 20,
    timeout: int = 15,
    delay_seconds: float = 1.0,
) -> list[FeebeeListing]:
    LOGGER.info("Executing Feebee search for keyword: %s", keyword)
    if delay_seconds > 0:
        time.sleep(delay_seconds)

    url = f"{FEEBEE_BASE}/s/?q={urllib.parse.quote(keyword)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        LOGGER.warning("Feebee search failed for keyword '%s': %s", keyword, exc)
        return []

    listings = parse_feebee_html(html, max_results=max_results)
    LOGGER.info("Feebee found %d candidate listings for %s", len(listings), keyword)
    return listings

def _clean_query_text(value: str) -> str:
    text = re.sub(r"https?://\S+", " ", value or "")
    text = re.sub(r"[【】〖〗\[\]（）()|｜,，、/]+", " ", text)
    text = re.sub(r"\d+\s*(粒|錠|顆|包|盒|瓶|日份|個月份)", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def _query_variants(keyword: str, expected_title: str = "") -> list[str]:
    variants: list[str] = []

    def add(value: str) -> None:
        value = _clean_query_text(value)
        if value and value not in variants:
            variants.append(value)

    add(keyword)
    if expected_title:
        add(expected_title)

    for value in (keyword, expected_title):
        cleaned = _clean_query_text(value)
        if not cleaned:
            continue
        without_afc = re.sub(r"\bafc\b", " ", cleaned, flags=re.IGNORECASE)
        without_afc = re.sub(r"\s+", " ", without_afc).strip()
        add(without_afc)

        normalized = normalize_name(cleaned)
        if normalized and len(normalized) >= 3:
            add(normalized)
            add(f"AFC {normalized}")

    return variants[:6]

def _dedupe_listings(listings: list[FeebeeListing]) -> list[FeebeeListing]:
    deduped: list[FeebeeListing] = []
    seen: set[tuple[str, str, str]] = set()
    for listing in listings:
        key = (listing.url, listing.title, listing.platform)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(listing)
    return deduped

def _essential_terms(value: str) -> list[str]:
    normalized = normalize_name(value)
    if not normalized:
        return []

    for marker in ("新究極", "究極", "菁鑽"):
        index = normalized.find(marker)
        if index < 0:
            continue
        tail = normalized[index + len(marker):]
        if marker == "菁鑽" and tail.startswith("新"):
            tail = tail[1:]
        terms = [marker]
        if len(tail) >= 2:
            terms.append(tail)
        return terms

    return []

def _passes_essential_terms(
    listing: FeebeeListing,
    keyword: str,
    expected_title: str = "",
) -> bool:
    terms = _essential_terms(keyword) or _essential_terms(expected_title)
    if not terms:
        return True
    title_norm = normalize_name(listing.title)
    return all(term in title_norm for term in terms)

def find_best_feebee_listing(
    keyword: str,
    expected_title: str = "",
    preferred_platform: str = "",
    min_score: int = 50,
    timeout: int = 15,
) -> Optional[FeebeeListing]:
    listings: list[FeebeeListing] = []
    for query in _query_variants(keyword, expected_title):
        listings.extend(
            listing
            for listing in search_feebee_listings(
                query,
                max_results=30,
                timeout=timeout,
                delay_seconds=0.2,
            )
            if listing.price is not None
        )
    listings = _dedupe_listings(listings)
    if not listings:
        return None

    scored: list[tuple[int, int, FeebeeListing]] = []
    for listing in listings:
        if not _passes_essential_terms(listing, keyword, expected_title):
            continue
        base_score = match_score(keyword, listing.title)
        if expected_title:
            base_score = max(base_score, match_score(expected_title, listing.title))
        if base_score < min_score:
            continue
        platform_bonus = 20 if preferred_platform and listing.platform == preferred_platform else 0
        scored.append((base_score + platform_bonus, base_score, listing))

    if not scored:
        best = max(
            listings,
            key=lambda listing: max(
                match_score(keyword, listing.title),
                match_score(expected_title, listing.title) if expected_title else 0,
            ),
        )
        LOGGER.info(
            "Feebee no match above threshold: keyword=%s best_title=%s",
            keyword,
            best.title,
        )
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    best_listing = scored[0][2]
    LOGGER.info(
        "Feebee best match: score=%d title=%s url=%s",
        scored[0][0],
        best_listing.title[:60],
        best_listing.url[:60],
    )
    return best_listing
