"""SQLite database layer for AFC Price Monitor.

Provides all CRUD operations for products, candidates, price snapshots,
and API usage logs. Uses Python built-in sqlite3 (no ORM).
"""
from __future__ import annotations

import json
import sqlite3
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

LOGGER = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    brand TEXT DEFAULT '',
    suggested_price REAL,
    keywords TEXT DEFAULT '',
    exclude_keywords TEXT DEFAULT '',
    priority INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT 1,
    official_image_url TEXT DEFAULT '',
    official_image_path TEXT DEFAULT '',
    official_image_hash TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS product_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    platform TEXT NOT NULL,
    title TEXT DEFAULT '',
    url TEXT NOT NULL UNIQUE,
    seller TEXT DEFAULT '',
    first_seen_at TEXT DEFAULT (datetime('now')),
    last_checked_at TEXT,
    last_price REAL,
    status TEXT DEFAULT 'active'
        CHECK(status IN ('active','normal','suspected_violation',
                         'price_unknown','excluded','error','inactive','blocked')),
    source_found_by TEXT DEFAULT 'manual'
        CHECK(source_found_by IN ('manual','serper','serpapi','brave',
                                   'crawler','mcp')),
    raw_data TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES product_candidates(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    checked_at TEXT DEFAULT (datetime('now')),
    price REAL,
    suggested_price REAL,
    price_diff REAL,
    is_violation BOOLEAN DEFAULT 0,
    screenshot_path TEXT DEFAULT '',
    error_message TEXT DEFAULT '',
    raw_data TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS api_usage_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    query TEXT DEFAULT '',
    used_at TEXT DEFAULT (datetime('now')),
    result_count INTEGER DEFAULT 0,
    success BOOLEAN DEFAULT 1,
    error_message TEXT DEFAULT '',
    purpose TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_candidates_product ON product_candidates(product_id);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON product_candidates(status);
CREATE INDEX IF NOT EXISTS idx_snapshots_candidate ON price_snapshots(candidate_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_checked ON price_snapshots(checked_at);
CREATE INDEX IF NOT EXISTS idx_api_usage_date ON api_usage_logs(used_at);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ProductRow:
    id: int = 0
    product_name: str = ""
    brand: str = ""
    suggested_price: float | None = None
    keywords: str = ""
    exclude_keywords: str = ""
    priority: int = 0
    is_active: bool = True
    official_image_url: str = ""
    official_image_path: str = ""
    official_image_hash: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class CandidateRow:
    id: int = 0
    product_id: int = 0
    platform: str = ""
    title: str = ""
    url: str = ""
    seller: str = ""
    first_seen_at: str = ""
    last_checked_at: str = ""
    last_price: float | None = None
    status: str = "active"
    source_found_by: str = "manual"
    raw_data: str = "{}"
    # joined fields (optional, from product)
    product_name: str = ""
    suggested_price: float | None = None
    brand: str = ""


@dataclass
class SnapshotRow:
    id: int = 0
    candidate_id: int = 0
    product_id: int = 0
    checked_at: str = ""
    price: float | None = None
    suggested_price: float | None = None
    price_diff: float | None = None
    is_violation: bool = False
    screenshot_path: str = ""
    error_message: str = ""
    raw_data: str = "{}"
    # joined fields
    product_name: str = ""
    platform: str = ""
    url: str = ""
    seller: str = ""
    title: str = ""
    brand: str = ""
    candidate_status: str = ""
    source_found_by: str = ""
    first_seen_at: str = ""


@dataclass
class ApiUsageRow:
    id: int = 0
    provider: str = ""
    query: str = ""
    used_at: str = ""
    result_count: int = 0
    success: bool = True
    error_message: str = ""
    purpose: str = ""


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class Database:
    """SQLite database wrapper for AFC Price Monitor."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _cursor(self) -> Iterator[tuple[sqlite3.Connection, sqlite3.Cursor]]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            yield conn, cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(SCHEMA_SQL)
            # Migration: add image columns if missing
            existing = {r[1] for r in conn.execute('PRAGMA table_info(products)').fetchall()}
            for col in ('official_image_url', 'official_image_path', 'official_image_hash'):
                if col not in existing:
                    conn.execute(f"ALTER TABLE products ADD COLUMN {col} TEXT DEFAULT ''")
            conn.commit()
        finally:
            conn.close()

    # -- Products -----------------------------------------------------------

    def upsert_product(
        self,
        product_name: str,
        suggested_price: float | None = None,
        brand: str = "",
        keywords: str = "",
        exclude_keywords: str = "",
        priority: int = 0,
        is_active: bool = True,
        official_image_url: str = "",
        official_image_path: str = "",
        official_image_hash: str = "",
    ) -> int:
        """Insert or update a product by name. Returns product id."""
        with self._cursor() as (conn, cur):
            cur.execute(
                "SELECT id FROM products WHERE product_name = ?",
                (product_name,),
            )
            row = cur.fetchone()
            now = _now_iso()
            if row:
                cur.execute(
                    """UPDATE products SET suggested_price=?, brand=?, keywords=?,
                       exclude_keywords=?, priority=?, is_active=?,
                       official_image_url=?, official_image_path=?, official_image_hash=?,
                       updated_at=?
                       WHERE id=?""",
                    (suggested_price, brand, keywords, exclude_keywords,
                     priority, is_active, official_image_url, official_image_path,
                     official_image_hash, now, row["id"]),
                )
                return row["id"]
            cur.execute(
                """INSERT INTO products
                   (product_name, suggested_price, brand, keywords,
                    exclude_keywords, priority, is_active,
                    official_image_url, official_image_path, official_image_hash,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (product_name, suggested_price, brand, keywords,
                 exclude_keywords, priority, is_active,
                 official_image_url, official_image_path, official_image_hash,
                 now, now),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def get_product(self, product_id: int) -> ProductRow | None:
        with self._cursor() as (conn, cur):
            cur.execute("SELECT * FROM products WHERE id=?", (product_id,))
            row = cur.fetchone()
            return self._to_product(row) if row else None

    def list_products(self, active_only: bool = True) -> list[ProductRow]:
        with self._cursor() as (conn, cur):
            sql = "SELECT * FROM products"
            if active_only:
                sql += " WHERE is_active=1"
            sql += " ORDER BY priority DESC, product_name"
            cur.execute(sql)
            return [self._to_product(r) for r in cur.fetchall()]

    def delete_product(self, product_id: int) -> bool:
        """Delete a product and its related candidates/snapshots. Returns True if deleted."""
        with self._cursor() as (conn, cur):
            cur.execute("SELECT id FROM products WHERE id=?", (product_id,))
            if not cur.fetchone():
                return False
            # Delete related snapshots first (FK constraint)
            cur.execute(
                "DELETE FROM price_snapshots WHERE candidate_id IN "
                "(SELECT id FROM product_candidates WHERE product_id=?)",
                (product_id,),
            )
            # Delete related candidates
            cur.execute("DELETE FROM product_candidates WHERE product_id=?", (product_id,))
            # Delete the product
            cur.execute("DELETE FROM products WHERE id=?", (product_id,))
            return True

    @staticmethod
    def _to_product(row: sqlite3.Row) -> ProductRow:
        keys = row.keys()
        return ProductRow(
            id=row["id"],
            product_name=row["product_name"],
            brand=row["brand"] or "",
            suggested_price=row["suggested_price"],
            keywords=row["keywords"] or "",
            exclude_keywords=row["exclude_keywords"] or "",
            priority=row["priority"] or 0,
            is_active=bool(row["is_active"]),
            official_image_url=row["official_image_url"] if "official_image_url" in keys else "",
            official_image_path=row["official_image_path"] if "official_image_path" in keys else "",
            official_image_hash=row["official_image_hash"] if "official_image_hash" in keys else "",
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    # -- Candidates ---------------------------------------------------------

    def upsert_candidate(
        self,
        product_id: int,
        url: str,
        platform: str,
        title: str = "",
        seller: str = "",
        source_found_by: str = "manual",
        status: str = "active",
    ) -> int:
        """Insert or update a candidate by URL. Returns candidate id."""
        with self._cursor() as (conn, cur):
            cur.execute(
                "SELECT id FROM product_candidates WHERE url=?", (url,),
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    """UPDATE product_candidates SET product_id=?, platform=?,
                       title=?, seller=?, source_found_by=?
                       WHERE id=?""",
                    (product_id, platform, title, seller, source_found_by, row["id"]),
                )
                return row["id"]
            cur.execute(
                """INSERT INTO product_candidates
                   (product_id, platform, title, url, seller,
                    source_found_by, status)
                   VALUES (?,?,?,?,?,?,?)""",
                (product_id, platform, title, url, seller, source_found_by, status),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def get_active_candidates(self, product_id: int | None = None) -> list[CandidateRow]:
        """Get candidates with status in (active, normal, suspected_violation, price_unknown)."""
        with self._cursor() as (conn, cur):
            sql = """
                SELECT c.*, p.product_name, p.suggested_price, p.brand
                FROM product_candidates c
                JOIN products p ON c.product_id = p.id
                WHERE c.status IN ('active','normal','suspected_violation','price_unknown')
                  AND p.is_active = 1
            """
            params: list[Any] = []
            if product_id is not None:
                sql += " AND c.product_id = ?"
                params.append(product_id)
            sql += " ORDER BY p.priority DESC, p.product_name, c.platform"
            cur.execute(sql, params)
            return [self._to_candidate(r) for r in cur.fetchall()]

    def list_candidates(
        self, product_id: int | None = None, status: str | None = None
    ) -> list[CandidateRow]:
        with self._cursor() as (conn, cur):
            sql = """
                SELECT c.*, p.product_name, p.suggested_price, p.brand
                FROM product_candidates c
                JOIN products p ON c.product_id = p.id WHERE 1=1
            """
            params: list[Any] = []
            if product_id is not None:
                sql += " AND c.product_id = ?"
                params.append(product_id)
            if status:
                sql += " AND c.status = ?"
                params.append(status)
            sql += " ORDER BY p.product_name, c.platform"
            cur.execute(sql, params)
            return [self._to_candidate(r) for r in cur.fetchall()]

    def update_candidate_status(
        self,
        candidate_id: int,
        status: str,
        last_price: float | None = None,
    ) -> None:
        with self._cursor() as (conn, cur):
            cur.execute(
                """UPDATE product_candidates
                   SET status=?, last_price=?, last_checked_at=?
                   WHERE id=?""",
                (status, last_price, _now_iso(), candidate_id),
            )

    def disable_candidate(self, candidate_id: int) -> None:
        self.update_candidate_status(candidate_id, "excluded")

    @staticmethod
    def _to_candidate(row: sqlite3.Row) -> CandidateRow:
        keys = row.keys()
        return CandidateRow(
            id=row["id"],
            product_id=row["product_id"],
            platform=row["platform"] or "",
            title=row["title"] or "",
            url=row["url"],
            seller=row["seller"] or "",
            first_seen_at=row["first_seen_at"] or "",
            last_checked_at=row["last_checked_at"] or "",
            last_price=row["last_price"],
            status=row["status"] or "active",
            source_found_by=row["source_found_by"] or "manual",
            raw_data=row["raw_data"] or "{}",
            product_name=row["product_name"] if "product_name" in keys else "",
            suggested_price=row["suggested_price"] if "suggested_price" in keys else None,
            brand=row["brand"] if "brand" in keys else "",
        )

    # -- Price Snapshots ----------------------------------------------------

    def insert_snapshot(
        self,
        candidate_id: int,
        product_id: int,
        price: float | None,
        suggested_price: float | None,
        is_violation: bool = False,
        screenshot_path: str = "",
        error_message: str = "",
        raw_data: dict[str, Any] | None = None,
    ) -> int:
        price_diff = None
        if price is not None and suggested_price is not None:
            price_diff = round(price - suggested_price, 2)
        with self._cursor() as (conn, cur):
            cur.execute(
                """INSERT INTO price_snapshots
                   (candidate_id, product_id, price, suggested_price,
                    price_diff, is_violation, screenshot_path,
                    error_message, raw_data)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (candidate_id, product_id, price, suggested_price,
                 price_diff, is_violation, screenshot_path,
                 error_message, json.dumps(raw_data or {}, ensure_ascii=False)),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def get_snapshots(
        self,
        date: str | None = None,
        violation_only: bool = False,
        limit: int = 500,
    ) -> list[SnapshotRow]:
        with self._cursor() as (conn, cur):
            sql = """
                SELECT s.*, p.product_name, p.brand,
                       c.platform, c.url, c.seller, c.title,
                       c.status AS candidate_status,
                       c.source_found_by, c.first_seen_at
                FROM price_snapshots s
                JOIN product_candidates c ON s.candidate_id = c.id
                JOIN products p ON s.product_id = p.id
                WHERE 1=1
            """
            params: list[Any] = []
            if date:
                sql += " AND s.checked_at LIKE ?"
                params.append(f"{date}%")
            if violation_only:
                sql += " AND s.is_violation = 1"
            sql += " ORDER BY s.checked_at DESC LIMIT ?"
            params.append(limit)
            cur.execute(sql, params)
            return [self._to_snapshot(r) for r in cur.fetchall()]

    @staticmethod
    def _to_snapshot(row: sqlite3.Row) -> SnapshotRow:
        keys = row.keys()
        return SnapshotRow(
            id=row["id"],
            candidate_id=row["candidate_id"],
            product_id=row["product_id"],
            checked_at=row["checked_at"] or "",
            price=row["price"],
            suggested_price=row["suggested_price"],
            price_diff=row["price_diff"],
            is_violation=bool(row["is_violation"]),
            screenshot_path=row["screenshot_path"] or "",
            error_message=row["error_message"] or "",
            raw_data=row["raw_data"] or "{}",
            product_name=row["product_name"] if "product_name" in keys else "",
            platform=row["platform"] if "platform" in keys else "",
            url=row["url"] if "url" in keys else "",
            seller=row["seller"] if "seller" in keys else "",
            title=row["title"] if "title" in keys else "",
            brand=row["brand"] if "brand" in keys else "",
            candidate_status=row["candidate_status"] if "candidate_status" in keys else "",
            source_found_by=row["source_found_by"] if "source_found_by" in keys else "",
            first_seen_at=row["first_seen_at"] if "first_seen_at" in keys else "",
        )

    # -- API Usage ----------------------------------------------------------

    def log_api_usage(
        self,
        provider: str,
        query: str = "",
        result_count: int = 0,
        success: bool = True,
        error_message: str = "",
        purpose: str = "",
    ) -> None:
        with self._cursor() as (conn, cur):
            cur.execute(
                """INSERT INTO api_usage_logs
                   (provider, query, result_count, success, error_message, purpose)
                   VALUES (?,?,?,?,?,?)""",
                (provider, query, result_count, success, error_message, purpose),
            )

    def get_api_usage_count(self, provider: str | None = None, since: str = "") -> int:
        with self._cursor() as (conn, cur):
            sql = "SELECT COUNT(*) AS cnt FROM api_usage_logs WHERE success=1"
            params: list[Any] = []
            if provider:
                sql += " AND provider=?"
                params.append(provider)
            if since:
                sql += " AND used_at >= ?"
                params.append(since)
            cur.execute(sql, params)
            return cur.fetchone()["cnt"]

    def get_api_usage_logs(self, limit: int = 100) -> list[ApiUsageRow]:
        with self._cursor() as (conn, cur):
            cur.execute(
                "SELECT * FROM api_usage_logs ORDER BY used_at DESC LIMIT ?",
                (limit,),
            )
            return [
                ApiUsageRow(
                    id=r["id"],
                    provider=r["provider"],
                    query=r["query"] or "",
                    used_at=r["used_at"] or "",
                    result_count=r["result_count"] or 0,
                    success=bool(r["success"]),
                    error_message=r["error_message"] or "",
                    purpose=r["purpose"] or "",
                )
                for r in cur.fetchall()
            ]

    # -- Stats --------------------------------------------------------------

    def summary_stats(self) -> dict[str, Any]:
        with self._cursor() as (conn, cur):
            cur.execute("SELECT COUNT(*) AS c FROM products WHERE is_active=1")
            products = cur.fetchone()["c"]
            cur.execute(
                """SELECT COUNT(*) AS c FROM product_candidates
                   WHERE status IN ('active','normal','suspected_violation','price_unknown')"""
            )
            active_candidates = cur.fetchone()["c"]
            cur.execute(
                "SELECT COUNT(*) AS c FROM product_candidates WHERE status='suspected_violation'"
            )
            violations = cur.fetchone()["c"]
            cur.execute(
                "SELECT COUNT(*) AS c FROM product_candidates WHERE status='price_unknown'"
            )
            price_unknown = cur.fetchone()["c"]
            cur.execute(
                "SELECT MAX(checked_at) AS last FROM price_snapshots"
            )
            last_row = cur.fetchone()
            last_check = last_row["last"] if last_row else ""
            return {
                "total_products": products,
                "active_candidates": active_candidates,
                "violations": violations,
                "price_unknown": price_unknown,
                "last_check_time": last_check or "",
            }
