# -*- coding: utf-8 -*-
import pytest
from src.visual_price import parse_price_from_text

def test_parse_simple_price():
    assert parse_price_from_text("$2,480") == 2480.0
    assert parse_price_from_text("NT$2480") == 2480.0
    assert parse_price_from_text("2,480元") == 2480.0
    assert parse_price_from_text("2480") == 2480.0

def test_parse_with_original_price():
    assert parse_price_from_text("售價 $2,480 原價 $2,880") == 2480.0
    assert parse_price_from_text("原價 2,880，特價 2,480") == 2480.0

def test_parse_with_points_and_cashback():
    assert parse_price_from_text("註冊送 50P幣起 商品價格 $2,480") == 2480.0
    assert parse_price_from_text("最高回饋 6% $2,480") == 2480.0

def test_parse_with_coupon():
    assert parse_price_from_text("折價券 100 元，售價 2,480") == 2480.0

def test_parse_no_price_found():
    assert parse_price_from_text("這裡沒有價格") is None
    assert parse_price_from_text("只有 6% 和 50P幣") is None
