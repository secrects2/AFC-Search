from pathlib import Path

from src.config import AppConfig
from src.database import Database
from src.extractors import ExtractionResult
from src.search.base import SearchResult
from src.services.daily_monitor import DailyMonitorService


def test_shopee_blocked_uses_feebee_fallback(monkeypatch, tmp_path: Path) -> None:
    db = Database(tmp_path / "price_monitor.db")
    product_id = db.upsert_product("GENKI元氣習慣", suggested_price=1000)
    candidate_id = db.upsert_candidate(
        product_id=product_id,
        url="https://shopee.tw/product-i.1.2",
        platform="shopee",
        title="AFC GENKI+元氣習慣(60包/盒) 全球藥局",
        source_found_by="serpapi",
    )
    config = AppConfig(request_delay_seconds=0, enable_image_match=False)
    service = DailyMonitorService(db, config, tmp_path)
    service.extractor.extract = lambda **kwargs: ExtractionResult(
        title="蝦皮購物 | 花得更少買得更好",
        price=None,
        platform="shopee",
        parse_status="page_blocked",
        raw_data={"evidence_text": "provider=playwright | blocked"},
        error_message="blocked",
    )

    class FakeFallbackChain:
        def __init__(self, *args, **kwargs):
            pass

        def search(self, product, max_results):
            return [SearchResult(
                source="feebee",
                url="https://feebee.com.tw/s/test",
                product_name="AFC GENKI+元氣習慣(60包/盒) 全球藥局",
                found_price=1380,
                platform="shopee",
                seller="蝦皮商城 - 全球藥局",
            )]

    monkeypatch.setattr("src.services.fallback_price_provider.ChainSearchProvider", FakeFallbackChain)

    extraction = service.check_single_candidate(candidate_id)

    assert extraction.parse_status == "ok"
    assert extraction.price == 1380
    assert extraction.raw_data["price_source"] == "feebee"

    candidate = db.list_candidates()[0]
    assert candidate.status == "normal"
    assert candidate.last_price == 1380


def test_shopee_search_failed_uses_feebee_fallback(monkeypatch, tmp_path: Path) -> None:
    db = Database(tmp_path / "price_monitor.db")
    product_id = db.upsert_product("究極金盞花膠囊", suggested_price=3223)
    candidate_id = db.upsert_candidate(
        product_id=product_id,
        url="https://shopee.tw/product-i.1.3",
        platform="shopee",
        title="【AFC宇勝】究極金盞花膠囊(60顆) 葉黃素 日本原裝",
        source_found_by="findprice",
    )
    config = AppConfig(request_delay_seconds=0, enable_image_match=False)
    service = DailyMonitorService(db, config, tmp_path)
    service.extractor.extract = lambda **kwargs: ExtractionResult(
        title="",
        price=None,
        platform="shopee",
        parse_status="search_failed",
        raw_data={"evidence_text": "all Shopee providers failed"},
        error_message="all Shopee providers failed",
    )

    class FakeFallbackChain:
        def __init__(self, *args, **kwargs):
            pass

        def search(self, product, max_results):
            return [SearchResult(
                source="feebee",
                url="https://feebee.com.tw/s/test",
                product_name="【AFC宇勝】究極金盞花膠囊(60顆) 葉黃素 日本原裝 | 全球藥局",
                found_price=3200,
                platform="shopee",
                seller="蝦皮商城 - 全球藥局｜全球藥局e購網",
            )]

    monkeypatch.setattr("src.services.fallback_price_provider.ChainSearchProvider", FakeFallbackChain)

    extraction = service.check_single_candidate(candidate_id)

    assert extraction.parse_status == "ok"
    assert extraction.price == 3200
    assert extraction.raw_data["price_source"] == "feebee"
