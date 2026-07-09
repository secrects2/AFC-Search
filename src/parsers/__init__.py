from __future__ import annotations

from urllib.parse import urlparse

from src.config import AppConfig
from src.parsers.base import BaseParser
from src.parsers.generic import GenericParser
from src.parsers.momo import MomoParser
from src.parsers.pchome import PChomeParser
from src.parsers.ruten import RutenParser
from src.parsers.shopee import ShopeeParser
from src.parsers.yahoo import YahooParser


def get_parser(platform: str, url: str, config: AppConfig) -> BaseParser:
    normalized = (platform or "").lower()
    host = urlparse(url).netloc.lower()
    if "shopee" in normalized or "shopee.tw" in host:
        return ShopeeParser(config)
    if "momo" in normalized or "momo.com.tw" in host:
        return MomoParser(config)
    if "yahoo" in normalized or "yahoo" in host:
        return YahooParser(config)
    if "pchome" in normalized or "pchome" in host:
        return PChomeParser(config)
    if normalized == "ruten" or "ruten.com.tw" in host:
        return RutenParser(config)
    return GenericParser(config)


__all__ = ["get_parser"]

