from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from src.loader import Product, parse_price_value, read_products


OFFICIAL_PRODUCTS_FILE = "official_products.csv"
REVIEW_DECISIONS_FILE = "product_review_decisions.csv"

OFFICIAL_PRODUCT_COLUMNS = [
    "official_product_name",
    "official_product_url",
    "official_image_url",
    "official_image_path",
    "official_image_hash",
    "matched_db_product_name",
    "suggested_price",
    "match_score",
    "monitor_status",
    "review_status",
    "decision_source",
    "reviewed_at",
    "reviewer",
    "note",
]

REVIEW_DECISION_COLUMNS = [
    "official_product_name",
    "official_product_url",
    "decision",
    "matched_db_product_name",
    "suggested_price",
    "reviewed_at",
    "reviewer",
    "note",
]

ACTIVE_STATUS = "active"
PENDING_REVIEW_STATUS = "pending_review"
MISSING_PRICE_STATUS = "missing_suggested_price"
REJECTED_STATUS = "rejected"
INACTIVE_STATUS = "inactive_on_official_site"


@dataclass(frozen=True)
class MonitorCatalog:
    products: list[Product]
    source: str
    active_count: int
    total_count: int


def official_products_path(project_root: Path) -> Path:
    return project_root / "data" / OFFICIAL_PRODUCTS_FILE


def review_decisions_path(project_root: Path) -> Path:
    return project_root / "data" / REVIEW_DECISIONS_FILE


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            with path.open("r", encoding=encoding, newline="") as csv_file:
                return [dict(row) for row in csv.DictReader(csv_file)]
        except UnicodeDecodeError:
            continue
    return []


def write_csv_dicts(path: Path, rows: Iterable[dict[str, str]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def normalize_official_row(row: dict[str, str]) -> dict[str, str]:
    normalized = {column: "" for column in OFFICIAL_PRODUCT_COLUMNS}
    for key, value in row.items():
        if key in normalized:
            normalized[key] = (value or "").strip()
    return normalized


def read_official_products(project_root: Path) -> list[dict[str, str]]:
    return [normalize_official_row(row) for row in read_csv_dicts(official_products_path(project_root))]


def write_official_products(project_root: Path, rows: Iterable[dict[str, str]]) -> None:
    write_csv_dicts(
        official_products_path(project_root),
        [normalize_official_row(row) for row in rows],
        OFFICIAL_PRODUCT_COLUMNS,
    )


def read_review_decisions(project_root: Path) -> list[dict[str, str]]:
    decisions = []
    for row in read_csv_dicts(review_decisions_path(project_root)):
        normalized = {column: "" for column in REVIEW_DECISION_COLUMNS}
        for key, value in row.items():
            if key in normalized:
                normalized[key] = (value or "").strip()
        decisions.append(normalized)
    return decisions


def write_review_decisions(project_root: Path, rows: Iterable[dict[str, str]]) -> None:
    write_csv_dicts(review_decisions_path(project_root), rows, REVIEW_DECISION_COLUMNS)


def decision_map(project_root: Path) -> dict[str, dict[str, str]]:
    return {row["official_product_url"]: row for row in read_review_decisions(project_root)}


def db_product_price_map(products_path: Path) -> dict[str, float]:
    products = read_products(products_path)
    return {product.product_name: product.suggested_price for product in products}


def format_price_value(price: float | int | None) -> str:
    if price is None:
        return ""
    if isinstance(price, float) and price.is_integer():
        return str(int(price))
    return str(price)


def apply_decisions_to_official_rows(
    project_root: Path,
    products_path: Path,
    rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    decisions = decision_map(project_root)
    prices = db_product_price_map(products_path)
    applied: list[dict[str, str]] = []
    for row in rows:
        normalized = normalize_official_row(row)
        decision = decisions.get(normalized["official_product_url"])
        if decision:
            apply_decision(normalized, decision, prices)
        applied.append(normalized)
    return applied


def apply_decision(row: dict[str, str], decision: dict[str, str], prices: dict[str, float]) -> None:
    row["review_status"] = decision.get("decision", "")
    row["decision_source"] = "manual"
    row["reviewed_at"] = decision.get("reviewed_at", "")
    row["reviewer"] = decision.get("reviewer", "")
    row["note"] = decision.get("note", "")

    decision_value = decision.get("decision", "")
    matched_name = decision.get("matched_db_product_name", "")
    if decision_value == "approved":
        row["matched_db_product_name"] = matched_name
        price = parse_price_value(decision.get("suggested_price", "")) or prices.get(matched_name)
        row["suggested_price"] = format_price_value(price)
        row["monitor_status"] = ACTIVE_STATUS if price is not None else MISSING_PRICE_STATUS
    elif decision_value == MISSING_PRICE_STATUS:
        row["matched_db_product_name"] = matched_name
        row["suggested_price"] = ""
        row["monitor_status"] = MISSING_PRICE_STATUS
    elif decision_value == "rejected":
        row["monitor_status"] = REJECTED_STATUS
    else:
        row["monitor_status"] = PENDING_REVIEW_STATUS


def upsert_review_decision(
    project_root: Path,
    products_path: Path,
    official_product_url: str,
    official_product_name: str,
    decision: str,
    matched_db_product_name: str = "",
    reviewer: str = "",
    note: str = "",
) -> None:
    prices = db_product_price_map(products_path)
    suggested_price = prices.get(matched_db_product_name)
    new_row = {
        "official_product_name": official_product_name,
        "official_product_url": official_product_url,
        "decision": decision,
        "matched_db_product_name": matched_db_product_name,
        "suggested_price": format_price_value(suggested_price),
        "reviewed_at": datetime.now().isoformat(timespec="seconds"),
        "reviewer": reviewer or "admin",
        "note": note,
    }

    rows = read_review_decisions(project_root)
    updated = False
    for index, row in enumerate(rows):
        if row.get("official_product_url") == official_product_url:
            rows[index] = new_row
            updated = True
            break
    if not updated:
        rows.append(new_row)
    write_review_decisions(project_root, rows)

    official_rows = read_official_products(project_root)
    official_rows = apply_decisions_to_official_rows(project_root, products_path, official_rows)
    write_official_products(project_root, official_rows)


def load_monitor_products(project_root: Path, products_path: Path) -> MonitorCatalog:
    products = read_products(products_path)
    return MonitorCatalog(products, products_path.name, len(products), len(products))
