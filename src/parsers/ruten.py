import re
import json
import logging
from typing import Optional, Tuple
from bs4 import BeautifulSoup

from src.parsers.generic import GenericParser

LOGGER = logging.getLogger(__name__)

def extract_ruten_price_from_text(text: str, title: str | None = None) -> Optional[float]:
    lines = text.split("\n")
    candidates = []
    
    exclude_keywords = [
        "Infinity", "-Infinity", "商品單價", "運費", "運送", "庫存", "銷售", "評價",
        "關注", "露幣", "回饋", "折價券", "推薦你參考", "賣家精選商品",
        "滿", "免運", "P幣", "付款", "出貨", "數量", "分期", "％", "%",
        "7天內出貨"
    ]
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
            
        if any(ex in line for ex in exclude_keywords):
            continue
            
        # Ignore numbers like "6.9萬", "97%", "100+"
        if re.search(r'\d+(?:\.\d+)?萬|\d+%|\d+\+', line):
            continue
            
        matches = re.finditer(r'(?:NT\$?|\$)?\s*([0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)(?:\.[0-9]+)?', line)
        for m in matches:
            # Check if this match is part of a word like 24h or R99N
            end_idx = m.end()
            if end_idx < len(line) and line[end_idx].isalpha():
                continue
                
            val_str = m.group(1).replace(",", "")
            try:
                val = float(val_str)
            except ValueError:
                continue
                
            if val == 0:
                continue
                
            score = 0
            context_before = "\n".join(lines[max(0, i-5):i])
            context_after = "\n".join(lines[i+1:min(len(lines), i+6)])
            
            # 位於商品主資訊區 (+50)
            if i < 20:
                score += 50
                
            # 靠近標題 (+30)
            if title and title in context_before:
                score += 30
            elif i < 10: 
                score += 30
                
            # 靠近「優惠活動」之前 (+20)
            if "優惠活動" in context_after[:50]:
                score += 20
                
            # 包含 $ 或 NT$ (+10)
            if "$" in m.group(0):
                score += 10
                
            # 在運費、推薦商品、商品單價彈窗附近 (-100)
            if any(ex in context_before for ex in ["賣家精選商品", "推薦你參考"]):
                score -= 100
                
            candidates.append({
                "val": val,
                "score": score
            })
            
    if not candidates:
        return None
        
    candidates.sort(key=lambda x: x["score"], reverse=True)
    best = candidates[0]
    if best["score"] >= 0:
        return best["val"]
        
    return None


class RutenParser(GenericParser):
    platform = "ruten"

    @classmethod
    def extract_price(cls, html_text: str, raw_text: str) -> Tuple[Optional[float], str]:
        # 1. JSON / RT.context
        match = re.search(r'RT\.context\s*=\s*({.*?});', html_text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                if "item" in data and "directPrice" in data["item"]:
                    price = float(data["item"]["directPrice"])
                    return price, "json"
            except json.JSONDecodeError:
                pass
                
        # 2. JSON LD / Meta (fallback to generic)
        price, evidence = super().extract_price(html_text, raw_text)
        if price is not None:
            return price, evidence

        # 3. HTML text fallback specifically for Ruten
        # We need the title, but extract_price doesn't get title directly.
        # Let's extract title from HTML
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
        title = None
        if title_match:
            title = re.sub(r"\s+", " ", title_match.group(1)).strip()
            
        price = extract_ruten_price_from_text(raw_text, title)
        if price is not None:
            return price, "html_text"
            
        return None, ""
