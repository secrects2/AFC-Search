import urllib.request
import urllib.parse
from bs4 import BeautifulSoup

def test_findprice(keyword):
    # FindPrice URL format: https://www.findprice.com.tw/g/AFC+DHA
    url = f"https://www.findprice.com.tw/g/{urllib.parse.quote(keyword)}"
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    })
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        html = resp.read().decode('utf-8')
        with open('findprice.html', 'w', encoding='utf-8') as f:
            f.write(html)
        soup = BeautifulSoup(html, 'html.parser')
        print(f"HTML Preview: {html[:500]}...")
        
        # Look for product items
        items = soup.select('.divGoodsName a, .gs-title a, .m-item a, .goods-name a')
        print(f"[{keyword}] Found {len(items)} items on FindPrice.")
        
        links = []
        for a in items[:5]:
            title = a.get_text(strip=True)
            href = a.get('href', '')
            if href.startswith('/'):
                href = 'https://www.findprice.com.tw' + href
            links.append((title, href))
            
        for t, h in links:
            print(f"  - {t}")
            print(f"    {h}")
            
    except Exception as e:
        print(f"Error fetching FindPrice for '{keyword}': {e}")

if __name__ == "__main__":
    test_findprice("AFC DHA")
