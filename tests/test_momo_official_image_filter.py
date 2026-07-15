from pathlib import Path

from src.config import AppConfig
from src.database import Database, canonical_final_url
from src.extractors import ExtractionResult
from src.image_text import ImageTextScanResult, normalize_image_text, scan_image_urls_for_text
from src.search.base import SearchResult
from src.services.daily_monitor import DailyMonitorService
from src.services.discovery_search import DiscoverySearchService


def test_normalize_image_text_removes_spacing() -> None:
    assert normalize_image_text("商 品 圖\n官 方") == "商品圖官方"


def test_scan_image_urls_detects_marker(monkeypatch) -> None:
    class Response:
        content = b"fake-image"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("src.image_text.requests.get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(
        "src.image_text._ocr_image_bytes",
        lambda _image_bytes: "AFC 商品 官方",
    )

    result = scan_image_urls_for_text(["https://img.example.com/momo.jpg"])

    assert result.matched
    assert result.matched_url == "https://img.example.com/momo.jpg"
    assert result.as_raw_data()["image_text_marker"] == "官方"


def test_daily_monitor_excludes_momo_image_with_official_marker(
    monkeypatch, tmp_path: Path
) -> None:
    db = Database(tmp_path / "price_monitor.db")
    product_id = db.upsert_product("AFC GENKI 測試商品", suggested_price=2180)
    candidate_id = db.upsert_candidate(
        product_id=product_id,
        url="https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code=1",
        platform="momo",
        title="AFC GENKI 測試商品",
    )
    service = DailyMonitorService(
        db,
        AppConfig(request_delay_seconds=0, enable_image_match=False),
        tmp_path,
    )
    service.extractor.extract = lambda **kwargs: ExtractionResult(
        title="AFC GENKI 測試商品",
        price=2180,
        seller="MOMO",
        platform="momo",
        image_urls=["https://img.example.com/momo.jpg"],
        parse_status="ok",
    )
    monkeypatch.setattr(
        "src.services.daily_monitor.scan_image_urls_for_text",
        lambda *args, **kwargs: ImageTextScanResult(
            "matched",
            marker="官方",
            matched_url="https://img.example.com/momo.jpg",
            raw_text="官方",
            checked_urls=1,
        ),
    )

    extraction = service.check_single_candidate(candidate_id)

    assert extraction.parse_status == "excluded"
    assert db.list_candidates(status="excluded")[0].id == candidate_id
    observation = db.get_observations(product_id=product_id)[0]
    assert observation.status == "excluded"
    assert '"image_text_marker": "官方"' in observation.raw_data


def test_discovery_stores_momo_official_image_as_excluded(
    monkeypatch, tmp_path: Path
) -> None:
    db = Database(tmp_path / "price_monitor.db")
    product_id = db.upsert_product("AFC GENKI 測試商品", suggested_price=2180)
    service = DiscoverySearchService(
        db,
        AppConfig(request_delay_seconds=0),
        tmp_path,
    )

    class FakeProvider:
        enabled = True
        last_provider = "fake"

        def search(self, product, max_results):
            return [
                SearchResult(
                    product_name="AFC GENKI 測試商品",
                    url="https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code=2",
                    platform="momo",
                    source="feebee",
                    found_price=2180,
                )
            ]

    monkeypatch.setattr(
        "src.services.discovery_search.build_chain_provider",
        lambda **kwargs: FakeProvider(),
    )
    monkeypatch.setattr(
        service,
        "_scan_momo_official_image",
        lambda url: ImageTextScanResult(
            "matched",
            marker="官方",
            matched_url="https://img.example.com/momo.jpg",
            raw_text="官方",
            checked_urls=1,
        ),
    )

    result = service.search_product(product_id)

    assert result["new"] == 1
    candidate = db.list_candidates()[0]
    assert candidate.status == "excluded"
    assert '"exclusion_reason": "MOMO 圖片含官方字樣"' in candidate.raw_data


def test_coupon_source_is_excluded_on_upsert(tmp_path: Path) -> None:
    db = Database(tmp_path / "price_monitor.db")
    product_id = db.upsert_product("AFC product", suggested_price=1000)

    candidate_id = db.upsert_candidate(
        product_id=product_id,
        url="https://coupon.example.com/product/1",
        platform="momo",
        title="AFC product",
        source_found_by="coupon_search",
    )

    candidate = db.list_candidates(status="excluded")[0]
    assert candidate.id == candidate_id
    assert candidate.source_found_by == "coupon"
    assert '"exclusion_reason": "source_coupon"' in candidate.raw_data


def test_deduplicate_candidates_by_final_url_keeps_earliest(tmp_path: Path) -> None:
    db = Database(tmp_path / "price_monitor.db")
    product_id = db.upsert_product("AFC product", suggested_price=1000)
    first_id = db.upsert_candidate(
        product_id=product_id,
        url="https://feebee.example.com/redirect/1",
        platform="momo",
        title="AFC product",
        source_found_by="feebee",
    )
    second_id = db.upsert_candidate(
        product_id=product_id,
        url="https://biggo.example.com/redirect/2",
        platform="momo",
        title="AFC product",
        source_found_by="biggo",
    )
    db.merge_candidate_raw_data(
        first_id,
        {"final_url": "https://shop.example.com/product/1?utm_source=feebee"},
    )
    db.merge_candidate_raw_data(
        second_id,
        {"final_url": "https://shop.example.com/product/1?utm_source=biggo"},
    )

    assert canonical_final_url(
        "https://shop.example.com/product/1?utm_source=feebee"
    ) == "https://shop.example.com/product/1"
    assert db.deduplicate_candidates_by_final_url(product_id) == 1
    candidates = {candidate.id: candidate for candidate in db.list_candidates()}
    assert candidates[first_id].status == "active"
    assert candidates[second_id].status == "excluded"
    assert '"duplicate_of_candidate_id": %d' % first_id in candidates[second_id].raw_data
