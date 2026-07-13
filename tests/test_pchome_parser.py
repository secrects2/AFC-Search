from src.parsers.pchome import PChomeParser


def test_pchome_parser_prefers_visible_product_price_marker() -> None:
    html = """
    <div id="ProdBriefing">
      <div data-gtm-item_id="DBAV0M-A900I1LJT" data-gtm-price="2480">
        <span>$2,480</span><span>$2,880</span><span>會員回饋 2356</span>
      </div>
    </div>
    """

    price, evidence = PChomeParser.extract_price(html, "商品價格網路價 2480 元 建議售價 2880 元")

    assert price == 2480.0
    assert evidence == "pchome data-gtm-price"


def test_pchome_parser_falls_back_when_product_marker_is_missing() -> None:
    price, evidence = PChomeParser.extract_price(
        "<html><body>售價 $2,480 原價 $2,880</body></html>",
        "售價 $2,480 原價 $2,880",
    )

    assert price == 2480.0
    assert evidence


def test_pchome_parser_supports_sale_data_attributes() -> None:
    html = """
    <section id="ProdBriefing">
      <span data-original-price="4280">$4,280</span>
      <span data-price="3000">$3,000</span>
    </section>
    """

    price, evidence = PChomeParser.extract_price(html, "AFC 金盞花 $3,000")

    assert price == 3000.0
    assert evidence == "pchome data-price"


def test_pchome_parser_supports_embedded_sale_price() -> None:
    html = '<script>window.__PRODUCT__ = {"salePrice":"3000","originalPrice":"4280"}</script>'

    price, evidence = PChomeParser.extract_price(html, "AFC 金盞花")

    assert price == 3000.0
    assert evidence == "pchome embedded salePrice"
