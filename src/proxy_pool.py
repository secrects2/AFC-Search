"""Free proxy rotation — fetch, test, and rotate through free proxy IPs.

Used as a fallback when no paid proxy is configured. Fetches proxies from
public free-proxy APIs, tests them, caches working ones, and rotates.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

LOGGER = logging.getLogger(__name__)

# Public free proxy APIs
_PROXY_SOURCES = [
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=yes&anonymity=all",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
]

# How many proxies to test at once
_TEST_BATCH = 15
# Max working proxies to keep
_MAX_POOL = 8
# How long a proxy is considered fresh (seconds)
_FRESHNESS = 1800  # 30 min
# Test timeout
_TEST_TIMEOUT = 8


@dataclass
class ProxyEntry:
    url: str
    last_tested: float = 0.0
    success_count: int = 0
    fail_count: int = 0
    avg_latency: float = 99.0


class FreeProxyPool:
    """Manages a pool of free rotating proxies."""

    def __init__(self, cache_path: Path | None = None) -> None:
        self._pool: list[ProxyEntry] = []
        self._lock = threading.Lock()
        self._last_fetch = 0.0
        self._cache_path = cache_path or Path("data/proxy_cache.json")

        # Load cached proxies
        self._load_cache()

    def get_proxy(self) -> str | None:
        """Get a working proxy URL, or None if no proxy available."""
        with self._lock:
            # Refresh if pool is empty or stale
            if not self._pool or (time.time() - self._last_fetch > _FRESHNESS):
                self._refresh()

            if not self._pool:
                return None

            # Pick a random proxy weighted by success rate
            working = [p for p in self._pool if p.fail_count < 3]
            if not working:
                # All failed, refresh
                self._refresh()
                working = self._pool

            if not working:
                return None

            # Prefer proxies with better success rate and lower latency
            working.sort(key=lambda p: (-p.success_count, p.avg_latency))
            # Pick from top 3 randomly for variety
            pick = random.choice(working[:min(3, len(working))])
            return f"http://{pick.url}"

    def report_success(self, proxy_url: str, latency: float = 1.0) -> None:
        """Report that a proxy worked."""
        addr = proxy_url.replace("http://", "").replace("https://", "")
        with self._lock:
            for p in self._pool:
                if p.url == addr:
                    p.success_count += 1
                    p.avg_latency = (p.avg_latency + latency) / 2
                    break
            self._save_cache()

    def report_failure(self, proxy_url: str) -> None:
        """Report that a proxy failed."""
        addr = proxy_url.replace("http://", "").replace("https://", "")
        with self._lock:
            for p in self._pool:
                if p.url == addr:
                    p.fail_count += 1
                    # Remove if too many failures
                    if p.fail_count >= 3:
                        self._pool.remove(p)
                    break
            self._save_cache()

    def _refresh(self) -> None:
        """Fetch new proxies and test them."""
        LOGGER.info("正在抓取免費代理 IP...")
        raw_proxies = self._fetch_raw_proxies()
        if not raw_proxies:
            LOGGER.warning("無法取得免費代理清單")
            return

        # Shuffle and take a batch to test
        random.shuffle(raw_proxies)
        candidates = raw_proxies[:_TEST_BATCH]

        LOGGER.info("測試 %d 個代理 IP...", len(candidates))
        working = self._test_proxies(candidates)

        # Merge with existing pool
        existing_urls = {p.url for p in self._pool}
        for proxy in working:
            if proxy.url not in existing_urls:
                self._pool.append(proxy)

        # Keep only the best
        self._pool.sort(key=lambda p: (-p.success_count, p.avg_latency))
        self._pool = self._pool[:_MAX_POOL]

        self._last_fetch = time.time()
        self._save_cache()
        LOGGER.info("代理池更新完成：%d 個可用代理", len(self._pool))

    def _fetch_raw_proxies(self) -> list[str]:
        """Fetch proxy IPs from public APIs."""
        proxies: set[str] = set()
        for source_url in _PROXY_SOURCES:
            try:
                resp = requests.get(source_url, timeout=10)
                if resp.status_code == 200:
                    for line in resp.text.strip().splitlines():
                        line = line.strip()
                        if line and ":" in line and not line.startswith("#"):
                            # Validate format: ip:port
                            parts = line.split(":")
                            if len(parts) == 2 and parts[1].isdigit():
                                proxies.add(line)
            except Exception as exc:
                LOGGER.debug("代理來源 %s 失敗: %s", source_url[:40], exc)
        LOGGER.info("從 %d 個來源抓到 %d 個候選代理", len(_PROXY_SOURCES), len(proxies))
        return list(proxies)

    def _test_proxies(self, candidates: list[str]) -> list[ProxyEntry]:
        """Test proxies concurrently and return working ones."""
        working: list[ProxyEntry] = []
        # Test URL: use httpbin or a lightweight endpoint
        test_url = "https://httpbin.org/ip"

        for addr in candidates:
            proxy_dict = {"http": f"http://{addr}", "https": f"http://{addr}"}
            start = time.time()
            try:
                resp = requests.get(
                    test_url,
                    proxies=proxy_dict,
                    timeout=_TEST_TIMEOUT,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                latency = time.time() - start
                if resp.status_code == 200:
                    working.append(ProxyEntry(
                        url=addr,
                        last_tested=time.time(),
                        success_count=1,
                        avg_latency=latency,
                    ))
                    LOGGER.debug("✅ 代理 %s (%.1fs)", addr, latency)
                    if len(working) >= _MAX_POOL:
                        break
            except Exception:
                pass  # Skip failed proxies silently

        LOGGER.info("測試結果：%d/%d 個代理可用", len(working), len(candidates))
        return working

    def _save_cache(self) -> None:
        """Save working proxies to disk."""
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            data = [
                {
                    "url": p.url,
                    "success_count": p.success_count,
                    "fail_count": p.fail_count,
                    "avg_latency": round(p.avg_latency, 2),
                    "last_tested": p.last_tested,
                }
                for p in self._pool
            ]
            self._cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            LOGGER.debug("代理快取儲存失敗: %s", exc)

    def _load_cache(self) -> None:
        """Load cached proxies from disk."""
        if not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            for item in data:
                self._pool.append(ProxyEntry(
                    url=item["url"],
                    success_count=item.get("success_count", 0),
                    fail_count=item.get("fail_count", 0),
                    avg_latency=item.get("avg_latency", 99.0),
                    last_tested=item.get("last_tested", 0),
                ))
            if self._pool:
                LOGGER.info("載入 %d 個快取代理", len(self._pool))
                self._last_fetch = max(p.last_tested for p in self._pool)
        except Exception as exc:
            LOGGER.debug("代理快取載入失敗: %s", exc)


# Global singleton
_pool_instance: FreeProxyPool | None = None
_pool_lock = threading.Lock()


def get_free_proxy_pool(cache_path: Path | None = None) -> FreeProxyPool:
    """Get the global FreeProxyPool singleton."""
    global _pool_instance
    with _pool_lock:
        if _pool_instance is None:
            _pool_instance = FreeProxyPool(cache_path)
        return _pool_instance
