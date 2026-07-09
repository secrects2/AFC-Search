"""Clean up non-AFC candidate links from the database."""
import sqlite3
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

db_path = root / "data" / "price_monitor.db"
conn = sqlite3.connect(str(db_path))
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys = ON")
cur = conn.cursor()

# Brand check keywords - only keep candidates whose title mentions these
BRAND_KEYWORDS = [
    "afc", "genki", "frura", "華舞", "爽快柑", "髮優",
    "究極", "菁鑽", "子供", "宇勝", "潤煌",
]

cur.execute("""
    SELECT pc.id, pc.title, p.product_name
    FROM product_candidates pc
    JOIN products p ON pc.product_id = p.id
""")
rows = cur.fetchall()

to_delete = []
for r in rows:
    title = (r["title"] or "").lower()
    if not title:
        continue
    if not any(b in title for b in BRAND_KEYWORDS):
        to_delete.append(r["id"])

print(f"Total candidates: {len(rows)}")
print(f"Non-AFC to delete: {len(to_delete)}")

if to_delete:
    # Delete related snapshots first
    placeholders = ",".join("?" * len(to_delete))
    cur.execute(
        f"DELETE FROM price_snapshots WHERE candidate_id IN ({placeholders})",
        to_delete,
    )
    snap_count = cur.rowcount
    # Delete candidates
    cur.execute(
        f"DELETE FROM product_candidates WHERE id IN ({placeholders})",
        to_delete,
    )
    cand_count = cur.rowcount
    conn.commit()
    print(f"Deleted {cand_count} candidates and {snap_count} snapshots.")

# Count remaining
cur.execute("SELECT COUNT(*) FROM product_candidates")
print(f"Remaining candidates: {cur.fetchone()[0]}")
conn.close()
