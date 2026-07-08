from pathlib import Path

from src.loader import parse_price_value, read_products


def test_read_products_without_header(tmp_path: Path) -> None:
    csv_path = tmp_path / "products.csv"
    csv_path.write_text("350.000000,AFC胺基酸\n\"1,300\",AFC綠藻\n", encoding="utf-8")

    products = read_products(csv_path)

    assert products[0].suggested_price == 350
    assert products[0].product_name == "AFC胺基酸"
    assert products[1].suggested_price == 1300


def test_read_products_with_header(tmp_path: Path) -> None:
    csv_path = tmp_path / "products.csv"
    csv_path.write_text(
        "suggested_price,product_name,official_image_url,official_image_hash\n"
        "880,光采紅顏鐵+C錠,https://example.com/a.jpg,abc123\n",
        encoding="utf-8",
    )

    products = read_products(csv_path)

    assert len(products) == 1
    assert products[0].product_name == "光采紅顏鐵+C錠"
    assert products[0].official_image_url == "https://example.com/a.jpg"
    assert products[0].official_image_hash == "abc123"


def test_parse_price_value() -> None:
    assert parse_price_value("NT$1,300") == 1300
    assert parse_price_value("350.000000") == 350
