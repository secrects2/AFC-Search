import pytest
from src.parsers.ruten import extract_ruten_price_from_text

def test_extract_ruten_price_from_text_simple():
    text = """
〖PChome 24h購物〗〖AFC〗新究極糖幸 10粒/盒(苦瓜) R99N
7天內出貨
549
尚未有評價 銷售 0
優惠活動
運費 NT$ 75
商品單價：
$Infinity - $-Infinity
庫存數量：1
"""
    price = extract_ruten_price_from_text(text)
    assert price == 549.0

def test_extract_ruten_price_from_text_recommendations():
    text = """
【日系好物】特價商品
1,290
賣家精選商品
商品A 1,990
商品B 399
商品C 799
"""
    price = extract_ruten_price_from_text(text)
    assert price == 1290.0

def test_extract_ruten_price_from_text_infinity():
    text = """
商品名稱
商品單價：
$Infinity - $-Infinity
庫存數量：1
"""
    price = extract_ruten_price_from_text(text)
    assert price is None

def test_extract_ruten_price_from_text_shipping():
    text = """
測試商品標題
999
運送 NT$ 75
滿490免運
庫存 100
"""
    price = extract_ruten_price_from_text(text)
    assert price == 999.0
