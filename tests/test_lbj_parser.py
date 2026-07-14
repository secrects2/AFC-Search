from src.search.lbj_api import parse_lbj_html, parse_lbj_query_price


def test_parse_lbj_query_price_prefers_aggregate_low_price():
    html = """
    <script type="application/ld+json">
      {"@type":"WebPage","offers":{"@type":"AggregateOffer","lowPrice":1380,"highPrice":1380}}
    </script>
    <div class="price-filter">1000~2999</div>
    """

    price, evidence = parse_lbj_query_price(
        html,
        "https://www.lbj.tw/BJ/Query.aspx?k=GENKI",
    )

    assert price == 1380
    assert evidence == "lbj JSON-LD AggregateOffer.lowPrice"


def test_parse_lbj_html_returns_each_product_once():
    html = """
    <div class="card">
      <button id="btnPriceHis1" data-pid="1" data-gn="AFC GENKI+伸長革命 60包/盒"
              data-url="/BJ/rd.ashx?ck=one" data-price="1,380" data-site="樂天市場 - 大向藥局"></button>
      <button id="btnEllip1" data-pid="1" data-gn="AFC GENKI+伸長革命 60包/盒"
              data-url="/BJ/rd.ashx?ck=one" data-price="1,380" data-site="樂天市場"></button>
    </div>
    <div class="card">
      <button id="btnPriceHis2" data-pid="2" data-gn="AFC GENKI+伸長革命 60包/盒"
              data-url="/BJ/rd.ashx?ck=two" data-price="1,485" data-site="Momo富邦購物"></button>
    </div>
    """

    listings = parse_lbj_html(html, "https://www.lbj.tw/BJ/Query.aspx?k=GENKI")

    assert len(listings) == 2
    assert listings[0].price == 1380
    assert listings[0].platform == "rakuten"
    assert listings[0].seller == "樂天市場 - 大向藥局"
    assert listings[0].url.startswith("https://www.lbj.tw/BJ/rd.ashx")
    assert listings[1].price == 1485
    assert listings[1].platform == "momo"


def test_legacy_lbj_query_candidate_uses_valid_skip_status(monkeypatch, tmp_path):
    from src.config import AppConfig
    from src.database import Database
    from src.services.daily_monitor import DailyMonitorService

    db = Database(tmp_path / "price_monitor.db")
    product_id = db.upsert_product("GENKI伸長革命", suggested_price=1485)
    candidate_id = db.upsert_candidate(
        product_id=product_id,
        url="https://www.lbj.tw/BJ/Query.aspx?k=GENKI",
        platform="lbj",
        title="LBJ Search: GENKI",
        source_found_by="lbj",
        last_price=1500,
        raw_data={"lbj_price": 1380},
    )

    service = DailyMonitorService(db, AppConfig(request_delay_seconds=0), tmp_path)
    monkeypatch.setattr(
        "src.services.daily_monitor.fetch_lbj_query_price",
        lambda *_args, **_kwargs: (1380.0, "lbj JSON-LD AggregateOffer.lowPrice"),
    )
    service.fallback_provider.observe = lambda *args, **kwargs: None

    extraction = service.check_single_candidate(candidate_id)

    assert extraction.parse_status == "price_not_found"
    observations = db.get_observations_for_decision(product_id)
    assert any(
        observation.candidate_id == candidate_id
        and observation.source == "lbj"
        and observation.price == 1380
        for observation in observations
    )
    assert any(
        observation.candidate_id == candidate_id
        and observation.status == "skipped_direct_crawl"
        for observation in observations
    )


def test_lbj_query_candidate_fetches_price_when_stored_price_is_missing(
    monkeypatch, tmp_path
):
    from src.config import AppConfig
    from src.database import Database
    from src.services.daily_monitor import DailyMonitorService

    db = Database(tmp_path / "price_monitor.db")
    product_id = db.upsert_product("GENKI 食育向上", suggested_price=1380)
    candidate_id = db.upsert_candidate(
        product_id=product_id,
        url="https://www.lbj.tw/BJ/Query.aspx?k=GENKI%20%E9%A3%9F%E8%82%B2",
        platform="lbj",
        title="LBJ Search: GENKI 食育",
        source_found_by="lbj",
        last_price=None,
        raw_data={},
    )

    monkeypatch.setattr(
        "src.services.daily_monitor.fetch_lbj_query_price",
        lambda *_args, **_kwargs: (1380.0, "lbj JSON-LD AggregateOffer.lowPrice"),
    )
    service = DailyMonitorService(db, AppConfig(request_delay_seconds=0), tmp_path)
    service.fallback_provider.observe = lambda *args, **kwargs: None

    extraction = service.check_single_candidate(candidate_id)

    assert extraction.price == 1380
    observations = db.get_observations_for_decision(product_id)
    assert any(
        observation.candidate_id == candidate_id
        and observation.source == "lbj"
        and observation.price == 1380
        for observation in observations
    )
