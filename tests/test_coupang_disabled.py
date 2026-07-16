from __future__ import annotations

import json
from pathlib import Path

from src.config import AppConfig
from src.database import Database
from src.search.base import SearchResult
from src.services.daily_monitor import DailyMonitorService
from src.services.fallback_price_provider import FallbackPriceProvider
from src.services.discovery_search import DiscoverySearchService


def test_coupang_candidates_are_excluded_on_upsert(tmp_path: Path) -> None:
    db = Database(tmp_path / "coupang-upsert.db")
    product_id = db.upsert_product("AFC GENKI test", suggested_price=1000)

    candidate_id = db.upsert_candidate(
        product_id=product_id,
        url="https://www.tw.coupang.com/products/1",
        platform="other",
        title="AFC GENKI test",
        source_found_by="manual",
    )

    candidate = db.list_candidates()[0]
    evidence = json.loads(candidate.raw_data)
    assert candidate.id == candidate_id
    assert candidate.status == "excluded"
    assert evidence["exclusion_reason"] == "platform_coupang_disabled"
    assert evidence["disabled_platform"] == "coupang"


def test_existing_coupang_candidates_are_migrated_to_excluded(tmp_path: Path) -> None:
    db_path = tmp_path / "coupang-migration.db"
    db = Database(db_path)
    product_id = db.upsert_product("AFC GENKI migration", suggested_price=1000)
    candidate_id = db.upsert_candidate(
        product_id=product_id,
        url="https://example.com/item/1",
        platform="momo",
        source_found_by="manual",
    )
    with db._cursor() as (conn, cur):
        cur.execute(
            "UPDATE product_candidates SET platform='coupang', status='active' WHERE id=?",
            (candidate_id,),
        )

    migrated_db = Database(db_path)
    candidate = migrated_db.list_candidates()[0]
    evidence = json.loads(candidate.raw_data)
    assert candidate.status == "excluded"
    assert evidence["exclusion_reason"] == "platform_coupang_disabled"


def test_daily_monitor_does_not_crawl_or_fallback_coupang(tmp_path: Path) -> None:
    db = Database(tmp_path / "coupang-monitor.db")
    product_id = db.upsert_product("AFC GENKI monitor", suggested_price=1000)
    candidate_id = db.upsert_candidate(
        product_id=product_id,
        url="https://www.tw.coupang.com/products/2",
        platform="coupang",
        title="AFC GENKI monitor",
        source_found_by="manual",
        last_price=900,
    )
    service = DailyMonitorService(
        db,
        AppConfig(request_delay_seconds=0, enable_image_match=False),
        tmp_path,
    )
    service.extractor.extract = lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("Coupang must not be crawled")
    )
    service.fallback_provider.observe = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("Coupang must not enter fallback")
    )

    extraction = service.check_single_candidate(candidate_id)

    assert extraction.parse_status == "excluded"
    assert extraction.error_message == "Excluded by platform: coupang"
    assert db.list_candidates()[0].status == "excluded"
    with db._cursor() as (conn, cur):
        snapshot = cur.execute(
            "SELECT error_message FROM price_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert snapshot["error_message"] == "Excluded by platform: coupang"


def test_fallback_provider_rejects_coupang_without_search(tmp_path: Path) -> None:
    db = Database(tmp_path / "coupang-fallback.db")
    product_id = db.upsert_product("AFC GENKI fallback", suggested_price=1000)
    candidate_id = db.upsert_candidate(
        product_id=product_id,
        url="https://www.tw.coupang.com/products/3",
        platform="coupang",
        source_found_by="manual",
    )
    provider = FallbackPriceProvider({}, db)

    assert provider.observe(db.get_product(product_id), db.list_candidates()[0]) is None
    assert provider.last_audit["match_status"] == "platform_disabled"


def test_discovery_skips_coupang_results(monkeypatch, tmp_path: Path) -> None:
    db = Database(tmp_path / "coupang-discovery.db")
    product_id = db.upsert_product("AFC GENKI discovery", suggested_price=1000)

    class FakeProvider:
        enabled = True
        last_provider = "test"

        def search(self, product, max_results):
            return [
                SearchResult(
                    product_name="AFC GENKI discovery",
                    url="https://www.tw.coupang.com/products/4",
                    platform="coupang",
                    source="serpapi",
                    found_price=900,
                )
            ]

    monkeypatch.setattr(
        "src.services.discovery_search.build_chain_provider",
        lambda **kwargs: FakeProvider(),
    )
    service = DiscoverySearchService(
        db,
        AppConfig(enable_ocr=False, enable_image_match=False),
        tmp_path,
    )

    result = service.search_product(product_id)

    assert result["found"] == 1
    assert result["new"] == 0
    assert result["skipped"] == 1
    assert db.list_candidates() == []
