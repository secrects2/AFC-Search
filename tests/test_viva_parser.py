from src.config import AppConfig
from src.parsers import get_parser
from src.parsers.viva import VivaParser


def test_viva_parser_prefers_visible_product_price_over_small_page_numbers() -> None:
    html = """
    <div class="product-title">【AFC】新究極女調 60粒/瓶</div>
    <div class="p-price">2,380</div>
    <div>最高可獲得紅利點數 5點</div>
    <div class="o-price">3,280</div>
    """

    price, evidence = VivaParser.extract_price(html, "商品特色 紅利 5 點")

    assert price == 2380.0
    assert evidence == "viva visible .p-price"


def test_viva_parser_uses_embedded_sale_price_when_dom_is_missing() -> None:
    html = """
    <script>
      window.__NUXT__={item_name:'AFC 商品',market_price:3280,sale_price:2380,bonus:5};
    </script>
    """

    price, evidence = VivaParser.extract_price(html, "紅利 5 點")

    assert price == 2380.0
    assert evidence == "viva embedded sale_price"


def test_viva_host_routes_to_viva_parser_even_when_candidate_label_is_lbj() -> None:
    parser = get_parser(
        "lbj",
        "https://www.vivatv.com.tw/goods/3288022025",
        AppConfig(enable_screenshot=False, enable_ocr=False),
    )

    assert isinstance(parser, VivaParser)
