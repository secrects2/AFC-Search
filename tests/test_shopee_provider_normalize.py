"""Tests for ThirdPartyShopeeProvider response normalization."""
import pytest

from src.parsers.shopee_provider import ShopeePriceResult
from src.parsers.shopee_third_party import ThirdPartyShopeeProvider


class TestNormalization:
    """Verify that various third-party API response formats are normalized correctly."""

    def _make_provider(self) -> ThirdPartyShopeeProvider:
        return ThirdPartyShopeeProvider(api_url="http://test.local/api")

    def test_normalize_flat_response(self):
        """Standard flat response with name, price."""
        provider = self._make_provider()
        data = {"name": "AFC 葉酸", "price": 850, "stock": 100}
        base = ShopeePriceResult(url="https://shopee.tw/test-i.1.2", shop_id="1", item_id="2")
        result = provider._normalize(data, base)
        assert result.title == "AFC 葉酸"
        assert result.price == 850
        assert result.stock == 100
        assert result.status == "ok"

    def test_normalize_nested_data_envelope(self):
        """Response wrapped in {"data": {...}}."""
        provider = self._make_provider()
        data = {"data": {"name": "AFC GENKI", "price": 1370, "currency": "TWD"}}
        base = ShopeePriceResult(url="https://shopee.tw/test-i.1.2", shop_id="1", item_id="2")
        result = provider._normalize(data, base)
        assert result.title == "AFC GENKI"
        assert result.price == 1370
        assert result.currency == "TWD"

    def test_normalize_nested_result_envelope(self):
        """Response wrapped in {"result": {...}}."""
        provider = self._make_provider()
        data = {"result": {"title": "葉酸 120粒", "current_price": 680}}
        base = ShopeePriceResult(url="https://shopee.tw/test-i.1.2", shop_id="1", item_id="2")
        result = provider._normalize(data, base)
        assert result.title == "葉酸 120粒"
        assert result.price == 680

    def test_normalize_shopee_microcent_price(self):
        """Shopee internal API returns prices in microcents (÷100000)."""
        provider = self._make_provider()
        data = {"name": "乳酸菌", "price": 137000000000}
        base = ShopeePriceResult(url="https://shopee.tw/test-i.1.2", shop_id="1", item_id="2")
        result = provider._normalize(data, base)
        assert result.price == 1370000.0  # 137000000000 / 100000

    def test_normalize_with_shop_info(self):
        """Response includes seller/shop info."""
        provider = self._make_provider()
        data = {
            "name": "AFC 鈣片",
            "price": 990,
            "shop": {"name": "AFC官方旗艦店"},
        }
        base = ShopeePriceResult(url="https://shopee.tw/test-i.1.2", shop_id="1", item_id="2")
        result = provider._normalize(data, base)
        assert result.seller == "AFC官方旗艦店"

    def test_normalize_with_shop_info_alt_format(self):
        """Response uses seller dict with shop_name field."""
        provider = self._make_provider()
        data = {
            "name": "AFC 鈣片",
            "price": 990,
            "seller": {"shop_name": "健康小舖"},
        }
        base = ShopeePriceResult(url="https://shopee.tw/test-i.1.2", shop_id="1", item_id="2")
        result = provider._normalize(data, base)
        assert result.seller == "健康小舖"

    def test_normalize_no_price_returns_unknown(self):
        """Missing price field → status = price_unknown."""
        provider = self._make_provider()
        data = {"name": "AFC 商品", "stock": 50}
        base = ShopeePriceResult(url="https://shopee.tw/test-i.1.2", shop_id="1", item_id="2")
        result = provider._normalize(data, base)
        assert result.price is None
        assert result.status == "price_unknown"

    def test_normalize_price_min_fallback(self):
        """If price is missing but price_min exists, use price_min."""
        provider = self._make_provider()
        data = {"name": "AFC 綠藻", "price_min": 450, "price_max": 680}
        base = ShopeePriceResult(url="https://shopee.tw/test-i.1.2", shop_id="1", item_id="2")
        result = provider._normalize(data, base)
        assert result.price == 450
        assert result.price_min == 450
        assert result.price_max == 680
        assert result.status == "ok"

    def test_normalize_zero_price_returns_unknown(self):
        """Price of 0 should be treated as no price."""
        provider = self._make_provider()
        data = {"name": "AFC", "price": 0}
        base = ShopeePriceResult(url="https://shopee.tw/test-i.1.2", shop_id="1", item_id="2")
        result = provider._normalize(data, base)
        assert result.price is None
        assert result.status == "price_unknown"

    def test_normalize_string_seller(self):
        """Seller as a plain string instead of dict."""
        provider = self._make_provider()
        data = {"name": "AFC", "price": 500, "seller": "直營店"}
        base = ShopeePriceResult(url="https://shopee.tw/test-i.1.2", shop_id="1", item_id="2")
        result = provider._normalize(data, base)
        assert result.seller == "直營店"
