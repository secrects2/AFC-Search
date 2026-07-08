from pathlib import Path

from src.monitor_catalog import (
    ACTIVE_STATUS,
    MISSING_PRICE_STATUS,
    PENDING_REVIEW_STATUS,
    load_monitor_products,
    read_official_products,
    read_review_decisions,
    upsert_review_decision,
    write_official_products,
)


def write_product_csv(project_root: Path) -> Path:
    data_dir = project_root / "data"
    data_dir.mkdir()
    products_path = data_dir / "AFC商品.csv"
    products_path.write_text(
        "suggested_price,product_name\n1485,GENKI+ 兒童營養顆粒食品\n3223,究極金盞花膠囊\n",
        encoding="utf-8",
    )
    return products_path


def test_review_decision_approves_official_product_for_monitoring(tmp_path: Path) -> None:
    products_path = write_product_csv(tmp_path)
    write_official_products(
        tmp_path,
        [
            {
                "official_product_name": "【AFC】GENKI+ 兒童營養顆粒食品",
                "official_product_url": "https://www.afc-life.com/zh-hant/products/genki",
                "matched_db_product_name": "GENKI+ 兒童營養顆粒食品",
                "match_score": "72",
                "monitor_status": PENDING_REVIEW_STATUS,
            }
        ],
    )

    upsert_review_decision(
        tmp_path,
        products_path,
        "https://www.afc-life.com/zh-hant/products/genki",
        "【AFC】GENKI+ 兒童營養顆粒食品",
        "approved",
        "GENKI+ 兒童營養顆粒食品",
        reviewer="qa",
        note="官網商品確認",
    )

    official_rows = read_official_products(tmp_path)
    assert official_rows[0]["monitor_status"] == ACTIVE_STATUS
    assert official_rows[0]["suggested_price"] == "1485"
    assert official_rows[0]["reviewer"] == "qa"

    decisions = read_review_decisions(tmp_path)
    assert decisions[0]["decision"] == "approved"

    # load_monitor_products always reads from AFC商品.csv
    catalog = load_monitor_products(tmp_path, products_path)
    assert catalog.source == "AFC商品.csv"
    assert catalog.total_count == 2
    assert catalog.active_count == 2


def test_missing_price_decision_stays_out_of_monitoring(tmp_path: Path) -> None:
    products_path = write_product_csv(tmp_path)
    write_official_products(
        tmp_path,
        [
            {
                "official_product_name": "官網限定新品",
                "official_product_url": "https://www.afc-life.com/zh-hant/products/new",
                "monitor_status": PENDING_REVIEW_STATUS,
            }
        ],
    )

    upsert_review_decision(
        tmp_path,
        products_path,
        "https://www.afc-life.com/zh-hant/products/new",
        "官網限定新品",
        MISSING_PRICE_STATUS,
        reviewer="qa",
        note="DB 尚無建議售價",
    )

    official_rows = read_official_products(tmp_path)
    assert official_rows[0]["monitor_status"] == MISSING_PRICE_STATUS

    # load_monitor_products reads AFC商品.csv regardless of official status
    catalog = load_monitor_products(tmp_path, products_path)
    assert catalog.source == "AFC商品.csv"
    assert len(catalog.products) == 2
