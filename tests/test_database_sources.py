from pathlib import Path

from src.database import Database


def test_candidate_sources_include_findprice_and_shopee(tmp_path: Path) -> None:
    db = Database(tmp_path / "price_monitor.db")
    product_id = db.upsert_product("GENKI元氣習慣", suggested_price=1485)

    findprice_id = db.upsert_candidate(
        product_id=product_id,
        url="https://www.findprice.com.tw/go/example",
        platform="shopee",
        title="AFC GENKI+元氣習慣",
        source_found_by="findprice",
    )
    shopee_id = db.upsert_candidate(
        product_id=product_id,
        url="https://shopee.tw/product-i.1.2",
        platform="shopee",
        title="AFC GENKI+元氣習慣",
        source_found_by="shopee",
    )

    candidates = {candidate.id: candidate for candidate in db.list_candidates()}
    assert candidates[findprice_id].source_found_by == "findprice"
    assert candidates[shopee_id].source_found_by == "shopee"
