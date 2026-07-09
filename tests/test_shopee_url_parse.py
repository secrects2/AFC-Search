"""Tests for Shopee URL parsing."""
import pytest

from src.parsers.shopee_provider import parse_shopee_url


class TestParseShopeeUrl:
    """Verify shop_id and item_id extraction from various Shopee URL formats."""

    def test_standard_url(self):
        url = "https://shopee.tw/AFC-GENKI+-每日快調-森永乳酸菌-(60包-盒)-i.27439060.24592685218"
        shop_id, item_id = parse_shopee_url(url)
        assert shop_id == "27439060"
        assert item_id == "24592685218"

    def test_encoded_url(self):
        url = (
            "https://shopee.tw/AFC-GENKI+-%E6%AF%8F%E6%97%A5%E5%BF%AB%E8%AA%BF-"
            "%E6%A3%AE%E6%B0%B8%E4%B9%B3%E9%85%B8%E8%8F%8C-(60%E5%8C%85-%E7%9B%92)"
            "-i.27439060.24592685218"
        )
        shop_id, item_id = parse_shopee_url(url)
        assert shop_id == "27439060"
        assert item_id == "24592685218"

    def test_short_ids(self):
        url = "https://shopee.tw/product-i.123.456"
        shop_id, item_id = parse_shopee_url(url)
        assert shop_id == "123"
        assert item_id == "456"

    def test_product_path_format(self):
        url = "https://shopee.tw/product/27439060/24592685218"
        shop_id, item_id = parse_shopee_url(url)
        assert shop_id == "27439060"
        assert item_id == "24592685218"

    def test_url_with_query_params(self):
        url = "https://shopee.tw/product-i.27439060.24592685218?sp_atk=abc123"
        shop_id, item_id = parse_shopee_url(url)
        assert shop_id == "27439060"
        assert item_id == "24592685218"

    def test_invalid_url_returns_empty(self):
        url = "https://shopee.tw/search?keyword=afc"
        shop_id, item_id = parse_shopee_url(url)
        assert shop_id == ""
        assert item_id == ""

    def test_non_shopee_url_returns_empty(self):
        url = "https://momo.com.tw/products/123456"
        shop_id, item_id = parse_shopee_url(url)
        assert shop_id == ""
        assert item_id == ""

    def test_empty_url(self):
        shop_id, item_id = parse_shopee_url("")
        assert shop_id == ""
        assert item_id == ""
