from __future__ import annotations

import argparse
import csv
import html
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

from src.image_matcher import average_hash_bytes, stable_image_filename
from src.loader import parse_price_value
from src.matcher import match_score
from src.monitor_catalog import (
    ACTIVE_STATUS,
    MISSING_PRICE_STATUS,
    OFFICIAL_PRODUCT_COLUMNS,
    PENDING_REVIEW_STATUS,
    apply_decisions_to_official_rows,
    write_official_products,
)


PRODUCT_COLUMNS = [
    "suggested_price",
    "product_name",
    "sku_code",
    "official_product_url",
    "official_image_url",
    "official_image_path",
    "official_image_hash",
    "official_match_score",
    "official_sync_status",
]


@dataclass(frozen=True)
class OfficialProduct:
    title: str
    url: str
    image_url: str


def main() -> int:
    args = parse_args()
    products_path = Path(args.products)
    project_root = products_path.resolve().parents[1]
    products = read_product_records(products_path)
    official_products = fetch_official_products(args.delay_seconds)
    image_dir = project_root / "data" / "official_images"
    image_dir.mkdir(parents=True, exist_ok=True)

    updated = []
    for record in products:
        best = choose_best_official_product(record["product_name"], official_products)
        if best and best[1] >= args.match_threshold:
            official, score = best
            image_path, image_hash, status = download_reference_image(
                image_dir, record["product_name"], official.image_url
            )
            record.update(
                {
                    "official_product_url": official.url,
                    "official_image_url": official.image_url,
                    "official_image_path": str(image_path.relative_to(project_root)) if image_path else "",
                    "official_image_hash": image_hash,
                    "official_match_score": str(score),
                    "official_sync_status": status,
                }
            )
        else:
            record.update(
                {
                    "official_product_url": "",
                    "official_image_url": "",
                    "official_image_path": "",
                    "official_image_hash": "",
                    "official_match_score": "0",
                    "official_sync_status": "not_found",
                }
            )
        updated.append(record)

    write_product_records(products_path, updated)
    official_catalog_rows = build_official_catalog(
        project_root,
        products_path,
        products,
        official_products,
        image_dir,
        args.match_threshold,
        args.review_threshold,
    )
    write_official_products(project_root, official_catalog_rows)
    matched = sum(1 for row in updated if row["official_sync_status"] == "ok")
    active = sum(1 for row in official_catalog_rows if row["monitor_status"] == ACTIVE_STATUS)
    pending = sum(1 for row in official_catalog_rows if row["monitor_status"] == PENDING_REVIEW_STATUS)
    missing = sum(1 for row in official_catalog_rows if row["monitor_status"] == MISSING_PRICE_STATUS)
    print(
        f"official_products={len(official_products)} products={len(updated)} "
        f"matched={matched} active={active} pending_review={pending} missing_price={missing}"
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步 AFC 官網商品圖片到商品主檔")
    parser.add_argument("--products", default="data/AFC商品.csv")
    parser.add_argument("--match-threshold", type=int, default=85)
    parser.add_argument("--review-threshold", type=int, default=60)
    parser.add_argument("--delay-seconds", type=float, default=0.2)
    return parser.parse_args()


def read_product_records(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        all_rows = [row for row in csv.reader(csv_file) if any(cell.strip() for cell in row)]
    if not all_rows:
        return []

    first = all_rows[0]
    has_header = parse_price_value(first[0]) is None
    rows: list[dict[str, str]] = []
    if has_header:
        headers = first
        for row in all_rows[1:]:
            rows.append(normalize_record(dict(zip(headers, row))))
    else:
        for row in all_rows:
            if len(row) < 2:
                continue
            rows.append(
                normalize_record(
                    {
                        "suggested_price": row[0].strip(),
                        "product_name": ",".join(row[1:]).strip(),
                    }
                )
            )
    return rows


def normalize_record(record: dict[str, str]) -> dict[str, str]:
    normalized = {column: "" for column in PRODUCT_COLUMNS}
    for key, value in record.items():
        if key in normalized:
            normalized[key] = (value or "").strip()
    return normalized


def write_product_records(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=PRODUCT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def fetch_official_products(delay_seconds: float) -> list[OfficialProduct]:
    session = requests.Session()
    session.headers.update({"User-Agent": "AFCPriceMonitor/1.0"})
    sitemap = session.get("https://www.afc-life.com/sitemap.xml?locale=zh-hant", timeout=30)
    sitemap.raise_for_status()
    urls = re.findall(
        r"<loc>(https://www\.afc-life\.com/zh-hant/products/.*?)</loc>",
        sitemap.text,
    )

    products: list[OfficialProduct] = []
    for index, url in enumerate(urls, start=1):
        try:
            response = session.get(html.unescape(url), timeout=30)
            response.raise_for_status()
            official = parse_official_product(response.text, html.unescape(url))
            if official:
                products.append(official)
        except Exception:
            continue
        if delay_seconds and index < len(urls):
            time.sleep(delay_seconds)
    return products


def parse_official_product(html_text: str, url: str) -> OfficialProduct | None:
    soup = BeautifulSoup(html_text, "html.parser")
    title = meta_content(soup, "property", "og:title") or (
        soup.title.get_text(" ", strip=True) if soup.title else ""
    )
    image_url = meta_content(soup, "property", "og:image") or meta_content(soup, "name", "twitter:image")
    title = title.replace(" - AFC Taiwan官方網站", "").strip()
    if not title or not image_url:
        return None
    return OfficialProduct(html.unescape(title), url, html.unescape(image_url))


def meta_content(soup: BeautifulSoup, attr_name: str, attr_value: str) -> str:
    tag = soup.find("meta", attrs={attr_name: attr_value})
    return str(tag["content"]).strip() if tag and tag.get("content") else ""


def choose_best_official_product(
    product_name: str, official_products: list[OfficialProduct]
) -> tuple[OfficialProduct, int] | None:
    scored = []
    for official in official_products:
        score = match_score(product_name, official.title)
        penalty = combo_penalty(official.title, official.url)
        scored.append((score - penalty, score, official))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if not scored:
        return None
    _, score, official = scored[0]
    return official, score


def build_official_catalog(
    project_root: Path,
    products_path: Path,
    db_products: list[dict[str, str]],
    official_products: list[OfficialProduct],
    image_dir: Path,
    match_threshold: int,
    review_threshold: int,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for official in official_products:
        if is_combo_product(official.title, official.url):
            continue
        best = choose_best_db_product(official, db_products)
        best_record = best[0] if best else None
        best_score = best[1] if best else 0
        image_path, image_hash, image_status = download_reference_image(
            image_dir, official.title, official.image_url
        )
        matched_name = best_record["product_name"] if best_record and best_score >= review_threshold else ""
        suggested_price = best_record["suggested_price"] if best_record and best_score >= review_threshold else ""
        if best_record and best_score >= match_threshold and parse_price_value(suggested_price) is not None:
            monitor_status = ACTIVE_STATUS
            review_status = "auto_approved"
            decision_source = "auto"
        elif best_record and best_score >= review_threshold:
            monitor_status = PENDING_REVIEW_STATUS
            review_status = PENDING_REVIEW_STATUS
            decision_source = "auto"
        else:
            monitor_status = MISSING_PRICE_STATUS
            review_status = MISSING_PRICE_STATUS
            decision_source = "auto"

        row = {column: "" for column in OFFICIAL_PRODUCT_COLUMNS}
        row.update(
            {
                "official_product_name": official.title,
                "official_product_url": official.url,
                "official_image_url": official.image_url,
                "official_image_path": str(image_path.relative_to(project_root)) if image_path else "",
                "official_image_hash": image_hash,
                "matched_db_product_name": matched_name,
                "suggested_price": suggested_price,
                "match_score": str(best_score),
                "monitor_status": monitor_status,
                "review_status": review_status,
                "decision_source": decision_source,
                "note": "" if image_status == "ok" else image_status,
            }
        )
        rows.append(row)

    return apply_decisions_to_official_rows(project_root, products_path, rows)


def choose_best_db_product(
    official: OfficialProduct,
    db_products: list[dict[str, str]],
) -> tuple[dict[str, str], int] | None:
    scored = []
    for record in db_products:
        score = match_score(record["product_name"], official.title)
        penalty = combo_penalty(official.title, official.url)
        scored.append((score - penalty, score, record))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if not scored:
        return None
    _, score, record = scored[0]
    return record, score


COMBO_MARKERS = (
    "二入組", "三入組", "四入組", "二入", "三入", "四入",
    "兩盒組", "盒組", "多入", "任選", "體驗包", "團購",
    "組合", "加贈", "限定組", "體驗試用",
)


def is_combo_product(title: str, url: str) -> bool:
    text = f"{title} {unquote(url)}"
    return any(marker in text for marker in COMBO_MARKERS)


def combo_penalty(title: str, url: str) -> int:
    text = f"{title} {unquote(url)}"
    penalty = 0
    for marker in COMBO_MARKERS:
        if marker in text:
            penalty += 100
    return penalty


def download_reference_image(
    image_dir: Path,
    product_name: str,
    image_url: str,
) -> tuple[Path | None, str, str]:
    try:
        response = requests.get(image_url, timeout=30, headers={"User-Agent": "AFCPriceMonitor/1.0"})
        response.raise_for_status()
        image_hash = average_hash_bytes(response.content)
        path = image_dir / stable_image_filename(product_name, image_url)
        path.write_bytes(response.content)
        return path, image_hash, "ok"
    except Exception:
        return None, "", "image_download_failed"


if __name__ == "__main__":
    raise SystemExit(main())
