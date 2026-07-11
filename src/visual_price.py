from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LOGGER = logging.getLogger(__name__)


@dataclass
class VisualPriceResult:
    price: Optional[float]
    raw_text: str
    confidence: float
    method: str
    screenshot_path: str
    crop_path: Optional[str]
    error_message: Optional[str]


def parse_price_from_text(text: str) -> Optional[float]:
    """Parse price from OCR text using proximity to target keywords."""
    if not text:
        return None

    # Replace newlines with spaces for easier proximity checking
    text = text.replace("\n", " ")

    price_pattern = re.compile(r'(?:NT\$?|\$)?\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:元)?', re.IGNORECASE)
    
    matches = []
    for match in price_pattern.finditer(text):
        val_str = match.group(1).replace(",", "")
        try:
            val = float(val_str)
            if val > 0:
                matches.append({
                    "val": val,
                    "start": match.start(),
                    "end": match.end(),
                    "match_text": match.group(0)
                })
        except ValueError:
            pass
            
    if not matches:
        return None

    target_keywords = ["售價", "特價", "優惠價", "立即購買", "加入購物車", "商品價格"]
    exclude_keywords = ["原價", "建議售價", "折價券", "P幣", "回饋", "運費", "%", "滿", "送"]

    best_price = None
    best_score = -999999

    for m in matches:
        # Check narrow context (8 chars before, 8 chars after) for exact exclusions
        narrow_before = text[max(0, m["start"] - 8):m["start"]]
        narrow_after = text[m["end"]:min(len(text), m["end"] + 8)]
        
        # Check wider context
        context_start = max(0, m["start"] - 15)
        context_end = min(len(text), m["end"] + 15)
        context = text[context_start:context_end]
        
        if any(ex in narrow_after for ex in ["%", "P幣", "折價", "回饋", "滿", "送", "件"]):
            continue
            
        score = 0
        
        # If "原價" is right before this number, exclude it
        is_excluded = False
        for ex in ["原價", "建議售價"]:
            if ex in narrow_before:
                is_excluded = True
                break
                
        if is_excluded:
            continue

        # Score based on narrow context (12 chars before)
        context_before = text[max(0, m["start"] - 12):m["start"]]
        for tk in target_keywords:
            if tk in context_before:
                score += 100
                
        if "$" in m["match_text"] or "NT" in m["match_text"].upper():
            score += 50
            
        if score > best_score:
            best_score = score
            best_price = m["val"]

    if best_price is None and matches:
        for m in matches:
            narrow_before = text[max(0, m["start"] - 8):m["start"]]
            narrow_after = text[m["end"]:min(len(text), m["end"] + 8)]
            if not any(ex in narrow_before for ex in ["原價", "建議售價"]) and not any(ex in narrow_after for ex in ["%", "P幣", "折價", "滿", "件"]):
                best_price = m["val"]
                break

    return best_price


class VisualPriceExtractor:
    def __init__(self) -> None:
        self.method = "pytesseract"
        try:
            import pytesseract
            import os
            if os.name == 'nt' and os.path.exists(r"C:\Program Files\Tesseract-OCR\tesseract.exe"):
                pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            self.enabled = True
        except ImportError:
            self.enabled = False

    def extract_from_screenshot(self, screenshot_path: str, platform: str | None = None) -> VisualPriceResult:
        if not self.enabled:
            return VisualPriceResult(
                price=None,
                raw_text="",
                confidence=0.0,
                method="disabled",
                screenshot_path=screenshot_path,
                crop_path=None,
                error_message="pytesseract is not installed"
            )

        try:
            from PIL import Image
            import pytesseract
        except ImportError:
            return VisualPriceResult(
                price=None,
                raw_text="",
                confidence=0.0,
                method="error",
                screenshot_path=screenshot_path,
                crop_path=None,
                error_message="PIL or pytesseract import failed"
            )

        try:
            img = Image.open(screenshot_path)
            crop_path = None
            
            if platform == "pchome":
                width, height = img.size
                left = width // 2
                top = 0
                right = width
                bottom = height // 2
                
                img = img.crop((left, top, right, bottom))
                
                path_obj = Path(screenshot_path)
                crop_path_obj = path_obj.parent / f"{path_obj.stem}_crop{path_obj.suffix}"
                img.save(crop_path_obj)
                crop_path = str(crop_path_obj)

            try:
                raw_text = pytesseract.image_to_string(img, lang="chi_tra+eng")
            except Exception as e:
                return VisualPriceResult(
                    price=None,
                    raw_text="",
                    confidence=0.0,
                    method="error",
                    screenshot_path=screenshot_path,
                    crop_path=crop_path,
                    error_message=f"Tesseract executable not found or failed: {e}"
                )
            
            price = parse_price_from_text(raw_text)
            
            confidence = 0.0
            if price is not None:
                confidence = 0.8 if platform == "pchome" else 0.6
                
            return VisualPriceResult(
                price=price,
                raw_text=raw_text.strip(),
                confidence=confidence,
                method=self.method,
                screenshot_path=screenshot_path,
                crop_path=crop_path,
                error_message=None
            )
            
        except Exception as exc:
            LOGGER.exception("OCR Extraction failed on %s", screenshot_path)
            return VisualPriceResult(
                price=None,
                raw_text="",
                confidence=0.0,
                method="error",
                screenshot_path=screenshot_path,
                crop_path=None,
                error_message=str(exc)
            )
