import logging
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

# Add src to path
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from src.database import Database
from src.image_matcher import average_hash_bytes, stable_image_filename

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOGGER = logging.getLogger(__name__)

def clean_search_term(name: str) -> str:
    """Clean product name for better search results."""
    # Remove sizes like 60粒/瓶, 370ml, etc.
    term = re.sub(r"\d+\s*(粒|錠|顆|包|盒|瓶|ml|g|mg|日份|個月份).*", "", name, flags=re.IGNORECASE)
    term = term.replace("【AFC】", "").replace("AFC ", "").replace("AFC_", "")
    term = re.sub(r"[【】〖〗\[\]（）()]", " ", term)
    term = term.strip()
    return term

import requests

def fetch_html(url: str) -> str:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    resp = requests.get(url, headers=headers, timeout=15)
    return resp.text

def download_image(url: str, save_path: Path) -> bytes:
    headers = {'User-Agent': 'Mozilla/5.0'}
    resp = requests.get(url, headers=headers, timeout=15)
    data = resp.content
    save_path.write_bytes(data)
    return data

def scrape_images():
    db = Database(root / "data" / "price_monitor.db")
    products = db.list_products(active_only=True)
    
    img_dir = root / "data" / "official_images"
    img_dir.mkdir(exist_ok=True, parents=True)
    
    missing = [p for p in products if not p.official_image_url]
    LOGGER.info("Total products: %d, Missing images: %d", len(products), len(missing))
    
    success = 0
    for i, p in enumerate(missing, 1):
        keyword = clean_search_term(p.product_name)
        LOGGER.info("[%d/%d] Searching for: %s (query: %s)", i, len(missing), p.product_name, keyword)
        
        try:
            # Search
            search_url = f"https://www.afc-life.com/products?query={urllib.parse.quote(keyword)}"
            html = fetch_html(search_url)
            soup = BeautifulSoup(html, 'html.parser')
            
            # Find exact match or first product link
            found_href = None
            for a in soup.find_all('a', href=True):
                href = a['href']
                text = a.get_text(strip=True)
                if '/products/' in href and text and keyword.lower() in text.lower():
                    found_href = href
                    break
            
            if not found_href:
                grid_links = soup.select('.product-item a, .ProductItem a')
                for a in grid_links:
                    href = a['href']
                    if '/products/' in href:
                        found_href = href
                        break
            
            if not found_href:
                LOGGER.warning("  -> No product found on search page.")
                time.sleep(1)
                continue
                
            if not found_href.startswith('http'):
                found_href = 'https://www.afc-life.com' + found_href
                
            # Fetch product page
            p_html = fetch_html(found_href)
            p_soup = BeautifulSoup(p_html, 'html.parser')
            
            img_url = None
            og_img = p_soup.find('meta', property='og:image')
            if og_img:
                img_url = og_img.get('content')
            
            if not img_url:
                # Try finding product image
                img_tag = p_soup.select_one('.product-image img, .ProductImage img')
                if img_tag:
                    img_url = img_tag.get('src')
            
            if img_url:
                if img_url.startswith('//'):
                    img_url = 'https:' + img_url
                    
                # Download and hash
                filename = stable_image_filename(p.product_name, img_url)
                save_path = img_dir / filename
                
                img_bytes = download_image(img_url, save_path)
                img_hash = average_hash_bytes(img_bytes)
                
                rel_path = f"data\\official_images\\{filename}"
                
                db.upsert_product(
                    product_name=p.product_name,
                    suggested_price=p.suggested_price,
                    brand=p.brand,
                    keywords=p.keywords,
                    exclude_keywords=p.exclude_keywords,
                    priority=p.priority,
                    is_active=p.is_active,
                    official_image_url=img_url,
                    official_image_path=rel_path,
                    official_image_hash=img_hash,
                )
                success += 1
                LOGGER.info("  -> Saved image: %s", img_url)
            else:
                LOGGER.warning("  -> No image found on product page.")
                
        except Exception as e:
            LOGGER.error("  -> Error: %s", e)
            
        time.sleep(1.5)  # Be nice to the server
        
    LOGGER.info("Done. Downloaded %d new images.", success)

if __name__ == "__main__":
    scrape_images()
