from src.parsers.generic import GenericParser


def test_extract_price_prefers_structured_price() -> None:
    html = '<meta property="product:price:amount" content="299">'
    price, evidence = GenericParser.extract_price(html, "評價 120 售價 NT$350")

    assert price == 299
    assert evidence == "meta price"


def test_extract_price_ignores_units_and_review_counts() -> None:
    price, evidence = GenericParser.extract_price("", "30粒 評價120 售價 NT$999")

    assert price == 999
    assert "999" in evidence


def test_extract_image_urls_from_meta() -> None:
    parser = GenericParser.__new__(GenericParser)
    html = '<meta property="og:image" content="/images/product.jpg"><img src="https://example.com/a.jpg">'

    urls = parser._extract_image_urls(html, "https://shop.example.com/product")

    assert urls[0] == "https://shop.example.com/images/product.jpg"
    assert "https://example.com/a.jpg" in urls
