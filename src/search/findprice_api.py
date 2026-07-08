import logging
import re
import time
import urllib.parse
import urllib.request
from typing import List

from bs4 import BeautifulSoup

from src.loader import Product
from src.search.base import BaseSearchProvider, SearchResult

LOGGER = logging.getLogger(__name__)

class FindPriceProvider(BaseSearchProvider):
    """Search implementation using FindPrice website scraping."""

    name = "findprice"

    def search(self, product: Product, max_results: int) -> list[SearchResult]:
        keyword = product.product_name
        LOGGER.info("Executing FindPrice search for keyword: %s", keyword)
        results = []
        
        # Add a delay to avoid aggressive rate limiting
        time.sleep(2.0)
        
        url = f"https://www.findprice.com.tw/g/{urllib.parse.quote(keyword)}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.8,en-US;q=0.5,en;q=0.3',
        })
        
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode('utf-8')
                
            soup = BeautifulSoup(html, 'html.parser')
            
            # FindPrice puts product items in <a> tags, often under .goodsItem or similar structures.
            # We look for all <a> tags since class names might change.
            for a in soup.find_all('a', href=True):
                if len(results) >= max_results:
                    break
                    
                href = a['href']
                title = a.get_text(strip=True)
                
                # Check if it looks like a product link
                # We expect titles to contain something close to the keyword or brand
                if not title or len(title) < 5:
                    continue
                    
                # FindPrice uses internal redirect URLs like /url.aspx?m=...&u=REAL_URL
                if 'url.aspx' in href:
                    parsed = urllib.parse.urlparse(href)
                    qs = urllib.parse.parse_qs(parsed.query)
                    real_url = qs.get('u', [None])[0]
                    
                    if real_url:
                        # Ensure absolute URL
                        if real_url.startswith('//'):
                            real_url = 'https:' + real_url
                        
                        # Add to results if we successfully extracted a real URL
                        if real_url.startswith('http'):
                            results.append(
                                SearchResult(
                                    url=real_url,
                                    product_name=title,
                                    source="findprice",
                                )
                            )
                            
        except Exception as exc:
            LOGGER.error("FindPrice search failed for keyword '%s': %s", keyword, exc)
            
        LOGGER.info("FindPrice found %d candidate URLs for %s", len(results), keyword)
        return results
