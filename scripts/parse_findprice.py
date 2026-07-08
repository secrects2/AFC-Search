import re
html = open('findprice.html', 'r', encoding='utf-8').read()
from bs4 import BeautifulSoup
soup = BeautifulSoup(html, 'html.parser')

items = soup.find_all('div', class_=re.compile('goodsItem'))
if not items:
    items = soup.find_all('li', class_=re.compile('goodsItem'))
if not items:
    items = soup.select('.divGoodsName, .goods-name, .m-item')

print(f"Found {len(items)} items using generic classes.")

for item in items[:5]:
    a = item.find('a')
    if a:
        print(a.get_text(strip=True), "->", a.get('href'))
    else:
        print("No link in item:", item.get_text(strip=True)[:50])

print("\nAll links containing AFC or DHA:")
for a in soup.find_all('a', href=True):
    text = a.get_text(strip=True)
    href = a['href']
    if 'afc' in text.lower() or 'dha' in text.lower() or 'afc' in href.lower() or 'dha' in href.lower():
        print(text[:50], "->", href)
