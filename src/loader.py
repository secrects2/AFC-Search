from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


@dataclass(frozen=True)
class Product:
    suggested_price: float
    product_name: str
    row_index: int
    raw_suggested_price: str
    sku_code: str = ""
    official_product_url: str = ""
    official_image_url: str = ""
    official_image_path: str = ""
    official_image_hash: str = ""
    matched_db_product_name: str = ""
    monitor_status: str = ""


PRICE_CLEAN_RE = re.compile(r"[^\d.,-]")


def parse_price_value(value: object) -> float | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        return None
    text = text.replace("NT$", "").replace("NTD", "").replace("TWD", "")
    text = text.replace("$", "").replace("元", "")
    text = PRICE_CLEAN_RE.sub("", text)
    if not text or text in {"-", ".", ","}:
        return None

    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts[-1]) == 3:
            text = "".join(parts)
        else:
            text = text.replace(",", ".")

    try:
        value_decimal = Decimal(text)
    except InvalidOperation:
        return None
    if value_decimal < 0:
        return None
    value_float = float(value_decimal)
    return int(value_float) if value_float.is_integer() else value_float


def _read_rows(path: Path) -> list[list[str]]:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            with path.open("r", encoding=encoding, newline="") as csv_file:
                return [row for row in csv.reader(csv_file)]
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return []


def _looks_like_header(row: list[str]) -> bool:
    if not row:
        return False
    joined = ",".join(row).lower()
    header_markers = ("suggested", "price", "product", "name", "建議", "售價", "商品", "名稱")
    if any(marker in joined for marker in header_markers):
        return parse_price_value(row[0]) is None
    return False


def _column_index(headers: list[str], candidates: tuple[str, ...], default: int) -> int:
    normalized = [unicodedata.normalize("NFKC", header).lower() for header in headers]
    for index, header in enumerate(normalized):
        if any(candidate in header for candidate in candidates):
            return index
    return default


def _split_no_header_row(row: list[str]) -> tuple[str, str]:
    if len(row) < 2:
        raise ValueError("CSV row must contain suggested price and product name")

    if (
        len(row) > 2
        and row[0].strip().isdigit()
        and row[1].strip().isdigit()
        and len(row[1].strip()) == 3
    ):
        return f"{row[0]},{row[1]}", ",".join(row[2:])

    return row[0], ",".join(row[1:])


def read_products(path: Path) -> list[Product]:
    if not path.exists():
        raise FileNotFoundError(f"Product CSV not found: {path}")

    rows = [row for row in _read_rows(path) if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError(f"Product CSV is empty: {path}")

    has_header = _looks_like_header(rows[0])
    products: list[Product] = []

    if has_header:
        headers = rows[0]
        price_index = _column_index(headers, ("suggested", "price", "建議", "售價"), 0)
        name_index = _column_index(headers, ("product", "name", "商品", "名稱"), 1)
        sku_code_index = _column_index(headers, ("sku_code", "sku", "商品編號"), -1)
        official_product_url_index = _column_index(headers, ("official_product_url", "官網商品頁"), -1)
        official_image_url_index = _column_index(headers, ("official_image_url", "官網圖片"), -1)
        official_image_path_index = _column_index(headers, ("official_image_path", "圖片檔案"), -1)
        official_image_hash_index = _column_index(headers, ("official_image_hash", "圖片雜湊"), -1)
        data_rows = rows[1:]
        start_index = 2
        for offset, row in enumerate(data_rows, start=start_index):
            if max(price_index, name_index) >= len(row):
                continue
            raw_price = row[price_index].strip()
            product_name = row[name_index].strip()
            price = parse_price_value(raw_price)
            if price is None or not product_name:
                continue
            products.append(
                Product(
                    suggested_price=price,
                    product_name=product_name,
                    row_index=offset,
                    raw_suggested_price=raw_price,
                    sku_code=_cell(row, sku_code_index),
                    official_product_url=_cell(row, official_product_url_index),
                    official_image_url=_cell(row, official_image_url_index),
                    official_image_path=_cell(row, official_image_path_index),
                    official_image_hash=_cell(row, official_image_hash_index),
                )
            )
        return products

    for offset, row in enumerate(rows, start=1):
        raw_price, product_name = _split_no_header_row(row)
        product_name = product_name.strip()
        price = parse_price_value(raw_price)
        if price is None or not product_name:
            continue
        products.append(Product(price, product_name, offset, raw_price.strip()))

    if not products:
        raise ValueError("No valid products found in product CSV")
    return products


def _cell(row: list[str], index: int) -> str:
    if index < 0 or index >= len(row):
        return ""
    return row[index].strip()
