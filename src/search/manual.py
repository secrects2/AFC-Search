from __future__ import annotations

import csv
from pathlib import Path

from src.loader import Product
from src.matcher import match_score
from src.search.base import BaseSearchProvider, SearchResult


class ManualSearchProvider(BaseSearchProvider):
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.links = self._load_links(path) if path and path.exists() else []

    @staticmethod
    def _load_links(path: Path) -> list[SearchResult]:
        links: list[SearchResult] = []
        for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
            try:
                with path.open("r", encoding=encoding, newline="") as csv_file:
                    reader = csv.DictReader(csv_file)
                    for row in reader:
                        product_name = (row.get("product_name") or "").strip()
                        url = (row.get("url") or "").strip()
                        platform = (row.get("platform") or "manual").strip() or "manual"
                        if product_name and url:
                            links.append(SearchResult(product_name, url, platform))
                return links
            except UnicodeDecodeError:
                continue
        return links

    def search(self, product: Product, max_results: int) -> list[SearchResult]:
        matched = [
            link for link in self.links if match_score(product.product_name, link.product_name) >= 70
        ]
        return matched[:max_results]

