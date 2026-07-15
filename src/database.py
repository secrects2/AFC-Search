"""SQLite database layer for AFC Price Monitor.

Provides all CRUD operations for products, candidates, price snapshots,
and API usage logs. Uses Python built-in sqlite3 (no ORM).
"""
from __future__ import annotations

import json
import sqlite3
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

LOGGER = logging.getLogger(__name__)


_TRACKING_QUERY_KEYS = {
    "af_c_id",
    "af_reengagement_window",
    "af_siteid",
    "af_sub5",
    "campaignid",
    "ctag",
    "dclid",
    "ecid",
    "fbclid",
    "gclid",
    "impressionid",
    "is_retargeting",
    "link_click_id",
    "lptag",
    "mcid",
    "msclkid",
    "network",
    "pageType",
    "pageValue",
    "pid",
    "puid",
    "redirect",
    "spec",
    "src",
    "subid",
    "subparam",
    "traceid",
    "trafficType",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
    "vtm_channel",
    "vtm_stat_id",
    "wpcid",
    "wref",
    "wtime",
}
_TRACKING_QUERY_PREFIXES = ("af_", "utm_", "vtm_")
_TRACKING_QUERY_KEYS_LOWER = {item.casefold() for item in _TRACKING_QUERY_KEYS}


def is_coupon_source(value: object) -> bool:
    """Return True when a provider/source label identifies coupon traffic."""
    return "coupon" in str(value or "").strip().casefold()


def canonical_final_url(url: str) -> str:
    """Normalize a resolved product URL for duplicate detection."""
    value = str(url or "").strip()
    if not value:
        return ""

    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value

    # Keep one stable identity for Shopee SEO URLs.
    if "shopee.tw" in parsed.netloc.lower():
        match = re.search(r"-i\.(\d+)\.(\d+)", parsed.path)
        if match:
            return f"https://shopee.tw/product/{match.group(1)}/{match.group(2)}"

    query: list[tuple[str, str]] = []
    for key, query_value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.casefold()
        if key_lower in _TRACKING_QUERY_KEYS_LOWER:
            continue
        if any(key_lower.startswith(prefix) for prefix in _TRACKING_QUERY_PREFIXES):
            continue
        query.append((key, query_value))

    normalized_path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            normalized_path,
            urlencode(sorted(query)),
            "",
        )
    )

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
                         'price_unknown','excluded','error','inactive','blocked',
                         'takedown_notified','source_dead','rate_limited',
                         'captcha_required','skipped')),
    source_found_by TEXT DEFAULT 'manual'
        CHECK(source_found_by IN ('manual','serper','serpapi','brave',
                                   'crawler','mcp','findprice','shopee',
                                   'findprice_shopee','feebee','biggo','lbj','fallback','coupon')),
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

CREATE TABLE IF NOT EXISTS global_exclusions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL UNIQUE,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS price_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    candidate_id INTEGER REFERENCES product_candidates(id),
    platform TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'direct_html'
        CHECK(source IN ('direct_dom','direct_json','direct_html',
                         'feebee','visual_ocr','manual','third_party',
                         'biggo','lbj','fallback')),
    url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    seller TEXT NOT NULL DEFAULT '',
    price REAL,
    currency TEXT NOT NULL DEFAULT 'TWD',
    match_score INTEGER DEFAULT 0,
    confidence REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'success'
        CHECK(status IN ('success','blocked','captcha_required','traffic_verify',
                         'rate_limited','source_dead','price_unknown',
                         'excluded','error','skipped_direct_crawl')),
    error_message TEXT NOT NULL DEFAULT '',
    raw_data TEXT NOT NULL DEFAULT '{}',
    observed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_observations_product ON price_observations(product_id);
CREATE INDEX IF NOT EXISTS idx_observations_candidate ON price_observations(candidate_id);
CREATE INDEX IF NOT EXISTS idx_observations_observed ON price_observations(observed_at);

CREATE TABLE IF NOT EXISTS source_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    success_count_24h INTEGER NOT NULL DEFAULT 0,
    error_count_24h INTEGER NOT NULL DEFAULT 0,
    blocked_count_24h INTEGER NOT NULL DEFAULT 0,
    rate_limited_count_24h INTEGER NOT NULL DEFAULT 0,
    success_rate_24h REAL NOT NULL DEFAULT 0.0,
    last_success_at TEXT,
    cooldown_until TEXT,
    enabled BOOLEAN NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_name, platform)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _matches_keyword(keyword: str, values: Iterable[Any]) -> bool:
    needle = keyword.strip().lower()
    if not needle:
        return False
    haystack = "\n".join(str(value or "") for value in values).lower()
    return needle in haystack


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
    final_price: float | None = None
    final_price_source: str = ""
    final_confidence: float = 0.0
    final_status: str = ""
    decision_reason: str = ""
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


@dataclass
class ObservationRow:
    id: int = 0
    product_id: int = 0
    candidate_id: int | None = None
    platform: str = ""
    source: str = "direct_html"
    url: str = ""
    title: str = ""
    seller: str = ""
    price: float | None = None
    currency: str = "TWD"
    match_score: int = 0
    confidence: float = 0.0
    status: str = "success"
    error_message: str = ""
    raw_data: str = "{}"
    observed_at: str = ""
    # joined fields
    product_name: str = ""
    suggested_price: float | None = None


@dataclass
class SourceHealthRow:
    id: int = 0
    source_name: str = ""
    platform: str = ""
    success_count_24h: int = 0
    error_count_24h: int = 0
    blocked_count_24h: int = 0
    rate_limited_count_24h: int = 0
    success_rate_24h: float = 0.0
    last_success_at: str = ""
    cooldown_until: str = ""
    enabled: bool = True
    updated_at: str = ""


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
            self._migrate_candidate_source_constraint(conn)
            self._migrate_candidate_takedown_status(conn)
            self._migrate_takedown_to_excluded(conn)
            self._migrate_snapshot_final_price(conn)
            self._migrate_candidate_multi_source(conn)
            self._migrate_coupon_source_candidates(conn)
            conn.commit()
        finally:
            conn.close()

    def _migrate_candidate_source_constraint(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='product_candidates'"
        ).fetchone()
        if not row or "findprice" in (row["sql"] or ""):
            return

        LOGGER.info("Migrating product_candidates source_found_by constraint")
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("CREATE TEMP TABLE product_candidates_backup AS SELECT * FROM product_candidates")
        conn.execute("DROP TABLE product_candidates")
        conn.execute(
            """
            CREATE TABLE product_candidates (
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
                                     'price_unknown','excluded','error','inactive','blocked',
                                     'takedown_notified','source_dead','rate_limited',
                                     'captcha_required','skipped')),
                source_found_by TEXT DEFAULT 'manual'
                    CHECK(source_found_by IN ('manual','serper','serpapi','brave',
                                               'crawler','mcp','findprice','shopee',
                                               'findprice_shopee','feebee','biggo',
                                               'lbj','fallback','coupon')),
                raw_data TEXT DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO product_candidates
                (id, product_id, platform, title, url, seller, first_seen_at,
                 last_checked_at, last_price, status, source_found_by, raw_data)
            SELECT id, product_id, platform, title, url, seller, first_seen_at,
                   last_checked_at, last_price, status,
                   CASE
                       WHEN source_found_by IN (
                           'manual','serper','serpapi','brave','crawler','mcp',
                           'findprice','shopee','findprice_shopee','feebee','biggo',
                           'lbj','fallback','coupon'
                       )
                       THEN source_found_by
                       ELSE 'crawler'
                   END,
                   raw_data
            FROM product_candidates_backup
            """
        )
        conn.execute("DROP TABLE product_candidates_backup")
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_candidates_product ON product_candidates(product_id);
            CREATE INDEX IF NOT EXISTS idx_candidates_status ON product_candidates(status);
            """
        )
        conn.execute("PRAGMA foreign_keys=ON")

    def _migrate_candidate_takedown_status(self, conn: sqlite3.Connection) -> None:
        """Add takedown_notified to the status CHECK constraint if missing."""
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='product_candidates'"
        ).fetchone()
        if not row or "takedown_notified" in (row["sql"] or ""):
            return

        LOGGER.info("Migrating product_candidates: adding takedown_notified status")
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("CREATE TEMP TABLE _pc_backup AS SELECT * FROM product_candidates")
        conn.execute("DROP TABLE product_candidates")
        conn.execute(
            """
            CREATE TABLE product_candidates (
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
                                     'price_unknown','excluded','error','inactive','blocked',
                                     'takedown_notified','source_dead','rate_limited',
                                     'captcha_required','skipped')),
                source_found_by TEXT DEFAULT 'manual'
                    CHECK(source_found_by IN ('manual','serper','serpapi','brave',
                                               'crawler','mcp','findprice','shopee',
                                               'findprice_shopee','feebee','biggo',
                                               'lbj','fallback','coupon')),
                raw_data TEXT DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO product_candidates
                (id, product_id, platform, title, url, seller, first_seen_at,
                 last_checked_at, last_price, status, source_found_by, raw_data)
            SELECT id, product_id, platform, title, url, seller, first_seen_at,
                   last_checked_at, last_price, status, source_found_by, raw_data
            FROM _pc_backup
            """
        )
        conn.execute("DROP TABLE _pc_backup")
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_candidates_product ON product_candidates(product_id);
            CREATE INDEX IF NOT EXISTS idx_candidates_status ON product_candidates(status);
            """
        )
        conn.execute("PRAGMA foreign_keys=ON")

    def _migrate_takedown_to_excluded(self, conn: sqlite3.Connection) -> None:
        """Treat legacy cancellation records as excluded monitoring links."""
        cur = conn.execute(
            "UPDATE product_candidates SET status='excluded' "
            "WHERE status='takedown_notified'"
        )
        if cur.rowcount:
            LOGGER.info(
                "Migrated %d legacy cancelled candidates to excluded",
                cur.rowcount,
            )

    def _migrate_coupon_source_candidates(self, conn: sqlite3.Connection) -> None:
        """Exclude existing candidates discovered through coupon sources."""
        rows = conn.execute(
            """
            SELECT id, source_found_by, raw_data, status
            FROM product_candidates
            WHERE status != 'excluded'
            """
        ).fetchall()
        migrated = 0
        for row in rows:
            try:
                raw_data = json.loads(row["raw_data"] or "{}")
            except (TypeError, ValueError):
                raw_data = {}
            if not isinstance(raw_data, dict):
                raw_data = {}

            source_values = (
                row["source_found_by"],
                raw_data.get("source"),
                raw_data.get("provider"),
                raw_data.get("source_found_by"),
            )
            if not any(is_coupon_source(value) for value in source_values):
                continue

            raw_data["exclusion_reason"] = "source_coupon"
            raw_data["coupon_source_excluded"] = True
            conn.execute(
                """
                UPDATE product_candidates
                SET status='excluded', source_found_by='coupon',
                    last_checked_at=?, raw_data=?
                WHERE id=?
                """,
                (_now_iso(), json.dumps(raw_data, ensure_ascii=False), row["id"]),
            )
            migrated += 1

        if migrated:
            LOGGER.info("Excluded %d existing coupon-source candidates", migrated)

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
        last_price: float | None = None,
        raw_data: dict[str, Any] | None = None,
    ) -> int:
        """Insert or update a candidate by URL. Returns candidate id."""
        incoming_raw_data = dict(raw_data or {}) if raw_data is not None else None
        coupon_source = is_coupon_source(source_found_by)
        if incoming_raw_data is not None:
            coupon_source = coupon_source or any(
                is_coupon_source(incoming_raw_data.get(key))
                for key in ("source", "provider", "source_found_by")
            )
        if coupon_source:
            source_found_by = "coupon"
            status = "excluded"
            raw_data = incoming_raw_data or {}
            raw_data.update(
                {
                    "exclusion_reason": "source_coupon",
                    "coupon_source_excluded": True,
                }
            )
        raw_json = json.dumps(raw_data, ensure_ascii=False) if raw_data is not None else None
        with self._cursor() as (conn, cur):
            cur.execute(
                "SELECT id FROM product_candidates WHERE url=?", (url,),
            )
            row = cur.fetchone()
            if row:
                updates = [
                    "product_id=?", "platform=?", "title=?",
                    "seller=?", "source_found_by=?",
                ]
                params: list[Any] = [product_id, platform, title, seller, source_found_by]
                if coupon_source:
                    updates.append("status=?")
                    params.append("excluded")
                if last_price is not None:
                    updates.append("last_price=?")
                    params.append(last_price)
                if raw_json is not None:
                    updates.append("raw_data=?")
                    params.append(raw_json)
                params.append(row["id"])
                cur.execute(
                    f"UPDATE product_candidates SET {', '.join(updates)} WHERE id=?",
                    params,
                )
                return row["id"]
            cur.execute(
                """INSERT INTO product_candidates
                   (product_id, platform, title, url, seller,
                    source_found_by, status)
                   VALUES (?,?,?,?,?,?,?)""",
                (product_id, platform, title, url, seller, source_found_by, status),
            )
            candidate_id = cur.lastrowid
            if last_price is not None or raw_json is not None:
                updates = []
                params = []
                if last_price is not None:
                    updates.append("last_price=?")
                    params.append(last_price)
                if raw_json is not None:
                    updates.append("raw_data=?")
                    params.append(raw_json)
                params.append(candidate_id)
                cur.execute(
                    f"UPDATE product_candidates SET {', '.join(updates)} WHERE id=?",
                    params,
                )
            return candidate_id  # type: ignore[return-value]

    def merge_candidate_raw_data(
        self,
        candidate_id: int,
        values: dict[str, Any],
    ) -> None:
        """Merge audit fields into a candidate without replacing existing evidence."""
        if not values:
            return
        with self._cursor() as (conn, cur):
            row = cur.execute(
                "SELECT raw_data FROM product_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
            if not row:
                return
            try:
                raw_data = json.loads(row["raw_data"] or "{}")
            except (TypeError, ValueError):
                raw_data = {}
            if not isinstance(raw_data, dict):
                raw_data = {}
            raw_data.update(values)
            cur.execute(
                "UPDATE product_candidates SET raw_data=? WHERE id=?",
                (json.dumps(raw_data, ensure_ascii=False), candidate_id),
            )

    def deduplicate_candidates_by_final_url(self, product_id: int | None = None) -> int:
        """Exclude later candidates that resolved to the same product page."""
        with self._cursor() as (conn, cur):
            sql = """
                SELECT id, product_id, first_seen_at, status, raw_data
                FROM product_candidates
                WHERE status != 'excluded'
            """
            params: list[Any] = []
            if product_id is not None:
                sql += " AND product_id=?"
                params.append(product_id)
            cur.execute(sql, params)
            rows = cur.fetchall()

            groups: dict[tuple[int, str], list[sqlite3.Row]] = {}
            for row in rows:
                try:
                    raw_data = json.loads(row["raw_data"] or "{}")
                except (TypeError, ValueError):
                    raw_data = {}
                if not isinstance(raw_data, dict):
                    continue
                final_url = canonical_final_url(raw_data.get("final_url", ""))
                if not final_url:
                    continue
                groups.setdefault((int(row["product_id"]), final_url), []).append(row)

            excluded_count = 0
            for (group_product_id, final_url), group in groups.items():
                if len(group) < 2:
                    continue
                ordered = sorted(
                    group,
                    key=lambda row: (row["first_seen_at"] or "9999-12-31", int(row["id"])),
                )
                keeper = ordered[0]
                for duplicate in ordered[1:]:
                    try:
                        raw_data = json.loads(duplicate["raw_data"] or "{}")
                    except (TypeError, ValueError):
                        raw_data = {}
                    if not isinstance(raw_data, dict):
                        raw_data = {}
                    raw_data.update(
                        {
                            "exclusion_reason": "duplicate_final_product_page",
                            "duplicate_final_url": final_url,
                            "duplicate_of_candidate_id": int(keeper["id"]),
                            "original_status_before_exclusion": duplicate["status"],
                        }
                    )
                    cur.execute(
                        """
                        UPDATE product_candidates
                        SET status='excluded', last_checked_at=?, raw_data=?
                        WHERE id=? AND product_id=? AND status != 'excluded'
                        """,
                        (
                            _now_iso(),
                            json.dumps(raw_data, ensure_ascii=False),
                            duplicate["id"],
                            group_product_id,
                        ),
                    )
                    excluded_count += cur.rowcount

            if excluded_count:
                LOGGER.info(
                    "Excluded %d duplicate candidates by final product URL",
                    excluded_count,
                )
            return excluded_count

    def get_active_candidates(self, product_id: int | None = None) -> list[CandidateRow]:
        """Get candidates with status in (active, normal, suspected_violation, price_unknown)."""
        with self._cursor() as (conn, cur):
            sql = """
                SELECT c.*, p.product_name, p.suggested_price, p.brand
                FROM product_candidates c
                JOIN products p ON c.product_id = p.id
                WHERE c.status IN ('active','normal','suspected_violation','price_unknown','takedown_notified')
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
        self,
        product_id: int | None = None,
        status: str | None = None,
        include_excluded: bool = True,
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
            elif not include_excluded:
                sql += " AND c.status != 'excluded'"
            sql += " ORDER BY p.product_name, c.platform"
            cur.execute(sql, params)
            return [self._to_candidate(r) for r in cur.fetchall()]

    def disable_obsolete_findprice_urls(self) -> int:
        """Mark old FindPrice 'go' URLs as source_dead."""
        with self._cursor() as (conn, cur):
            cur.execute(
                """UPDATE product_candidates
                   SET status = 'source_dead'
                   WHERE url LIKE '%findprice.com.tw/go/%'
                   AND status != 'source_dead'"""
            )
            count = cur.rowcount
            return count

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
        latest_only: bool = True,
    ) -> list[SnapshotRow]:
        with self._cursor() as (conn, cur):
            if latest_only:
                sql = """
                    WITH RankedSnapshots AS (
                        SELECT s.*, p.product_name, p.brand,
                               c.platform, c.url, c.seller, c.title,
                               c.status AS candidate_status,
                               c.source_found_by, c.first_seen_at,
                               ROW_NUMBER() OVER(PARTITION BY s.candidate_id ORDER BY s.checked_at DESC) as rn
                        FROM price_snapshots s
                        JOIN product_candidates c ON s.candidate_id = c.id
                        JOIN products p ON s.product_id = p.id
                        WHERE c.status != 'excluded'
                    )
                    SELECT * FROM RankedSnapshots
                    WHERE rn = 1
                """
            else:
                sql = """
                    SELECT s.*, p.product_name, p.brand,
                           c.platform, c.url, c.seller, c.title,
                           c.status AS candidate_status,
                           c.source_found_by, c.first_seen_at
                    FROM price_snapshots s
                    JOIN product_candidates c ON s.candidate_id = c.id
                    JOIN products p ON s.product_id = p.id
                    WHERE c.status != 'excluded'
                """
            
            params: list[Any] = []
            if date:
                sql += " AND checked_at LIKE ?"
                params.append(f"{date}%")
            if violation_only:
                sql += " AND is_violation = 1"
            sql += " ORDER BY checked_at DESC LIMIT ?"
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
            final_price=row["final_price"] if "final_price" in keys else None,
            final_price_source=row["final_price_source"] if "final_price_source" in keys else "",
            final_confidence=row["final_confidence"] if "final_confidence" in keys else 0.0,
            final_status=row["final_status"] if "final_status" in keys else "",
            decision_reason=row["decision_reason"] if "decision_reason" in keys else "",
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

    def get_api_usage_stats(self) -> dict[str, dict[str, int]]:
        """Return usage stats grouped by provider and date."""
        with self._cursor() as (conn, cur):
            cur.execute(
                """
                SELECT provider, date(used_at) as dt, COUNT(*) as cost
                FROM api_usage_logs
                GROUP BY provider, dt
                ORDER BY dt DESC, provider
                """
            )
            rows = cur.fetchall()
            
        stats: dict[str, dict[str, int]] = {}
        for row in rows:
            provider = row["provider"]
            dt = row["dt"]
            cost = row["cost"]
            if provider not in stats:
                stats[provider] = {}
            stats[provider][dt] = cost
        return stats

    # -- Global Exclusions ----------------------------------------------------

    def add_global_exclusion(self, keyword: str) -> None:
        """Add a keyword to the global exclusion list."""
        with self._cursor() as (conn, cur):
            cur.execute(
                "INSERT OR IGNORE INTO global_exclusions (keyword) VALUES (?)",
                (keyword.strip(),)
            )
            
    def remove_global_exclusion(self, exclusion_id: int) -> None:
        """Remove a keyword from the global exclusion list."""
        with self._cursor() as (conn, cur):
            cur.execute("DELETE FROM global_exclusions WHERE id = ?", (exclusion_id,))
            
    def list_global_exclusions(self) -> list[dict[str, Any]]:
        """List all global exclusions."""
        with self._cursor() as (conn, cur):
            cur.execute("SELECT id, keyword, created_at FROM global_exclusions ORDER BY created_at DESC")
            return [dict(row) for row in cur.fetchall()]
            
    def get_all_exclusion_keywords(self) -> list[str]:
        """Get just a list of all exclusion keywords for fast checking."""
        with self._cursor() as (conn, cur):
            cur.execute("SELECT keyword FROM global_exclusions")
            return [row["keyword"] for row in cur.fetchall() if (row["keyword"] or "").strip()]

    def find_matching_global_exclusion(
        self,
        candidate: CandidateRow,
        extra_values: Iterable[Any] = (),
    ) -> str:
        """Return the first exclusion keyword matching a candidate or extra evidence."""
        values = (
            candidate.title,
            candidate.product_name,
            candidate.seller,
            candidate.url,
            candidate.brand,
            candidate.raw_data,
            *tuple(extra_values),
        )
        for keyword in self.get_all_exclusion_keywords():
            if _matches_keyword(keyword, values):
                return keyword
        return ""
            
    def retroactively_exclude_candidates(self, keyword: str) -> int:
        """Set status to 'excluded' for candidates matching the keyword."""
        keyword = keyword.strip()
        if not keyword:
            return 0

        with self._cursor() as (conn, cur):
            cur.execute(
                """
                SELECT c.*, p.product_name, p.suggested_price, p.brand,
                       (SELECT raw_data FROM price_snapshots s WHERE s.candidate_id = c.id ORDER BY checked_at DESC LIMIT 1) as latest_snap_raw,
                       (SELECT error_message FROM price_snapshots s WHERE s.candidate_id = c.id ORDER BY checked_at DESC LIMIT 1) as latest_snap_err
                FROM product_candidates c
                JOIN products p ON c.product_id = p.id
                WHERE c.status != 'excluded'
                """
            )
            # Fetch all rows directly and convert to list of dicts to keep the extra columns
            rows = cur.fetchall()
            candidates = [self._to_candidate(row) for row in rows]
            matched_ids = []
            for candidate, row in zip(candidates, rows):
                if _matches_keyword(
                    keyword,
                    (
                        candidate.title,
                        candidate.product_name,
                        candidate.seller,
                        candidate.url,
                        candidate.brand,
                        candidate.raw_data,
                        row["latest_snap_raw"],
                        row["latest_snap_err"],
                    ),
                ):
                    matched_ids.append(candidate.id)
                    
            if not matched_ids:
                return 0

            placeholders = ",".join("?" for _ in matched_ids)
            cur.execute(
                f"""
                UPDATE product_candidates
                SET status = 'excluded', last_checked_at = ?
                WHERE id IN ({placeholders})
                """,
                (_now_iso(), *matched_ids),
            )
            return cur.rowcount

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
                   WHERE status IN ('active','normal','suspected_violation','price_unknown','takedown_notified')"""
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
                """SELECT COUNT(*) AS c
                   FROM (
                       SELECT s.final_status,
                              ROW_NUMBER() OVER (
                                  PARTITION BY s.candidate_id
                                  ORDER BY s.checked_at DESC
                              ) AS rn
                       FROM price_snapshots s
                       JOIN product_candidates c ON c.id = s.candidate_id
                       WHERE c.status != 'excluded'
                   ) latest
                   WHERE rn = 1 AND final_status = 'needs_review'"""
            )
            needs_review = cur.fetchone()["c"]
            cur.execute(
                "SELECT COUNT(*) AS c FROM product_candidates WHERE status='takedown_notified'"
            )
            takedown_notified = cur.fetchone()["c"]
            cur.execute(
                "SELECT COUNT(*) AS c FROM product_candidates WHERE status='excluded'"
            )
            excluded_candidates = cur.fetchone()["c"]
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
                "needs_review": needs_review,
                "takedown_notified": takedown_notified,
                "excluded_candidates": excluded_candidates,
                "last_check_time": last_check or "",
            }

    # -- Migrations (new) ---------------------------------------------------

    def _migrate_snapshot_final_price(self, conn: sqlite3.Connection) -> None:
        """Add final_price columns to price_snapshots if missing."""
        existing = {r[1] for r in conn.execute('PRAGMA table_info(price_snapshots)').fetchall()}
        new_cols = {
            'final_price': 'REAL',
            'final_price_source': "TEXT DEFAULT ''",
            'final_confidence': 'REAL DEFAULT 0.0',
            'final_status': "TEXT DEFAULT ''",
            'decision_reason': "TEXT DEFAULT ''",
        }
        for col, col_type in new_cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE price_snapshots ADD COLUMN {col} {col_type}")
                LOGGER.info("Migration: added %s to price_snapshots", col)

    def _migrate_candidate_multi_source(self, conn: sqlite3.Connection) -> None:
        """Expand product_candidates status and source_found_by CHECK constraints
        to include multi-source values (source_dead, rate_limited, feebee, etc.)."""
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='product_candidates'"
        ).fetchone()
        if not row:
            return
        schema_sql = row["sql"] or ""
        # Already migrated only when all current multi-source values exist.
        if all(value in schema_sql for value in ("source_dead", "feebee", "biggo", "lbj", "fallback", "coupon")):
            return

        LOGGER.info("Migration: expanding product_candidates for multi-source support")
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("CREATE TEMP TABLE _pc_ms_backup AS SELECT * FROM product_candidates")
        conn.execute("DROP TABLE product_candidates")
        conn.execute(
            """
            CREATE TABLE product_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL REFERENCES products(id),
                platform TEXT NOT NULL,
                title TEXT DEFAULT '',
                url TEXT NOT NULL,
                seller TEXT DEFAULT '',
                first_seen_at TEXT DEFAULT (datetime('now')),
                last_checked_at TEXT,
                last_price REAL,
                status TEXT DEFAULT 'active'
                    CHECK(status IN ('active','normal','suspected_violation',
                                     'price_unknown','excluded','error','inactive','blocked',
                                     'takedown_notified','source_dead','rate_limited',
                                     'captcha_required','skipped')),
                source_found_by TEXT DEFAULT 'manual'
                    CHECK(source_found_by IN ('manual','serper','serpapi','brave',
                                               'crawler','mcp','findprice','shopee',
                                               'findprice_shopee','feebee','biggo',
                                               'lbj','fallback','coupon')),
                raw_data TEXT DEFAULT '{}',
                UNIQUE(product_id, url)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO product_candidates
                (id, product_id, platform, title, url, seller, first_seen_at,
                 last_checked_at, last_price, status, source_found_by, raw_data)
            SELECT id, product_id, platform, title, url, seller, first_seen_at,
                   last_checked_at, last_price, status, source_found_by, raw_data
            FROM _pc_ms_backup
            """
        )
        conn.execute("DROP TABLE _pc_ms_backup")
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_candidates_product ON product_candidates(product_id);
            CREATE INDEX IF NOT EXISTS idx_candidates_status ON product_candidates(status);
            """
        )
        conn.execute("PRAGMA foreign_keys=ON")

    # -- Price Observations -------------------------------------------------

    def insert_observation(
        self,
        product_id: int,
        candidate_id: int | None,
        platform: str,
        source: str,
        url: str = "",
        title: str = "",
        seller: str = "",
        price: float | None = None,
        currency: str = "TWD",
        match_score: int = 0,
        confidence: float = 0.0,
        status: str = "success",
        error_message: str = "",
        raw_data: dict[str, Any] | None = None,
    ) -> int:
        with self._cursor() as (conn, cur):
            cur.execute(
                """INSERT INTO price_observations
                   (product_id, candidate_id, platform, source, url, title,
                    seller, price, currency, match_score, confidence,
                    status, error_message, raw_data)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (product_id, candidate_id, platform, source, url, title,
                 seller, price, currency, match_score, confidence,
                 status, error_message,
                 json.dumps(raw_data or {}, ensure_ascii=False)),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def get_observations(
        self,
        product_id: int | None = None,
        since: str | None = None,
        limit: int = 500,
    ) -> list[ObservationRow]:
        with self._cursor() as (conn, cur):
            sql = """
                SELECT o.*, p.product_name, p.suggested_price
                FROM price_observations o
                JOIN products p ON o.product_id = p.id
                WHERE 1=1
            """
            params: list[Any] = []
            if product_id is not None:
                sql += " AND o.product_id = ?"
                params.append(product_id)
            if since:
                sql += " AND o.observed_at >= ?"
                params.append(since)
            sql += " ORDER BY o.observed_at DESC LIMIT ?"
            params.append(limit)
            cur.execute(sql, params)
            return [self._to_observation(r) for r in cur.fetchall()]

    def get_observations_for_decision(
        self, product_id: int, run_time: str | None = None
    ) -> list[ObservationRow]:
        """Get the most recent observations for final price decision."""
        since = run_time or _now_iso()[:10]  # today
        return self.get_observations(product_id=product_id, since=since, limit=100)

    @staticmethod
    def _to_observation(row: sqlite3.Row) -> ObservationRow:
        keys = row.keys()
        return ObservationRow(
            id=row["id"],
            product_id=row["product_id"],
            candidate_id=row["candidate_id"],
            platform=row["platform"] or "",
            source=row["source"] or "direct_html",
            url=row["url"] or "",
            title=row["title"] or "",
            seller=row["seller"] or "",
            price=row["price"],
            currency=row["currency"] or "TWD",
            match_score=row["match_score"] or 0,
            confidence=row["confidence"] or 0.0,
            status=row["status"] or "success",
            error_message=row["error_message"] or "",
            raw_data=row["raw_data"] or "{}",
            observed_at=row["observed_at"] or "",
            product_name=row["product_name"] if "product_name" in keys else "",
            suggested_price=row["suggested_price"] if "suggested_price" in keys else None,
        )

    def update_snapshot_final_price(
        self,
        snapshot_id: int,
        final_price: float | None,
        final_price_source: str,
        final_confidence: float,
        final_status: str,
        decision_reason: str,
    ) -> None:
        with self._cursor() as (conn, cur):
            cur.execute(
                """UPDATE price_snapshots
                   SET final_price=?, final_price_source=?,
                       final_confidence=?, final_status=?, decision_reason=?
                   WHERE id=?""",
                (final_price, final_price_source, final_confidence,
                 final_status, decision_reason, snapshot_id),
            )

    # -- Source Health -------------------------------------------------------

    def upsert_source_health(
        self,
        source_name: str,
        platform: str,
        success_count_24h: int = 0,
        error_count_24h: int = 0,
        blocked_count_24h: int = 0,
        rate_limited_count_24h: int = 0,
        last_success_at: str = "",
        cooldown_until: str = "",
        enabled: bool = True,
    ) -> None:
        total = success_count_24h + error_count_24h + blocked_count_24h + rate_limited_count_24h
        rate = round(success_count_24h / total, 4) if total > 0 else 0.0
        with self._cursor() as (conn, cur):
            cur.execute(
                """INSERT INTO source_health
                   (source_name, platform, success_count_24h, error_count_24h,
                    blocked_count_24h, rate_limited_count_24h, success_rate_24h,
                    last_success_at, cooldown_until, enabled, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_name, platform) DO UPDATE SET
                    success_count_24h=excluded.success_count_24h,
                    error_count_24h=excluded.error_count_24h,
                    blocked_count_24h=excluded.blocked_count_24h,
                    rate_limited_count_24h=excluded.rate_limited_count_24h,
                    success_rate_24h=excluded.success_rate_24h,
                    last_success_at=CASE WHEN excluded.last_success_at != '' THEN excluded.last_success_at ELSE source_health.last_success_at END,
                    cooldown_until=excluded.cooldown_until,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at""",
                (source_name, platform, success_count_24h, error_count_24h,
                 blocked_count_24h, rate_limited_count_24h, rate,
                 last_success_at, cooldown_until, enabled, _now_iso()),
            )

    def get_source_health(self) -> list[SourceHealthRow]:
        with self._cursor() as (conn, cur):
            cur.execute("SELECT * FROM source_health ORDER BY source_name, platform")
            return [
                SourceHealthRow(
                    id=r["id"],
                    source_name=r["source_name"],
                    platform=r["platform"] or "",
                    success_count_24h=r["success_count_24h"] or 0,
                    error_count_24h=r["error_count_24h"] or 0,
                    blocked_count_24h=r["blocked_count_24h"] or 0,
                    rate_limited_count_24h=r["rate_limited_count_24h"] or 0,
                    success_rate_24h=r["success_rate_24h"] or 0.0,
                    last_success_at=r["last_success_at"] or "",
                    cooldown_until=r["cooldown_until"] or "",
                    enabled=bool(r["enabled"]),
                    updated_at=r["updated_at"] or "",
                )
                for r in cur.fetchall()
            ]

    def disable_findprice_candidates(self) -> int:
        """Mark all findprice.com.tw/go/ candidates as source_dead."""
        with self._cursor() as (conn, cur):
            cur.execute(
                """UPDATE product_candidates
                   SET status='source_dead', last_checked_at=?
                   WHERE url LIKE '%findprice.com.tw/go/%'
                     AND status NOT IN ('excluded','source_dead')""",
                (_now_iso(),),
            )
            count = cur.rowcount
            LOGGER.info("Disabled %d FindPrice candidates as source_dead", count)
            return count
