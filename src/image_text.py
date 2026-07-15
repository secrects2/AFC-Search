from __future__ import annotations

import io
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import requests


IMAGE_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36 AFCPriceMonitor/1.0"
    ),
}


@dataclass(frozen=True)
class ImageTextScanResult:
    status: str
    marker: str = ""
    matched_url: str = ""
    raw_text: str = ""
    checked_urls: int = 0
    error_message: str = ""

    @property
    def matched(self) -> bool:
        return self.status == "matched"

    def as_raw_data(self) -> dict[str, Any]:
        return {
            "image_text_marker": self.marker,
            "image_text_status": self.status,
            "image_text_checked_urls": self.checked_urls,
            "image_text_matched_url": self.matched_url,
            "image_text_ocr": self.raw_text[:1000],
            "image_text_error": self.error_message[:300],
        }


def normalize_image_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", "", normalized)


def _ocr_image_bytes(image_bytes: bytes) -> str:
    from PIL import Image  # type: ignore
    import pytesseract  # type: ignore

    if os.name == "nt":
        for executable in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ):
            if os.path.exists(executable):
                pytesseract.pytesseract.tesseract_cmd = executable
                break

    with Image.open(io.BytesIO(image_bytes)) as image:
        return pytesseract.image_to_string(image, lang="chi_tra+eng")


def scan_image_urls_for_text(
    image_urls: list[str] | None,
    marker: str = "官方",
    timeout_seconds: int = 15,
    max_candidates: int = 6,
) -> ImageTextScanResult:
    """Scan product images for a visible text marker using optional OCR."""
    urls: list[str] = []
    seen: set[str] = set()
    for value in image_urls or []:
        url = str(value or "").strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
        if len(urls) >= max_candidates:
            break

    if not urls:
        return ImageTextScanResult("no_candidate_image", marker=marker)

    try:
        import pytesseract  # type: ignore  # noqa: F401
        from PIL import Image  # type: ignore  # noqa: F401
    except Exception as exc:
        return ImageTextScanResult(
            "ocr_unavailable",
            marker=marker,
            error_message=str(exc),
        )

    checked_urls = 0
    successful_ocr = 0
    last_error = ""
    for url in urls:
        try:
            response = requests.get(
                url,
                headers=IMAGE_REQUEST_HEADERS,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            checked_urls += 1
            text = _ocr_image_bytes(response.content)
            successful_ocr += 1
            if normalize_image_text(marker) in normalize_image_text(text):
                return ImageTextScanResult(
                    "matched",
                    marker=marker,
                    matched_url=url,
                    raw_text=text,
                    checked_urls=checked_urls,
                )
        except Exception as exc:
            last_error = str(exc)

    if successful_ocr:
        return ImageTextScanResult(
            "not_matched",
            marker=marker,
            checked_urls=checked_urls,
            error_message=last_error,
        )
    return ImageTextScanResult(
        "ocr_failed" if checked_urls else "image_fetch_failed",
        marker=marker,
        checked_urls=checked_urls,
        error_message=last_error,
    )
