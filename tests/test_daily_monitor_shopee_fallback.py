import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config import AppConfig
from src.database import Database
from src.extractors import ExtractionResult
from src.search.base import SearchResult
from src.services.daily_monitor import DailyMonitorService
from src.services.daily_monitor import DailyMonitorResult


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

    assert extraction.parse_status == "price_not_found"
    assert extraction.price == 1380
    assert extraction.raw_data["price_source"] == "feebee"

    candidate = db.list_candidates()[0]
    assert candidate.status == "price_unknown"
    assert candidate.last_price == 1380
    snapshot = db.get_snapshots()[0]
    assert snapshot.final_status == "needs_review"


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

    assert extraction.parse_status == "price_not_found"
    assert extraction.price == 3200
    assert extraction.raw_data["price_source"] == "feebee"


@pytest.mark.parametrize("platform", ["momo", "yahoo", "ruten", "shopee", "pchome"])
def test_all_platforms_share_the_same_fallback_entrypoint(
    monkeypatch, tmp_path: Path, platform: str
) -> None:
    db = Database(tmp_path / f"{platform}.db")
    product_id = db.upsert_product(f"AFC {platform} 商品", suggested_price=1200)
    candidate_id = db.upsert_candidate(
        product_id=product_id,
        url=f"https://{platform}.example/item/1",
        platform=platform,
        title=f"AFC {platform} 商品",
        source_found_by="manual",
    )
    config = AppConfig(request_delay_seconds=0, enable_image_match=False)
    service = DailyMonitorService(db, config, tmp_path)
    service.extractor.extract = lambda **kwargs: ExtractionResult(
        title=f"AFC {platform} 商品",
        price=None,
        platform=platform,
        parse_status="page_blocked",
        error_message="blocked",
    )
    fallback = MagicMock(return_value={
        "source": "feebee",
        "platform": platform,
        "url": "https://feebee.com.tw/s/test",
        "title": f"AFC {platform} 商品",
        "seller": "",
        "price": 1100,
        "currency": "TWD",
        "match_score": 100,
        "confidence": 0.8,
        "status": "success",
        "error_message": "",
        "raw_data": {},
    })
    service.fallback_provider.observe = fallback

    candidate = db.list_candidates()[0]
    service._check_candidate(candidate, tmp_path / "screenshots", DailyMonitorResult())

    assert fallback.called
    assert db.list_candidates()[0].status == "price_unknown"
    assert db.get_snapshots()[0].final_status == "needs_review"


def test_direct_success_does_not_trigger_fallback(tmp_path: Path) -> None:
    db = Database(tmp_path / "direct.db")
    product_id = db.upsert_product("AFC 直連商品", suggested_price=1200)
    candidate_id = db.upsert_candidate(
        product_id=product_id,
        url="https://momo.com.tw/item/1",
        platform="momo",
        title="AFC 直連商品",
        source_found_by="manual",
    )
    config = AppConfig(request_delay_seconds=0, enable_image_match=False)
    service = DailyMonitorService(db, config, Path(tmp_path))
    service.extractor.extract = lambda **kwargs: ExtractionResult(
        title="AFC 直連商品",
        price=1200,
        platform="momo",
        parse_status="ok",
    )
    fallback = MagicMock()
    service.fallback_provider.observe = fallback

    service.check_single_candidate(candidate_id)

    fallback.assert_not_called()


def test_pchome_sold_out_is_excluded_without_fallback(tmp_path: Path) -> None:
    db = Database(tmp_path / "sold-out.db")
    product_id = db.upsert_product("AFC 新究極糖幸", suggested_price=2180)
    candidate_id = db.upsert_candidate(
        product_id=product_id,
        url="https://24h.pchome.com.tw/prod/DBADRG-A900BT98Q",
        platform="pchome",
        title="AFC 新究極糖幸 60粒/瓶",
        source_found_by="manual",
    )
    service = DailyMonitorService(
        db,
        AppConfig(request_delay_seconds=0, enable_image_match=False),
        tmp_path,
    )
    service.extractor.extract = lambda **kwargs: ExtractionResult(
        title="AFC 新究極糖幸 60粒/瓶",
        platform="pchome",
        parse_status="out_of_stock",
        raw_data={
            "special_status": "out_of_stock",
            "evidence_text": "PChome 商品頁顯示：熱銷一空",
        },
    )
    fallback = MagicMock()
    service.fallback_provider.observe = fallback

    extraction = service.check_single_candidate(candidate_id)

    assert extraction.parse_status == "excluded"
    assert extraction.error_message == "Excluded by PChome: 熱銷一空"
    assert db.list_candidates()[0].status == "excluded"
    with sqlite3.connect(tmp_path / "sold-out.db") as conn:
        snapshot = conn.execute(
            "SELECT price, error_message FROM price_snapshots "
            "WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
    assert snapshot == (None, "Excluded by PChome: 熱銷一空")
    fallback.assert_not_called()


def test_confirm_candidate_records_manual_price_and_resumes_monitoring(tmp_path: Path) -> None:
    db = Database(tmp_path / "confirm.db")
    product_id = db.upsert_product("AFC 究極女調", suggested_price=2380)
    candidate_id = db.upsert_candidate(
        product_id=product_id,
        url="https://24h.pchome.com.tw/prod/DBADRG-A900BT98Q",
        platform="pchome",
        title="AFC 究極女調 60顆",
        source_found_by="lbj",
    )
    snapshot_id = db.insert_snapshot(
        candidate_id=candidate_id,
        product_id=product_id,
        price=2280,
        suggested_price=2380,
        raw_data={"price_source": "lbj"},
    )
    db.update_snapshot_final_price(
        snapshot_id=snapshot_id,
        final_price=2280,
        final_price_source="lbj",
        final_confidence=0.8,
        final_status="needs_review",
        decision_reason="備援來源待確認",
    )

    service = DailyMonitorService(
        db,
        AppConfig(request_delay_seconds=0, enable_image_match=False),
        tmp_path,
    )
    service.confirm_candidate_and_start_monitoring(candidate_id)

    candidate = db.list_candidates()[0]
    assert candidate.status == "suspected_violation"
    assert candidate.last_price == 2280

    manual = next(obs for obs in db.get_observations(product_id) if obs.source == "manual")
    manual_audit = json.loads(manual.raw_data)
    assert manual.price == 2280
    assert manual_audit["review_action"] == "confirm_and_start_monitoring"
    assert manual_audit["confirmed_from_source"] == "lbj"

    confirmed_snapshot = next(
        snapshot
        for snapshot in db.get_snapshots(limit=500, latest_only=False)
        if snapshot.final_price_source == "manual"
    )
    assert confirmed_snapshot.final_status == "suspected_violation"
    assert confirmed_snapshot.final_price == 2280
