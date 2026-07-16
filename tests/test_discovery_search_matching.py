from __future__ import annotations

from pathlib import Path

from src.config import AppConfig
from src.database import Database
from src.search.base import SearchResult
from src.services.discovery_search import (
    DiscoverySearchService,
    _best_discovery_match_score,
)


def test_discovery_alias_score_accepts_short_promotion_title() -> None:
    title = (
        "//\u77ed\u6548\u7279\u50f9//AFC \u5143\u6c23\u6bcf\u65e5\u5feb\u8abf"
        "\u4e73\u9178\u83cc\u9846\u7c92\u98df\u54c1 60\u5305"
    )

    assert _best_discovery_match_score(
        "GENKI\u6bcf\u65e5\u5feb\u8abf",
        "\u5feb\u8abf,\u6bcf\u65e5\u5feb\u8abf",
        title,
    ) >= 50


def test_discovery_uses_full_product_name_and_stores_shopee_result(
    monkeypatch, tmp_path: Path
) -> None:
    db = Database(tmp_path / "discovery.db")
    product_id = db.upsert_product(
        "GENKI\u6bcf\u65e5\u5feb\u8abf",
        suggested_price=1485,
        keywords="\u5feb\u8abf,\u6bcf\u65e5\u5feb\u8abf",
    )
    captured_queries: list[str] = []

    class FakeProvider:
        enabled = True
        last_provider = "feebee"
        last_attempts = []

        def search(self, product, max_results):
            captured_queries.append(product.product_name)
            return [
                SearchResult(
                    product_name=(
                        "//\u77ed\u6548\u7279\u50f9//AFC \u5143\u6c23\u6bcf\u65e5\u5feb\u8abf"
                        "\u4e73\u9178\u83cc\u9846\u7c92\u98df\u54c1 60\u5305"
                    ),
                    url=(
                        "https://shopee.tw/-AFC-\u5143\u6c23\u6bcf\u65e5\u5feb\u8abf"
                        "-i.1177025472.24948252880"
                    ),
                    platform="shopee",
                    source="feebee",
                    found_price=1380,
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

    assert captured_queries == ["AFC GENKI\u6bcf\u65e5\u5feb\u8abf"]
    assert result["new"] == 1
    candidate = db.list_candidates()[0]
    assert candidate.platform == "shopee"
    assert candidate.status == "active"
    assert '"discovery_match_score": 100' in candidate.raw_data
