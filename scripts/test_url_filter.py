import sys; sys.path.insert(0, ".")
from src.search.serp_api import is_product_page

urls = [
    "https://tw.buy.yahoo.com/amp/search/product?p=dha",
    "https://tw.buy.yahoo.com/category/4436601",
    "https://m.tw.buy.yahoo.com/smartcollection?tags=x",
    "https://m.tw.buy.yahoo.com/rushbuy?id=123",
    "https://shopee.tw/search?keyword=abc",
    "https://24h.pchome.com.tw/search/?q=test",
    "https://24h.pchome.com.tw/prod/DBADRG-A900GIHNQ",
    "https://shopee.tw/AFC-DHA-i.123.456",
    "https://tw.buy.yahoo.com/gdsale/ABC-123",
    "https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code=123",
    "https://tw.buy.yahoo.com/activity/activity950?p=act2",
    "https://ruten.com.tw/find/?q=AFC",
    "https://ruten.com.tw/item/show?123456",
]

for u in urls:
    tag = "PASS" if is_product_page(u) else "SKIP"
    print(f"  {tag:4s}  {u}")
