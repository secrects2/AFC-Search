"""Tests for blocked/error status handling in Shopee providers."""
from unittest.mock import patch, MagicMock
import pytest

from src.parsers.shopee_provider import ShopeePriceResult
from src.parsers.shopee_html_fallback import ShopeeHtmlFallbackProvider
from src.parsers.shopee_playwright_fallback import ShopeePlaywrightFallbackProvider
from src.parsers.shopee_third_party import ThirdPartyShopeeProvider


SAMPLE_URL = "https://shopee.tw/test-product-i.27439060.24592685218"


class TestHtmlFallbackBlocked:
    """Test ShopeeHtmlFallbackProvider handles blocking gracefully."""

    def test_http_403_returns_blocked(self):
        """HTTP 403 → status = blocked, no exception."""
        provider = ShopeeHtmlFallbackProvider(timeout=5)
        mock_resp = MagicMock()
        mock_resp.status_code = 403

        with patch("src.parsers.shopee_html_fallback.requests.get", return_value=mock_resp):
            result = provider.get_product_price(SAMPLE_URL)

        assert result.status == "blocked"
        assert "403" in result.error_message
        assert isinstance(result, ShopeePriceResult)

    def test_http_429_returns_blocked(self):
        """HTTP 429 (rate limit) → status = blocked."""
        provider = ShopeeHtmlFallbackProvider(timeout=5)
        mock_resp = MagicMock()
        mock_resp.status_code = 429

        with patch("src.parsers.shopee_html_fallback.requests.get", return_value=mock_resp):
            result = provider.get_product_price(SAMPLE_URL)

        assert result.status == "blocked"

    def test_timeout_returns_error(self):
        """Request timeout → status = error, no exception."""
        import requests as req_module
        provider = ShopeeHtmlFallbackProvider(timeout=1)

        with patch(
            "src.parsers.shopee_html_fallback.requests.get",
            side_effect=req_module.Timeout("timed out"),
        ):
            result = provider.get_product_price(SAMPLE_URL)

        assert result.status == "error"
        assert "timeout" in result.error_message.lower() or "Timeout" in result.error_message

    def test_no_price_in_html_returns_unknown(self):
        """HTML with no price data → status = price_unknown."""
        provider = ShopeeHtmlFallbackProvider(timeout=5)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><head><title>蝦皮購物</title></head><body>No prices here</body></html>"

        with patch("src.parsers.shopee_html_fallback.requests.get", return_value=mock_resp):
            result = provider.get_product_price(SAMPLE_URL)

        assert result.status == "price_unknown"
        assert result.price is None

    def test_invalid_url_returns_error(self):
        """Non-Shopee product URL → status = error."""
        provider = ShopeeHtmlFallbackProvider()
        result = provider.get_product_price("https://shopee.tw/search?keyword=test")

        assert result.status == "error"
        assert "Cannot parse" in result.error_message


class TestPlaywrightFallbackBlocked:
    """Test ShopeePlaywrightFallbackProvider handles blocking gracefully."""

    def test_playwright_not_installed(self):
        """When playwright is not available → status = error."""
        provider = ShopeePlaywrightFallbackProvider()
        provider._playwright_available = False

        result = provider.get_product_price(SAMPLE_URL)
        assert result.status == "error"
        assert "not installed" in result.error_message.lower()

    def test_blocked_page_detection(self):
        """Verify _is_blocked detects Shopee blocking patterns."""
        assert ShopeePlaywrightFallbackProvider._is_blocked("蝦皮購物", "選擇語言 繁體中文")
        assert ShopeePlaywrightFallbackProvider._is_blocked("蝦皮購物", "頁面無法顯示 發生錯誤")
        assert ShopeePlaywrightFallbackProvider._is_blocked("蝦皮", "請登入並再試一次")
        assert ShopeePlaywrightFallbackProvider._is_blocked("Verify", "captcha please verify")

    def test_normal_page_not_blocked(self):
        """Normal product page title should not be detected as blocked."""
        assert not ShopeePlaywrightFallbackProvider._is_blocked(
            "AFC GENKI 乳酸菌 | 蝦皮購物", "正常商品描述"
        )

    def test_invalid_url_returns_error(self):
        """Non-Shopee URL → status = error."""
        provider = ShopeePlaywrightFallbackProvider()
        provider._playwright_available = True  # pretend it's installed
        result = provider.get_product_price("https://shopee.tw/search?keyword=test")
        assert result.status == "error"


class TestThirdPartyBlocked:
    """Test ThirdPartyShopeeProvider handles failures gracefully."""

    def test_not_configured_returns_error(self):
        """No API URL → status = error."""
        provider = ThirdPartyShopeeProvider(api_url="", api_key="")
        result = provider.get_product_price(SAMPLE_URL)
        assert result.status == "error"
        assert "not configured" in result.error_message.lower()

    def test_api_returns_500(self):
        """API returns 500 → status = error, no exception."""
        provider = ThirdPartyShopeeProvider(
            api_url="http://test.local/api",
            api_key="test_key",
            max_retries=0,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("src.parsers.shopee_third_party.requests.post", return_value=mock_resp):
            result = provider.get_product_price(SAMPLE_URL)

        assert result.status == "error"
        assert "500" in result.error_message

    def test_api_timeout(self):
        """API timeout → status = error, no exception."""
        import requests as req_module
        provider = ThirdPartyShopeeProvider(
            api_url="http://test.local/api",
            api_key="test_key",
            timeout=1,
            max_retries=0,
        )

        with patch(
            "src.parsers.shopee_third_party.requests.post",
            side_effect=req_module.Timeout("timed out"),
        ):
            result = provider.get_product_price(SAMPLE_URL)

        assert result.status == "error"
        assert "timeout" in result.error_message.lower() or "Timeout" in result.error_message

    def test_api_network_error(self):
        """Network error → status = error, no exception."""
        provider = ThirdPartyShopeeProvider(
            api_url="http://test.local/api",
            api_key="test_key",
            max_retries=0,
        )

        with patch(
            "src.parsers.shopee_third_party.requests.post",
            side_effect=ConnectionError("Connection refused"),
        ):
            result = provider.get_product_price(SAMPLE_URL)

        assert result.status == "error"


class TestNoExceptionPropagation:
    """Verify that no provider ever raises — all errors are in the result."""

    def test_html_provider_never_raises(self):
        provider = ShopeeHtmlFallbackProvider()
        # Even with a completely bogus URL, it should not raise
        result = provider.get_product_price("not-a-url")
        assert isinstance(result, ShopeePriceResult)
        assert result.status in ("error", "blocked", "price_unknown")

    def test_third_party_never_raises(self):
        provider = ThirdPartyShopeeProvider(api_url="http://test.local/api")
        result = provider.get_product_price("not-a-url")
        assert isinstance(result, ShopeePriceResult)
        assert result.status in ("error", "blocked", "price_unknown")
