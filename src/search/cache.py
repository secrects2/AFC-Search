"""24-hour search result cache.

Caches search results to disk (JSON) to avoid redundant API calls.
Results expire after a configurable number of hours (default 24).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.search.base import SearchResult

LOGGER = logging.getLogger(__name__)


CACHE_VERSION = "v2"


def _cache_key(product_name: str) -> str:
    """Generate a stable cache key from product name."""
    return hashlib.sha256(f"{CACHE_VERSION}:{product_name}".encode("utf-8")).hexdigest()[:16]


def _result_to_dict(r: SearchResult) -> dict[str, Any]:
    return {
        "product_name": r.product_name,
        "url": r.url,
        "snippet": r.snippet,
        "platform": r.platform,
        "source": r.source,
        "rank": r.rank,
        "searched_at": r.searched_at,
        "found_price": r.found_price,
        "seller": r.seller,
        "raw_data": r.raw_data,
    }


def _dict_to_result(d: dict[str, Any], cached: bool = True) -> SearchResult:
    return SearchResult(
        product_name=d.get("product_name", ""),
        url=d.get("url", ""),
        snippet=d.get("snippet", ""),
        platform=d.get("platform", ""),
        source=d.get("source", ""),
        rank=d.get("rank", 0),
        cached=cached,
        searched_at=d.get("searched_at", ""),
        found_price=d.get("found_price"),
        seller=d.get("seller", ""),
        raw_data=d.get("raw_data", {}) or {},
    )


class SearchCache:
    """File-based search result cache with TTL."""

    def __init__(self, cache_path: Path, ttl_hours: int = 24) -> None:
        self.cache_path = cache_path
        self.ttl_hours = ttl_hours
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _is_expired(self, entry: dict[str, Any]) -> bool:
        stored_at = entry.get("stored_at", "")
        if not stored_at:
            return True
        try:
            stored = datetime.fromisoformat(stored_at)
            now = datetime.now(timezone.utc)
            age_hours = (now - stored).total_seconds() / 3600
            return age_hours > self.ttl_hours
        except Exception:
            return True

    def get(self, product_name: str) -> list[SearchResult] | None:
        """Return cached results for a product, or None if miss/expired."""
        key = _cache_key(product_name)
        entry = self._data.get(key)
        if entry is None or self._is_expired(entry):
            return None
        results = [_dict_to_result(d, cached=True) for d in entry.get("results", [])]
        LOGGER.debug("快取命中：%s (%d 筆)", product_name, len(results))
        return results

    def put(self, product_name: str, results: list[SearchResult]) -> None:
        """Store search results in cache."""
        key = _cache_key(product_name)
        self._data[key] = {
            "product_name": product_name,
            "stored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": results[0].source if results else "",
            "results": [_result_to_dict(r) for r in results],
        }
        self._save()

    def clear_expired(self) -> int:
        """Remove all expired entries. Returns count of removed entries."""
        expired_keys = [k for k, v in self._data.items() if self._is_expired(v)]
        for key in expired_keys:
            del self._data[key]
        if expired_keys:
            self._save()
        return len(expired_keys)

    @property
    def size(self) -> int:
        return len(self._data)
