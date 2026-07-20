from src.parsers.yahoo import YahooParser


def test_yahoo_parser_prefers_structured_auction_price_over_other_numbers() -> None:
    html = """
    <script id="isoredux-data" type="mime/invalid">
      {"item":{"listingId":"100326247777","price":3680,
      "title":"日本AFC 胎盤素膠囊 60粒/盒"},"sidebar":{"bonus":5}}
    </script>
    """

    price, evidence = YahooParser.extract_price(html, "紅利 5 點 其他數字 233")

    assert price == 3680.0
    assert evidence == "yahoo embedded item.price"
