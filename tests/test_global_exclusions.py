from pathlib import Path

from src.config import AppConfig
from src.database import Database
from src.extractors import ExtractionResult
from src.services.daily_monitor import DailyMonitorService


def test_global_exclusion_retroactively_matches_seller_and_product(tmp_path: Path) -> None:
    db = Database(tmp_path / "price_monitor.db")
    product_id = db.upsert_product("AFC 測試商品", suggested_price=1000)
    seller_match_id = db.upsert_candidate(
        product_id=product_id,
        url="https://example.com/seller",
        platform="other",
        title="AFC 測試商品 正規盒裝",
        seller="分裝包賣場",
    )
    product_match_id = db.upsert_candidate(
        product_id=product_id,
        url="https://example.com/product",
        platform="other",
        title="一般商品頁",
    )

    assert db.retroactively_exclude_candidates("分裝包") == 1
    assert db.retroactively_exclude_candidates("測試商品") == 1

    candidates = {candidate.id: candidate for candidate in db.list_candidates()}
    assert candidates[seller_match_id].status == "excluded"
    assert candidates[product_match_id].status == "excluded"
    assert db.list_candidates(include_excluded=False) == []
    assert len(db.list_candidates(status="excluded")) == 2


def test_monitor_excludes_when_extracted_title_matches_keyword(tmp_path: Path) -> None:
    db = Database(tmp_path / "price_monitor.db")
    product_id = db.upsert_product("AFC 測試商品", suggested_price=1000)
    candidate_id = db.upsert_candidate(
        product_id=product_id,
        url="https://example.com/item",
        platform="other",
        title="AFC 測試商品 正規盒裝",
    )
    db.add_global_exclusion("分裝包")

    service = DailyMonitorService(
        db,
        AppConfig(request_delay_seconds=0, enable_image_match=False),
        tmp_path,
    )
    service.extractor.extract = lambda **kwargs: ExtractionResult(
        title="AFC 測試商品 分裝包 10粒",
        price=99,
        platform="other",
        parse_status="ok",
        raw_data={"evidence_text": "title contains excluded package"},
    )

    extraction = service.check_single_candidate(candidate_id)

    assert extraction.parse_status == "excluded"
    candidate = db.list_candidates(status="excluded")[0]
    assert candidate.id == candidate_id
    assert candidate.status == "excluded"
    assert candidate.last_price is None

