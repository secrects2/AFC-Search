from types import SimpleNamespace

from src.parsers.coupang import CoupangParser


def test_coupang_parser_prefers_visible_sale_price():
    page = SimpleNamespace(
        locator=lambda selector: SimpleNamespace(
            all=lambda: [SimpleNamespace(inner_text=lambda: "$2,180")]
        )
    )

    assert CoupangParser._extract_visible_price(page) == 2180


def test_coupang_parser_price_text():
    assert CoupangParser._parse_price_text("$2,180") == 2180
    assert CoupangParser._parse_price_text("原價 $2,380") == 2380
