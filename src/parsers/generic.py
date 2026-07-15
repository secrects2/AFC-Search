from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from src.loader import parse_price_value
from src.ocr import capture_screenshot
from src.parsers.base import BaseParser, ParserOutput
from src.utils import sanitize_filename
from src.visual_price import VisualPriceExtractor


PRICE_PATTERNS = (
    re.compile(
        r"(?i)(?:NT\$|NTD|TWD|\$|售價|特價|促銷價|優惠價|價格)\s*[:：]?\s*\$?\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:元)?"
    ),
    re.compile(r"([0-9][0-9,]*(?:\.\d+)?)\s*元"),
)
IGNORE_BEFORE_RE = re.compile(r"(評價|銷售|售出|庫存|規格)\s*$")
IGNORE_AFTER_RE = re.compile(r"^\s*(折|%|粒|錠|顆|膠囊|日份|mg|ml|公克|克)")


class GenericParser(BaseParser):
    platform = "generic"

    def parse(self, url: str, output_dir: Path) -> ParserOutput:
        try:
            html_text = self.fetch_page(url, platform=self.platform)
        except PermissionError as exc:
            return ParserOutput(self.platform, url, parse_status="page_blocked", evidence_text=str(exc))
        except requests_timeout_errors() as exc:
            return ParserOutput(self.platform, url, parse_status="timeout", evidence_text=str(exc))
        except Exception as exc:
            return ParserOutput(self.platform, url, parse_status="search_failed", evidence_text=str(exc))

        title, raw_text, seller = self._extract_text_fields(html_text)
        image_urls = self._extract_image_urls(html_text, url)
        price, evidence = self.extract_price(html_text, raw_text)
        output = ParserOutput(
            platform=self.platform,
            url=url,
            title=title,
            price=price,
            seller=seller,
            raw_text=raw_text[:5000],
            parse_status="ok" if price is not None else "price_not_found",
            evidence_text=evidence,
            image_urls=image_urls,
        )
        output.raw_data["price_source"] = evidence if price is not None else "unknown"
        output.raw_data["final_url"] = self.last_fetched_url or url

        if price is None and (self.config.enable_screenshot or self.config.enable_ocr):
            if "findprice.com.tw/go/" in url:
                output.parse_status = "price_not_found"
                output.evidence_text = "FindPrice redirect unresolved"
                return output
                
            screenshot_dir = output_dir / "screenshots"
            screenshot_path = screenshot_dir / f"{sanitize_filename(title or self.platform)}.png"
            screenshot, screenshot_status = capture_screenshot(
                url, screenshot_path, headless=self.config.headless
            )
            output.screenshot_path = screenshot
            if screenshot_status == "ok" and self.config.enable_ocr:
                extractor = VisualPriceExtractor()
                visual_result = extractor.extract_from_screenshot(str(screenshot_path), platform=self.platform)
                
                output.ocr_status = "ok" if visual_result.method != "error" else "ocr_failed"
                output.ocr_text = visual_result.raw_text
                
                output.raw_data["visual_price_used"] = True
                output.raw_data["visual_price_method"] = visual_result.method
                output.raw_data["visual_price_raw_text"] = visual_result.raw_text
                output.raw_data["visual_price_confidence"] = visual_result.confidence
                
                if visual_result.price is not None:
                    output.price = visual_result.price
                    output.parse_status = "ok"
                    output.raw_data["price_source"] = "visual_ocr"
                    output.evidence_text = f"VisualOCR({visual_result.method}): {visual_result.price}"
                else:
                    output.raw_data["price_source"] = "unknown"
                    if visual_result.error_message:
                        output.evidence_text = f"VisualOCR Error: {visual_result.error_message}"
            else:
                output.ocr_status = "disabled" if screenshot_status == "disabled" else screenshot_status

        if output.price is not None and "price_source" not in output.raw_data:
            output.raw_data["price_source"] = "dom"

        return output

    def _extract_text_fields(self, html_text: str) -> tuple[str, str, str]:
        try:
            from bs4 import BeautifulSoup  # type: ignore

            soup = BeautifulSoup(html_text, "html.parser")
            title = self._first_meta(
                soup,
                [
                    ("property", "og:title"),
                    ("name", "title"),
                    ("name", "twitter:title"),
                ],
            )
            if not title and soup.title:
                title = soup.title.get_text(" ", strip=True)
            if not title:
                h1 = soup.find("h1")
                title = h1.get_text(" ", strip=True) if h1 else ""

            seller = self._first_meta(
                soup,
                [
                    ("property", "product:brand"),
                    ("name", "seller"),
                    ("name", "author"),
                ],
            )
            raw_text = soup.get_text(" ", strip=True)
            return html.unescape(title), raw_text, html.unescape(seller)
        except Exception:
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
            title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
            raw_text = re.sub(r"<[^>]+>", " ", html_text)
            raw_text = html.unescape(re.sub(r"\s+", " ", raw_text)).strip()
            return html.unescape(title), raw_text, ""

    def _extract_image_urls(self, html_text: str, base_url: str) -> list[str]:
        urls: list[str] = []
        try:
            from bs4 import BeautifulSoup  # type: ignore

            soup = BeautifulSoup(html_text, "html.parser")
            meta_attrs = [
                ("property", "og:image"),
                ("property", "og:image:secure_url"),
                ("name", "twitter:image"),
                ("itemprop", "image"),
            ]
            for attr_name, attr_value in meta_attrs:
                tag = soup.find("meta", attrs={attr_name: attr_value})
                if tag and tag.get("content"):
                    urls.append(str(tag["content"]).strip())

            for tag in soup.find_all("img"):
                src = tag.get("src") or tag.get("data-src") or tag.get("data-original")
                if src:
                    urls.append(str(src).strip())
                srcset = tag.get("srcset")
                if srcset:
                    first = str(srcset).split(",")[0].strip().split(" ")[0]
                    if first:
                        urls.append(first)
        except Exception:
            pattern = (
                r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']'
                r'[^>]+content=["\']([^"\']+)["\']'
            )
            for match in re.finditer(pattern, html_text, re.I):
                urls.append(match.group(1).strip())

        deduped: list[str] = []
        seen: set[str] = set()
        for value in urls:
            absolute = urljoin(base_url, html.unescape(value))
            if absolute.lower().startswith(("http://", "https://")) and absolute not in seen:
                deduped.append(absolute)
                seen.add(absolute)
            if len(deduped) >= 20:
                break
        return deduped

    @staticmethod
    def _first_meta(soup: Any, attrs: list[tuple[str, str]]) -> str:
        for attr_name, attr_value in attrs:
            tag = soup.find("meta", attrs={attr_name: attr_value})
            if tag and tag.get("content"):
                return str(tag["content"]).strip()
        return ""

    @classmethod
    def extract_price(cls, html_text: str, raw_text: str) -> tuple[float | None, str]:
        structured_price = cls._extract_json_ld_price(html_text)
        if structured_price is not None:
            return structured_price, "json-ld offers.price"

        meta_price = cls._extract_meta_price(html_text)
        if meta_price is not None:
            return meta_price, "meta price"

        candidates: list[tuple[float, str]] = []
        for pattern in PRICE_PATTERNS:
            for match in pattern.finditer(raw_text):
                candidate = parse_price_value(match.group(1))
                if candidate is None:
                    continue
                before = raw_text[max(0, match.start() - 8) : match.start()]
                after = raw_text[match.end() : match.end() + 8]
                context = raw_text[max(0, match.start() - 12) : match.end() + 12]
                if cls._is_plausible_price(candidate, before, after):
                    candidates.append((candidate, context.strip()))

        if not candidates:
            return None, ""
        best = min(candidates, key=lambda item: item[0])
        return best[0], best[1]

    @classmethod
    def _is_plausible_price(cls, value: float, before: str, after: str) -> bool:
        if value < 50 or value > 500000:
            return False
        if IGNORE_BEFORE_RE.search(before):
            return False
        return not IGNORE_AFTER_RE.search(after)

    @staticmethod
    def _extract_meta_price(html_text: str) -> float | None:
        patterns = (
            r'<meta[^>]+property=["\']product:price:amount["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']product:price:amount["\']',
        )
        for pattern in patterns:
            match = re.search(pattern, html_text, re.I)
            if match:
                price = parse_price_value(match.group(1))
                if price is not None:
                    return price
        return None

    @staticmethod
    def _extract_json_ld_price(html_text: str) -> float | None:
        for match in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html_text,
            re.I | re.S,
        ):
            try:
                payload = json.loads(html.unescape(match.group(1).strip()))
            except Exception:
                continue
            price = find_price_in_json(payload)
            if price is not None:
                return price
        return None


def find_price_in_json(value: Any) -> float | None:
    if isinstance(value, dict):
        if "price" in value:
            price = parse_price_value(value["price"])
            if price is not None:
                return price
        for nested in value.values():
            price = find_price_in_json(nested)
            if price is not None:
                return price
    elif isinstance(value, list):
        for item in value:
            price = find_price_in_json(item)
            if price is not None:
                return price
    return None


def requests_timeout_errors() -> tuple[type[BaseException], ...]:
    try:
        import requests

        return (requests.Timeout,)
    except Exception:
        return (TimeoutError,)
