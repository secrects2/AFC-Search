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

    def parse(self, url: str, output_dir: Path) -> ParserOutput:
        raise NotImplementedError

    def fetch_page(self, url: str) -> str:
        local_text = self._read_local_file(url)
        if local_text is not None:
            return local_text

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36 AFCPriceMonitor/1.0"
            )
        }
        last_error: Exception | None = None
        for attempt in range(self.config.request_retries + 1):
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=self.config.request_timeout_seconds,
                    allow_redirects=True,
                )
                if response.status_code in {401, 403, 429}:
                    raise PermissionError(f"page_blocked: HTTP {response.status_code}")
                response.raise_for_status()
                response.encoding = response.encoding or "utf-8"
                return response.text
            except Exception as exc:
                last_error = exc
                LOGGER.warning("Fetch failed for %s on attempt %s: %s", url, attempt + 1, exc)
                if attempt < self.config.request_retries:
                    time.sleep(min(self.config.request_delay_seconds, 5))
        if last_error:
            raise last_error
        raise RuntimeError(f"Unable to fetch page: {url}")

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
