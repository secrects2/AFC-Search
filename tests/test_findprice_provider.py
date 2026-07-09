from src.search.findprice_api import (
    FindPriceListing,
    find_best_findprice_listing,
    parse_findprice_html,
)


FINDPRICE_HTML = """
<div class="divGoods">
  <div class="GoodsImg">
    <a href="https://www.findprice.com.tw/go/gaymea4icgk/?s=0&t=1">
      <img class="searchImg" src="https://cf.shopee.tw/file/example_tn">
    </a>
  </div>
  <div class="divGoodsContent">
    <div class="rec-price-20">$ 1,380</div>
    <div class="GoodsGname">
      <a class="ga" href="https://www.findprice.com.tw/go/gaymea4icgk/?s=0&t=1"
         title="AFC GENKI+元氣習慣(60包/盒) 全球藥局">
        AFC GENKI+元氣習慣(60包/盒) 全球藥局
      </a>
    </div>
    <div class="GoodsMname">
      <span class="mname">蝦皮商城 - 全球藥局｜全球藥局e購網</span>
    </div>
  </div>
</div>
<div class="divGoods">
  <div class="divGoodsContent">
    <div class="rec-price-20">$ 1,485</div>
    <div class="GoodsGname">
      <a class="ga" href="https://www.findprice.com.tw/url.aspx?u=https%3A%2F%2Fwww.momoshop.com.tw%2Fgoods%2F123"
         title="【AFC】GENKI+ 元氣習慣 60包/盒(日本原裝)">
        【AFC】GENKI+ 元氣習慣 60包/盒(日本原裝)
      </a>
    </div>
    <div class="GoodsMname"><span class="mname">momo購物網</span></div>
  </div>
</div>
"""


def test_parse_findprice_html_detects_shopee_listing() -> None:
    listings = parse_findprice_html(FINDPRICE_HTML)

    assert len(listings) == 2
    assert listings[0].platform == "shopee"
    assert listings[0].price == 1380
    assert "全球藥局" in listings[0].seller
    assert listings[0].url.startswith("https://www.findprice.com.tw/go/")
    assert listings[1].platform == "momo"
    assert listings[1].url == "https://www.momoshop.com.tw/goods/123"


def test_find_best_findprice_listing_prefers_requested_platform(monkeypatch) -> None:
    def fake_search(keyword: str, max_results: int = 20, timeout: int = 15, delay_seconds: float = 1.0):
        return [
            FindPriceListing(
                title="AFC GENKI+ 元氣習慣 60包/盒",
                url="https://www.findprice.com.tw/go/shopee",
                platform="shopee",
                seller="蝦皮商城 - 全球藥局",
                price=1380,
                price_text="$ 1,380",
            ),
            FindPriceListing(
                title="AFC GENKI+ 元氣習慣 60包/盒",
                url="https://www.momoshop.com.tw/goods/123",
                platform="momo",
                seller="momo購物網",
                price=1485,
                price_text="$ 1,485",
            ),
        ]

    monkeypatch.setattr("src.search.findprice_api.search_findprice_listings", fake_search)

    best = find_best_findprice_listing(
        "GENKI元氣習慣",
        expected_title="AFC GENKI+元氣習慣(60包/盒) 全球藥局",
        preferred_platform="shopee",
    )

    assert best is not None
    assert best.platform == "shopee"
    assert best.price == 1380


def test_find_best_findprice_listing_uses_query_variants(monkeypatch) -> None:
    calls: list[str] = []

    def fake_search(keyword: str, max_results: int = 20, timeout: int = 15, delay_seconds: float = 1.0):
        calls.append(keyword)
        if "新究極甘源膠囊食品" in keyword:
            return [
                FindPriceListing(
                    title="AFC 新究極 甘源膠囊食品 60粒/瓶",
                    url="https://www.findprice.com.tw/go/ganyuan",
                    platform="shopee",
                    seller="蝦皮購物精選 - 專品藥局",
                    price=2380,
                    price_text="$ 2,380",
                )
            ]
        return [
            FindPriceListing(
                title="AFC 記清PS膠囊 60顆 全球藥局",
                url="https://www.findprice.com.tw/go/wrong",
                platform="shopee",
                seller="蝦皮商城 - 全球藥局",
                price=1600,
                price_text="$ 1,600",
            )
        ]

    monkeypatch.setattr("src.search.findprice_api.search_findprice_listings", fake_search)

    best = find_best_findprice_listing(
        "AFC 新究極甘源",
        expected_title="AFC 新究極甘源膠囊食品60粒/瓶",
        preferred_platform="shopee",
    )

    assert best is not None
    assert best.price == 2380
    assert "新究極甘源" in best.title.replace(" ", "")
    assert len(calls) > 1


def test_find_best_findprice_listing_rejects_unrelated_preferred_platform(monkeypatch) -> None:
    def fake_search(keyword: str, max_results: int = 20, timeout: int = 15, delay_seconds: float = 1.0):
        return [
            FindPriceListing(
                title="AFC 記清PS膠囊 60顆 全球藥局",
                url="https://www.findprice.com.tw/go/wrong",
                platform="shopee",
                seller="蝦皮商城 - 全球藥局",
                price=1600,
                price_text="$ 1,600",
            )
        ]

    monkeypatch.setattr("src.search.findprice_api.search_findprice_listings", fake_search)

    best = find_best_findprice_listing(
        "AFC 新究極甘源",
        expected_title="AFC 新究極甘源膠囊食品60粒/瓶",
        preferred_platform="shopee",
    )

    assert best is None


def test_find_best_findprice_listing_requires_essential_product_terms(monkeypatch) -> None:
    def fake_search(keyword: str, max_results: int = 20, timeout: int = 15, delay_seconds: float = 1.0):
        return [
            FindPriceListing(
                title="【美麗人生藥局】AFC RICH葉黃素 30粒/瓶 葉黃素 金盞花",
                url="https://www.findprice.com.tw/go/rich-lutein",
                platform="shopee",
                seller="蝦皮購物精選 - 美麗人生藥局",
                price=1500,
                price_text="$ 1,500",
            ),
            FindPriceListing(
                title="【AFC宇勝】究極金盞花膠囊(60顆) 葉黃素 日本原裝 | 全球藥局",
                url="https://www.findprice.com.tw/go/marigold",
                platform="shopee",
                seller="蝦皮商城 - 全球藥局",
                price=3200,
                price_text="$ 3,200",
            ),
        ]

    monkeypatch.setattr("src.search.findprice_api.search_findprice_listings", fake_search)

    best = find_best_findprice_listing(
        "AFC 究極金盞花膠囊",
        expected_title="【AFC宇勝】究極金盞花膠囊(60顆) 葉黃素 日本原裝",
        preferred_platform="shopee",
    )

    assert best is not None
    assert best.price == 3200
    assert "究極金盞花" in best.title
