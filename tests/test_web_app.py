from pathlib import Path

from src.monitor_catalog import ACTIVE_STATUS, PENDING_REVIEW_STATUS, read_official_products, write_official_products
from src.web.app import create_app


def test_dashboard_and_download_routes(tmp_path: Path) -> None:
    latest = tmp_path / "output" / "latest"
    latest.mkdir(parents=True)
    (latest / "all_results.csv").write_text(
        "run_time,platform,product_name,suggested_price,found_title,found_price,price_gap,price_gap_percent,match_score,violation_status,seller,url,screenshot_path,parse_status,ocr_status,evidence_text\n",
        encoding="utf-8",
    )
    (latest / "violations.csv").write_text(
        "run_time,platform,product_name,suggested_price,found_title,found_price,price_gap,price_gap_percent,match_score,violation_status,seller,url,screenshot_path,parse_status,ocr_status,evidence_text\n",
        encoding="utf-8",
    )
    (latest / "price_monitor_report.xlsx").write_bytes(b"fake")

    app = create_app(tmp_path)
    client = app.test_client()

    assert client.get("/").status_code == 200
    assert client.get("/results").status_code == 200
    assert client.get("/downloads/price_monitor_report.xlsx").status_code == 200
    assert client.get("/downloads/secret.env").status_code == 404


def test_review_page_saves_decision(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "AFC商品.csv").write_text(
        "suggested_price,product_name\n1485,GENKI+ 兒童營養顆粒食品\n",
        encoding="utf-8",
    )
    write_official_products(
        tmp_path,
        [
            {
                "official_product_name": "【AFC】GENKI+ 兒童營養顆粒食品",
                "official_product_url": "https://www.afc-life.com/zh-hant/products/genki",
                "matched_db_product_name": "GENKI+ 兒童營養顆粒食品",
                "match_score": "70",
                "monitor_status": PENDING_REVIEW_STATUS,
            }
        ],
    )

    app = create_app(tmp_path)
    client = app.test_client()

    page = client.get("/review")
    assert page.status_code == 200
    assert "GENKI+".encode("utf-8") in page.data

    response = client.post(
        "/review/decision",
        data={
            "official_product_name": "【AFC】GENKI+ 兒童營養顆粒食品",
            "official_product_url": "https://www.afc-life.com/zh-hant/products/genki",
            "matched_db_product_name": "GENKI+ 兒童營養顆粒食品",
            "decision": "approved",
            "reviewer": "qa",
            "note": "確認",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    official_rows = read_official_products(tmp_path)
    assert official_rows[0]["monitor_status"] == ACTIVE_STATUS
    assert official_rows[0]["suggested_price"] == "1485"
