from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


COMMON_SUFFIXES = (
    "錠狀食品",
    "膠囊食品",
    "粉末食品",
    "食品",
    "日本原裝",
    "袋裝",
    "包裝",
    "盒裝",
    "瓶裝",
    "膠囊",
    "錠",
    "粒",
    "瓶",
    "盒",
    "包",
)


def normalize_name(value: str, strip_descriptors: bool = True) -> str:
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"[【】〖〗\[\]（）()]", "", text)
    text = re.sub(r"\bafc\b", "", text)
    text = text.replace("afc_", "")
    text = text.replace("afc ", "")
    text = text.replace("afc", "")
    if strip_descriptors:
        text = re.sub(r"\d+\s*(粒|錠|顆|包|盒|瓶|日份|個月份)", "", text)
        for suffix in COMMON_SUFFIXES:
            text = text.replace(suffix.lower(), "")
    text = text.replace("_", "")
    text = re.sub(r"\s+", "", text)
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def match_score(expected_name: str, found_title: str) -> int:
    expected = normalize_name(expected_name)
    found = normalize_name(found_title)
    if not expected or not found:
        return 0
    if expected in found or found in expected:
        return 100
    try:
        from rapidfuzz import fuzz  # type: ignore

        return int(round(fuzz.WRatio(expected, found)))
    except Exception:
        return int(round(SequenceMatcher(None, expected, found).ratio() * 100))


def classify_match(score: int, threshold: int = 85) -> str:
    if score >= threshold:
        return "matched"
    if score >= 70:
        return "needs_review"
    return "unmatched"
