import urllib.request
import urllib.parse
from bs4 import BeautifulSoup

def search_afc(keyword):
    url = f"https://www.afc-life.com/products?query={urllib.parse.quote(keyword)}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        resp = urllib.request.urlopen(req)
        html = resp.read().decode('utf-8')
        soup = BeautifulSoup(html, 'html.parser')
        
        # In Shopline, products usually have a link with class or in a product item box
        items = soup.select('a.product-item-link, a.box-inner')
        if not items:
            # try finding any product link
            items = soup.select('a[href*="/products/"]')
            
        if items:
            href = items[0].get('href')
            if not href.startswith('http'):
                href = 'https://www.afc-life.com' + href
            
            # Now fetch the product page to get the high res image
            req2 = urllib.request.Request(href, headers={'User-Agent': 'Mozilla/5.0'})
            resp2 = urllib.request.urlopen(req2)
            html2 = resp2.read().decode('utf-8')
            soup2 = BeautifulSoup(html2, 'html.parser')
            
            # Find main product image
            # Shopline usually uses meta property="og:image"
            og_img = soup2.find('meta', property='og:image')
            if og_img:
                img_url = og_img.get('content')
                print(f"[{keyword}] Found: {href}")
                print(f"[{keyword}] Image: {img_url}")
                return img_url
        print(f"[{keyword}] No results found.")
    except Exception as e:
        print(f"[{keyword}] Error: {e}")

search_afc("DHA")
search_afc("綠藻")
