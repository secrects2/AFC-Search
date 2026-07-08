import urllib.request
import urllib.parse
from bs4 import BeautifulSoup
import re

def search_afc(keyword):
    url = f"https://www.afc-life.com/products?query={urllib.parse.quote(keyword)}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        resp = urllib.request.urlopen(req)
        html = resp.read().decode('utf-8')
        soup = BeautifulSoup(html, 'html.parser')
        
        # In shopline, search results are often in a specific grid or list
        # Let's find product titles that match our keyword
        items = soup.select('.box-inner, .product-item, .sl-product-item, div.title, div.product-title')
        
        found_links = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True)
            if '/products/' in href and text and keyword.lower() in text.lower():
                found_links.append((text, href))
                
        print(f"[{keyword}] Exact text matches:")
        for t, h in set(found_links):
            print(f"  {t} -> {h}")
            
        if not found_links:
            # Let's try to just get the first actual product in the list that isn't a banner
            # Usually product links are within a grid, and have images
            grid_links = soup.select('.product-item a, .ProductItem a')
            for a in grid_links[:3]:
                print(f"  Fallback grid link: {a.get_text(strip=True)} -> {a['href']}")
                
    except Exception as e:
        print(f"[{keyword}] Error: {e}")

search_afc("DHA")
search_afc("綠藻")
