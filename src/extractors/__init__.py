"""Product page extractors — extract price/title from e-commerce URLs.

Wraps the existing parsers module with the new ProductPageExtractor interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import AppConfig, load_config
from src.parsers import get_parser


@dataclass
class ExtractionResult:
    """Unified extraction result from a product page."""
    title: str = ""
    price: float | None = None
    seller: str = ""
    availability: str = "unknown"
    screenshot_path: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""
    platform: str = ""
    image_urls: list[str] = field(default_factory=list)
    parse_status: str = "ok"


class ProductPageExtractor:
    """Extract product details from an e-commerce page URL.

    Delegates to the existing parser infrastructure (parsers/generic.py etc.)
    which handles HTML fetching, JSON-LD, meta tags, price regex, etc.
    """

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or load_config(Path("config.yaml"))

    def extract(
        self, url: str, platform: str = "", screenshot_dir: Path | None = None
    ) -> ExtractionResult:
        """Extract price/title from a URL. Never raises — errors in result."""
        output_dir = screenshot_dir or Path("output/screenshots")
        output_dir.mkdir(parents=True, exist_ok=True)

        plat = platform or "generic"
        try:
            parser = get_parser(plat, url, self.config)
            output = parser.parse(url, output_dir)
            merged_raw_data = {"evidence_text": output.evidence_text, "ocr_status": output.ocr_status}
            merged_raw_data.update(output.raw_data)
            merged_raw_data["final_url"] = str(
                merged_raw_data.get("final_url")
                or merged_raw_data.get("rendered_url")
                or getattr(parser, "last_fetched_url", "")
                or url
            )
            
            return ExtractionResult(
                title=output.title,
                price=output.price,
                seller=output.seller,
                screenshot_path=output.screenshot_path,
                raw_data=merged_raw_data,
                error_message="" if output.parse_status == "ok" else output.evidence_text,
                platform=output.platform or plat,
                image_urls=output.image_urls or [],
                parse_status=output.parse_status,
                availability="available" if output.price is not None else "unknown",
            )
        except Exception as exc:
            return ExtractionResult(
                error_message=str(exc),
                platform=plat,
                parse_status="error",
                availability="error",
            )


# Platform-specific aliases (all delegate to the same extractor since
# the existing parsers/generic.py already handles platform differences)
ShopeeExtractor = ProductPageExtractor
MomoExtractor = ProductPageExtractor
RutenExtractor = ProductPageExtractor
PchomeExtractor = ProductPageExtractor
YahooExtractor = ProductPageExtractor
VivaExtractor = ProductPageExtractor
GenericExtractor = ProductPageExtractor


def get_extractor(platform: str, config: AppConfig | None = None) -> ProductPageExtractor:
    """Get the appropriate extractor for a platform."""
    return ProductPageExtractor(config)
