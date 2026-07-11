"""Tests for Shopee language page detection and blocked page detection."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.parsers.shopee_playwright_fallback import (
    ShopeePlaywrightFallbackProvider,
    is_shopee_language_page,
)


class _FakePage:
    """Minimal mock of a Playwright page with title() and inner_text()."""

    def __init__(self, title: str = "", body: str = "") -> None:
        self._title = title
        self._body = body

    def title(self) -> str:
        return self._title

    def inner_text(self, selector: str) -> str:
        if selector == "body":
            return self._body
        return ""


# -----------------------------------------------------------------------
# is_shopee_language_page
# -----------------------------------------------------------------------

class TestIsShopeeLanguagePage:
    """Verify is_shopee_language_page detects language selection pages."""

    def test_detects_chinese_keyword(self):
        page = _FakePage(title="蝦皮購物", body="選擇語言 繁體中文 English")
        assert is_shopee_language_page(page) is True

    def test_detects_choose_language(self):
        page = _FakePage(title="Shopee", body="Choose Language and Region")
        assert is_shopee_language_page(page) is True

    def test_detects_select_language(self):
        page = _FakePage(title="", body="Please Select Language")
        assert is_shopee_language_page(page) is True

    def test_detects_thai_indicator(self):
        page = _FakePage(title="", body="ภาษาไทย Bahasa Indonesia")
        assert is_shopee_language_page(page) is True

    def test_detects_vietnamese_indicator(self):
        page = _FakePage(title="", body="Tiếng Việt English")
        assert is_shopee_language_page(page) is True

    def test_detects_language_list_heuristic(self):
        """Page listing multiple languages (繁體中文 + English + Bahasa)."""
        page = _FakePage(
            title="蝦皮購物",
            body="繁體中文\nEnglish\nBahasa Indonesia\nTiếng Việt",
        )
        assert is_shopee_language_page(page) is True

    def test_detects_two_language_tokens(self):
        """Heuristic: 2+ language list tokens → language page."""
        page = _FakePage(title="", body="繁體中文 English")
        assert is_shopee_language_page(page) is True

    def test_normal_product_page_not_detected(self):
        """Normal product page should NOT be detected as language page."""
        page = _FakePage(
            title="AFC GENKI 乳酸菌 | 蝦皮購物",
            body="AFC GENKI+元氣習慣 60包 NT$ 1,380 加入購物車 賣場 評價 4.9",
        )
        assert is_shopee_language_page(page) is False

    def test_empty_page_not_detected(self):
        page = _FakePage(title="", body="")
        assert is_shopee_language_page(page) is False

    def test_page_with_single_language_token_not_detected(self):
        """A single language token (e.g. 繁體中文 in footer) is not enough."""
        page = _FakePage(
            title="AFC 胎盤素 | 蝦皮購物",
            body="繁體中文 版權所有 蝦皮購物",
        )
        # Only 1 token (繁體中文) → should NOT trigger
        assert is_shopee_language_page(page) is False


# -----------------------------------------------------------------------
# _is_blocked
# -----------------------------------------------------------------------

class TestIsBlocked:
    """Verify _is_blocked detects Shopee blocking patterns."""

    def test_detects_page_cannot_display(self):
        assert ShopeePlaywrightFallbackProvider._is_blocked("蝦皮購物", "頁面無法顯示 發生錯誤") is True

    def test_detects_login_required(self):
        assert ShopeePlaywrightFallbackProvider._is_blocked("蝦皮購物", "請登入並再試一次") is True

    def test_detects_captcha(self):
        assert ShopeePlaywrightFallbackProvider._is_blocked("Verify", "captcha please verify") is True

    def test_detects_access_denied(self):
        assert ShopeePlaywrightFallbackProvider._is_blocked("Error", "Access Denied") is True

    def test_detects_403(self):
        assert ShopeePlaywrightFallbackProvider._is_blocked("Error", "HTTP 403 Forbidden") is True

    def test_detects_verification(self):
        assert ShopeePlaywrightFallbackProvider._is_blocked("蝦皮", "需要驗證") is True

    def test_normal_product_not_blocked(self):
        assert ShopeePlaywrightFallbackProvider._is_blocked(
            "AFC GENKI 乳酸菌 | 蝦皮購物", "正常商品描述 NT$ 1,380"
        ) is False

    def test_empty_not_blocked(self):
        assert ShopeePlaywrightFallbackProvider._is_blocked("", "") is False
