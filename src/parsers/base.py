from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, unquote

import requests

from src.config import AppConfig


LOGGER = logging.getLogger(__name__)


@dataclass
class ParserOutput:
    platform: str
    url: str
    title: str = ""
    price: float | None = None
    seller: str = ""
    raw_text: str = ""
    screenshot_path: str = ""
    parse_status: str = "ok"
    ocr_status: str = "disabled"
    evidence_text: str = ""
    ocr_text: str = ""
    image_urls: list[str] | None = None
    image_match_status: str = ""
    image_match_score: int = 0
    raw_data: dict[str, Any] = field(default_factory=dict)


class BaseParser:
    platform = "generic"

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.last_fetched_url = ""

    def parse(self, url: str, output_dir: Path) -> ParserOutput:
        raise NotImplementedError

    def fetch_page(self, url: str, platform: str = "") -> str:
        local_text = self._read_local_file(url)
        if local_text is not None:
            self.last_fetched_url = url
            return local_text

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36 AFCPriceMonitor/1.0"
            )
        }
        if (platform or "").lower() == "pchome":
            # PChome currently rate-limits the monitor-specific UA. Let
            # requests use its default UA for the direct public page fetch.
            headers = {}

        # Determine if proxy should be used for this platform
        proxies = None
        proxy_url = self.config.proxy_url
        if proxy_url and proxy_url.lower() != "auto":
            proxy_platforms = self.config.proxy_platforms
            should_proxy = not proxy_platforms or (
                platform and platform.lower() in [p.lower() for p in proxy_platforms]
            )
            if should_proxy:
                proxies = {"http": proxy_url, "https": proxy_url}
                LOGGER.debug("Using proxy for %s: %s", platform or url[:40], proxy_url[:30])

        fast_fail_domains = ["pchome.com.tw", "coupang.onelink.me", "tw.coupang.com", "shopee.tw"]

        last_error: Exception | None = None
        for attempt in range(self.config.request_retries + 1):
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=self.config.request_timeout_seconds,
                    allow_redirects=True,
                    proxies=proxies,
                )
                if response.status_code in {401, 403, 429}:
                    raise PermissionError(f"page_blocked: HTTP {response.status_code}")
                response.raise_for_status()
                response.encoding = response.encoding or "utf-8"
                self.last_fetched_url = response.url or url
                return response.text
            except PermissionError as exc:
                last_error = exc
                LOGGER.warning("Fetch failed with permission error: %s", exc)
                if any(domain in url for domain in fast_fail_domains):
                    LOGGER.info("Fast-failing for known blocked domain: %s", url[:60])
                    break
                if attempt < self.config.request_retries:
                    time.sleep(min(self.config.request_delay_seconds, 5))
            except Exception as exc:
                last_error = exc
                LOGGER.warning("Fetch failed for %s on attempt %s: %s", url, attempt + 1, exc)
                if attempt < self.config.request_retries:
                    time.sleep(min(self.config.request_delay_seconds, 5))

        # ScraperAPI fallback — when direct fetch fails with 429/403
        skip_scraperapi_domains = ["pchome.com.tw", "coupang.onelink.me", "tw.coupang.com", "shopee.tw"]
        if any(domain in url for domain in skip_scraperapi_domains):
            scraperapi_key = ""
            LOGGER.info("Skipping ScraperAPI for known blocked domain: %s", url[:60])
        else:
            scraperapi_key = self._get_scraperapi_key()

        if scraperapi_key and isinstance(last_error, PermissionError):
            try:
                from src.scraper_api import fetch_via_scraperapi
                LOGGER.info("Direct fetch blocked, trying ScraperAPI: %s", url[:60])
                self.last_fetched_url = url
                return fetch_via_scraperapi(url, scraperapi_key, timeout=30)
            except Exception as scraper_exc:
                LOGGER.warning("ScraperAPI fallback also failed: %s", scraper_exc)

        if last_error:
            raise last_error
        raise RuntimeError(f"Unable to fetch page: {url}")

    def _get_scraperapi_key(self) -> str:
        """Get ScraperAPI key from env."""
        import os
        return os.environ.get("SCRAPERAPI_KEY", "").strip()

    @staticmethod
    def _read_local_file(url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.scheme == "file":
            path = Path(unquote(parsed.path.lstrip("/")))
            if len(parsed.path) >= 3 and parsed.path[0] == "/" and parsed.path[2] == ":":
                path = Path(unquote(parsed.path[1:]))
            return path.read_text(encoding="utf-8")

        path = Path(url)
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
        return None
