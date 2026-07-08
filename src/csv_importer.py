"""Import AFC商品.csv and manual_links.csv into SQLite database."""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

from src.database import Database
from src.loader import parse_price_value
from src.search.serp_api import detect_platform

LOGGER = logging.getLogger(__name__)

# Column aliases for auto-mapping
PRODUCT_NAME_ALIASES = {"product_name", "商品名稱", "名稱", "品名", "official_product_name"}
PRICE_ALIASES = {"suggested_price", "建議售價", "售價", "定價", "price"}
BRAND_ALIASES = {"brand", "品牌"}
KEYWORDS_ALIASES = {"keywords", "關鍵字"}
EXCLUDE_ALIASES = {"exclude_keywords", "排除字", "排除關鍵字"}
SKU_ALIASES = {"sku_code", "sku", "品號", "商品編號"}
IMAGE_URL_ALIASES = {"official_image_url"}
IMAGE_PATH_ALIASES = {"official_image_path"}
IMAGE_HASH_ALIASES = {"official_image_hash"}

# Bundle keywords to skip
BUNDLE_KEYWORDS = {"套組", "組合", "二入組", "三入組", "禮盒", "超值組"}


def _find_column(header: list[str], aliases: set[str]) -> int:
    """Find column index by matching any alias (case-insensitive)."""
    for i, col in enumerate(header):
        if col.strip().lower() in {a.lower() for a in aliases}:
            return i
    return -1


def _is_bundle(name: str) -> bool:
    """Check if product name contains bundle keywords."""
    return any(kw in name for kw in BUNDLE_KEYWORDS)


def import_products_csv(db: Database, csv_path: Path) -> dict[str, int]:
    """Import products from AFC商品.csv into database.

    Returns dict with counts: imported, skipped, errors.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    rows = list(csv.reader(csv_path.read_text(encoding="utf-8-sig").splitlines()))
    if not rows:
        return {"imported": 0, "skipped": 0, "errors": 0}

    header = [c.strip() for c in rows[0]]
    name_idx = _find_column(header, PRODUCT_NAME_ALIASES)
    price_idx = _find_column(header, PRICE_ALIASES)
    brand_idx = _find_column(header, BRAND_ALIASES)
    kw_idx = _find_column(header, KEYWORDS_ALIASES)
    excl_idx = _find_column(header, EXCLUDE_ALIASES)
    sku_idx = _find_column(header, SKU_ALIASES)
    img_url_idx = _find_column(header, IMAGE_URL_ALIASES)
    img_path_idx = _find_column(header, IMAGE_PATH_ALIASES)
    img_hash_idx = _find_column(header, IMAGE_HASH_ALIASES)

    if name_idx < 0:
        raise ValueError(f"找不到商品名稱欄位。Header: {header}")

    imported = 0
    skipped = 0
    errors = 0

    for row_num, row in enumerate(rows[1:], start=2):
        try:
            if len(row) <= name_idx:
                continue
            name = row[name_idx].strip()
            if not name:
                continue
            if _is_bundle(name):
                LOGGER.debug("跳過套組：%s", name)
                skipped += 1
                continue

            price = parse_price_value(row[price_idx]) if price_idx >= 0 and len(row) > price_idx else None
            brand = row[brand_idx].strip() if brand_idx >= 0 and len(row) > brand_idx else ""
            keywords = row[kw_idx].strip() if kw_idx >= 0 and len(row) > kw_idx else ""
            exclude = row[excl_idx].strip() if excl_idx >= 0 and len(row) > excl_idx else ""
            img_url = row[img_url_idx].strip() if img_url_idx >= 0 and len(row) > img_url_idx else ""
            img_path = row[img_path_idx].strip() if img_path_idx >= 0 and len(row) > img_path_idx else ""
            img_hash = row[img_hash_idx].strip() if img_hash_idx >= 0 and len(row) > img_hash_idx else ""

            if price is None:
                LOGGER.warning("Row %d: 建議售價為空或非數字：%s", row_num, name)

            db.upsert_product(
                product_name=name,
                suggested_price=price,
                brand=brand,
                keywords=keywords,
                exclude_keywords=exclude,
                official_image_url=img_url,
                official_image_path=img_path,
                official_image_hash=img_hash,
            )
            imported += 1
        except Exception as exc:
            LOGGER.warning("Row %d import error: %s", row_num, exc)
            errors += 1

    result = {"imported": imported, "skipped": skipped, "errors": errors}
    LOGGER.info("商品匯入完成：%s", result)
    return result


def import_manual_links(db: Database, csv_path: Path) -> dict[str, int]:
    """Import manual_links.csv into product_candidates.

    Links product_name to existing products in DB. Skips if no match.
    """
    if not csv_path.exists():
        LOGGER.info("manual_links.csv 不存在，跳過")
        return {"imported": 0, "skipped": 0}

    rows = list(csv.reader(csv_path.read_text(encoding="utf-8-sig").splitlines()))
    if len(rows) < 2:
        return {"imported": 0, "skipped": 0}

    header = [c.strip().lower() for c in rows[0]]
    name_idx = header.index("product_name") if "product_name" in header else -1
    url_idx = header.index("url") if "url" in header else -1
    plat_idx = header.index("platform") if "platform" in header else -1

    if name_idx < 0 or url_idx < 0:
        LOGGER.warning("manual_links.csv 缺少 product_name 或 url 欄位")
        return {"imported": 0, "skipped": 0}

    # Build product name -> id lookup
    products = db.list_products(active_only=False)
    name_to_id: dict[str, int] = {p.product_name: p.id for p in products}

    imported = 0
    skipped = 0

    for row in rows[1:]:
        if len(row) <= max(name_idx, url_idx):
            continue
        name = row[name_idx].strip()
        url = row[url_idx].strip()
        platform = row[plat_idx].strip() if plat_idx >= 0 and len(row) > plat_idx else ""

        if not name or not url:
            continue
        if not url.startswith(("http://", "https://")):
            skipped += 1
            continue

        product_id = name_to_id.get(name)
        if product_id is None:
            LOGGER.debug("手動連結找不到對應商品：%s", name)
            skipped += 1
            continue

        if not platform:
            platform = detect_platform(url)

        db.upsert_candidate(
            product_id=product_id,
            url=url,
            platform=platform,
            source_found_by="manual",
        )
        imported += 1

    result = {"imported": imported, "skipped": skipped}
    LOGGER.info("手動連結匯入完成：%s", result)
    return result


def full_import(db: Database, project_root: Path) -> dict[str, Any]:
    """Run full import: products CSV + manual links."""
    products_result = import_products_csv(
        db, project_root / "data" / "AFC商品.csv"
    )
    links_result = import_manual_links(
        db, project_root / "data" / "manual_links.csv"
    )
    return {"products": products_result, "links": links_result}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    root = Path(__file__).resolve().parent.parent
    db = Database(root / "data" / "price_monitor.db")
    result = full_import(db, root)
    print(f"匯入結果：{result}")
