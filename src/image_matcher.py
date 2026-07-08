from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import requests

from src.loader import Product


@dataclass(frozen=True)
class ImageMatchResult:
    status: str
    score: int
    matched_url: str = ""


def average_hash_bytes(image_bytes: bytes, hash_size: int = 8) -> str:
    try:
        from PIL import Image  # type: ignore
    except Exception as exc:
        raise RuntimeError("Pillow is required for image hashing") from exc

    with Image.open(io.BytesIO(image_bytes)) as image:
        image = image.convert("L").resize((hash_size, hash_size))
        pixels = list(image.getdata())
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if pixel >= avg else "0" for pixel in pixels)
    return f"{int(bits, 2):0{hash_size * hash_size // 4}x}"


def average_hash_file(path: Path) -> str:
    return average_hash_bytes(path.read_bytes())


def hamming_similarity(hash_a: str, hash_b: str) -> int:
    if not hash_a or not hash_b:
        return 0
    width = max(len(hash_a), len(hash_b))
    a = bin(int(hash_a, 16))[2:].zfill(width * 4)
    b = bin(int(hash_b, 16))[2:].zfill(width * 4)
    if len(a) != len(b):
        return 0
    distance = sum(left != right for left, right in zip(a, b))
    return int(round((1 - distance / len(a)) * 100))


def fetch_image_hash(url: str, timeout_seconds: int = 15) -> str:
    headers = {"User-Agent": "AFCPriceMonitor/1.0"}
    response = requests.get(url, timeout=timeout_seconds, headers=headers)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "image" not in content_type.lower():
        raise ValueError(f"URL is not an image: {content_type}")
    return average_hash_bytes(response.content)


def best_image_match(
    product: Product,
    candidate_urls: list[str] | None,
    threshold: int,
    timeout_seconds: int = 15,
    max_candidates: int = 6,
) -> ImageMatchResult:
    if not product.official_image_hash:
        return ImageMatchResult("no_reference_image", 0)
    if not candidate_urls:
        return ImageMatchResult("no_candidate_image", 0)

    best_score = 0
    best_url = ""
    for url in candidate_urls[:max_candidates]:
        try:
            candidate_hash = fetch_image_hash(url, timeout_seconds)
        except Exception:
            continue
        score = hamming_similarity(product.official_image_hash, candidate_hash)
        if score > best_score:
            best_score = score
            best_url = url
        if score >= threshold:
            return ImageMatchResult("matched", score, url)

    if best_url:
        return ImageMatchResult("not_matched", best_score, best_url)
    return ImageMatchResult("candidate_fetch_failed", 0)


def stable_image_filename(product_name: str, image_url: str) -> str:
    digest = hashlib.sha1(f"{product_name}|{image_url}".encode("utf-8")).hexdigest()[:12]
    return f"{digest}.jpg"

