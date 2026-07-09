"""Tests for search providers: SerpAPI, Brave, Chain, and Cache."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from src.loader import Product
from src.search.base import SearchResult
from src.search.brave_search import BraveSearchProvider
from src.search.cache import SearchCache
from src.search.search_api import ChainSearchProvider, build_chain_provider
from src.search.serp_api import SerpAPIProvider, detect_platform


# --- Platform detection ---

def test_detect_platform_shopee() -> None:
    assert detect_platform("https://shopee.tw/product/123") == "shopee"


def test_detect_platform_momo() -> None:
    assert detect_platform("https://www.momo.com.tw/item/123") == "momo"


def test_detect_platform_pchome() -> None:
    assert detect_platform("https://24h.pchome.com.tw/prod/ABC") == "pchome"


def test_detect_platform_ruten() -> None:
    assert detect_platform("https://www.ruten.com.tw/item/show?123") == "ruten"


def test_detect_platform_yahoo() -> None:
    assert detect_platform("https://tw.buy.yahoo.com/gdsale/123") == "yahoo"


def test_detect_platform_unknown() -> None:
    assert detect_platform("https://unknown-shop.com/item") == "other"


# --- SerpAPI provider ---

def _make_product(name: str = "GENKI元氣習慣") -> Product:
    return Product(
        suggested_price=1380,
        product_name=name,
        row_index=1,
        raw_suggested_price="1380",
    )


def test_serpapi_disabled_without_key() -> None:
    provider = SerpAPIProvider(api_key="", platforms=["shopee"])
    assert not provider.enabled
    assert provider.search(_make_product(), 5) == []


def test_serpapi_parses_results() -> None:
    mock_data = {
        "organic_results": [
            {"title": "GENKI - Shopee", "link": "https://shopee.tw/item/999", "snippet": "好評推薦"},
            {"title": "GENKI Blog", "link": "https://blog.example.com/genki", "snippet": "介紹"},
            {"title": "GENKI - Momo", "link": "https://www.momo.com.tw/goods/456", "snippet": "限時特價"},
        ]
    }

    provider = SerpAPIProvider(api_key="test", platforms=["shopee", "momo"])

    with patch("src.search.serp_api.urllib.request.urlopen") as mock_urlopen:
        import json
        mock_resp = type("Resp", (), {
            "read": lambda self: json.dumps(mock_data).encode(),
            "__enter__": lambda self: self,
            "__exit__": lambda self, *a: None,
        })()
        mock_urlopen.return_value = mock_resp
        results = provider.search(_make_product(), 5)

    assert len(results) == 2
    assert results[0].platform == "shopee"
    assert results[0].source == "serpapi"
    assert results[1].platform == "momo"


# --- Brave provider ---

def test_brave_disabled_without_key() -> None:
    provider = BraveSearchProvider(api_key="", platforms=["shopee"])
    assert not provider.enabled
    assert provider.search(_make_product(), 5) == []


def test_brave_parses_results() -> None:
    mock_data = {
        "web": {
            "results": [
                {"title": "GENKI - Ruten", "url": "https://www.ruten.com.tw/item/1", "description": "便宜賣"},
                {"title": "GENKI Blog", "url": "https://blog.example.com/g", "description": "分享"},
            ]
        }
    }

    provider = BraveSearchProvider(api_key="test", platforms=["ruten"])

    with patch("src.search.brave_search.urllib.request.urlopen") as mock_urlopen:
        import json
        mock_resp = type("Resp", (), {
            "read": lambda self: json.dumps(mock_data).encode(),
            "__enter__": lambda self: self,
            "__exit__": lambda self, *a: None,
        })()
        mock_urlopen.return_value = mock_resp
        results = provider.search(_make_product(), 5)

    assert len(results) == 1
    assert results[0].platform == "ruten"
    assert results[0].source == "brave"


# --- Cache ---

def test_cache_miss_on_empty(tmp_path: Path) -> None:
    cache = SearchCache(tmp_path / "cache.json", ttl_hours=24)
    assert cache.get("test_product") is None


def test_cache_hit_after_put(tmp_path: Path) -> None:
    cache = SearchCache(tmp_path / "cache.json", ttl_hours=24)
    results = [SearchResult(
        product_name="GENKI", url="https://shopee.tw/1",
        platform="shopee", source="serpapi", searched_at="2026-01-01T00:00:00+00:00",
    )]
    cache.put("GENKI", results)
    cached = cache.get("GENKI")
    assert cached is not None
    assert len(cached) == 1
    assert cached[0].cached is True
    assert cached[0].url == "https://shopee.tw/1"


def test_cache_expired(tmp_path: Path) -> None:
    cache = SearchCache(tmp_path / "cache.json", ttl_hours=24)
    results = [SearchResult(
        product_name="GENKI", url="https://shopee.tw/1",
        platform="shopee", source="serpapi",
    )]
    cache.put("GENKI", results)
    # Manually expire the entry
    import json
    data = json.loads(cache.cache_path.read_text(encoding="utf-8"))
    for key in data:
        old_time = datetime.now(timezone.utc) - timedelta(hours=25)
        data[key]["stored_at"] = old_time.isoformat(timespec="seconds")
    cache.cache_path.write_text(json.dumps(data), encoding="utf-8")
    cache2 = SearchCache(tmp_path / "cache.json", ttl_hours=24)
    assert cache2.get("GENKI") is None


def test_cache_persists_to_disk(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache = SearchCache(cache_path, ttl_hours=24)
    cache.put("test", [SearchResult(
        product_name="t", url="https://shopee.tw/1",
        platform="shopee", source="serpapi",
    )])
    # Reload from disk
    cache2 = SearchCache(cache_path, ttl_hours=24)
    assert cache2.size == 1
    assert cache2.get("test") is not None


# --- Chain provider ---

def test_chain_merges_successful_providers(tmp_path: Path) -> None:
    product = _make_product()

    class FakeProvider(SerpAPIProvider):
        name = "fake_serpapi"
        enabled = True
        def search(self, product, max_results):
            return [SearchResult(
                product_name="GENKI", url="https://shopee.tw/1",
                platform="shopee", source="fake_serpapi",
            )]

    class FailProvider(BraveSearchProvider):
        name = "fake_brave"
        enabled = True
        def search(self, product, max_results):
            return [SearchResult(
                product_name="GENKI momo", url="https://momo.com.tw/1",
                platform="momo", source="fake_brave",
            )]

    chain = ChainSearchProvider(
        providers=[FakeProvider("k", []), FailProvider("k", [])],
        cache=SearchCache(tmp_path / "c.json"),
    )
    results = chain.search(product, 5)
    assert len(results) == 2
    assert results[0].source == "fake_serpapi"
    assert results[1].source == "fake_brave"
    assert chain.last_provider == "fake_serpapi+fake_brave"


def test_chain_does_not_stop_when_first_provider_hits_limit(tmp_path: Path) -> None:
    product = _make_product()

    class FullProvider(SerpAPIProvider):
        name = "full_serpapi"
        enabled = True

        def search(self, product, max_results):
            return [
                SearchResult(
                    product_name=f"GENKI {index}",
                    url=f"https://momo.com.tw/{index}",
                    platform="momo",
                    source="full_serpapi",
                )
                for index in range(max_results)
            ]

    class ShopeeProvider(BraveSearchProvider):
        name = "findprice"
        enabled = True

        def search(self, product, max_results):
            return [
                SearchResult(
                    product_name="GENKI Shopee",
                    url="https://www.findprice.com.tw/go/shopee",
                    platform="shopee",
                    source="findprice",
                )
            ]

    chain = ChainSearchProvider(
        providers=[FullProvider("k", []), ShopeeProvider("k", [])],
        cache=SearchCache(tmp_path / "c.json"),
    )
    results = chain.search(product, 2)

    assert len(results) == 3
    assert any(result.platform == "shopee" for result in results)
    assert chain.last_provider == "full_serpapi+findprice"


def test_chain_falls_back_on_failure(tmp_path: Path) -> None:
    product = _make_product()

    class FailProvider(SerpAPIProvider):
        name = "fail_serp"
        enabled = True
        def search(self, product, max_results):
            raise Exception("API down")

    class OKProvider(BraveSearchProvider):
        name = "ok_brave"
        enabled = True
        def search(self, product, max_results):
            return [SearchResult(
                product_name="GENKI", url="https://ruten.com.tw/1",
                platform="ruten", source="ok_brave",
            )]

    chain = ChainSearchProvider(
        providers=[FailProvider("k", []), OKProvider("k", [])],
        cache=SearchCache(tmp_path / "c.json"),
    )
    results = chain.search(product, 5)
    assert len(results) == 1
    assert results[0].source == "ok_brave"


def test_chain_returns_cached_results(tmp_path: Path) -> None:
    product = _make_product()
    cache = SearchCache(tmp_path / "c.json")
    cache.put("GENKI元氣習慣", [SearchResult(
        product_name="cached", url="https://shopee.tw/cached",
        platform="shopee", source="serpapi", searched_at="2026-01-01",
    )])

    chain = ChainSearchProvider(providers=[], cache=cache)
    results = chain.search(product, 5)
    assert len(results) == 1
    assert results[0].cached is True


def test_build_chain_provider_no_keys(tmp_path: Path) -> None:
    chain = build_chain_provider("", "", ["shopee"], tmp_path / "c.json")
    assert chain.enabled
