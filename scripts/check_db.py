"""Quick DB inspection script."""
from pathlib import Path
from src.database import Database

db = Database(Path("data/price_monitor.db"))

# Candidates
cs = db.list_candidates()
print(f"=== Candidates (product_candidates) ===")
print(f"Total: {len(cs)}")

stats = {}
for c in cs:
    stats[c.status] = stats.get(c.status, 0) + 1
print(f"By status: {stats}")

source_stats = {}
for c in cs:
    source_stats[c.source_found_by] = source_stats.get(c.source_found_by, 0) + 1
print(f"By source: {source_stats}")

platform_stats = {}
for c in cs:
    platform_stats[c.platform] = platform_stats.get(c.platform, 0) + 1
print(f"By platform: {platform_stats}")

# Snapshots
snaps = db.get_snapshots(limit=9999)
print(f"\n=== Price Snapshots ===")
print(f"Total: {len(snaps)}")

# Recent candidates
print(f"\n=== Last 10 candidates added ===")
with db._cursor() as (conn, cur):
    cur.execute("""
        SELECT c.id, c.platform, c.status, c.source_found_by, c.first_seen_at,
               c.url, p.product_name
        FROM product_candidates c
        JOIN products p ON c.product_id = p.id
        ORDER BY c.id DESC LIMIT 10
    """)
    for row in cur.fetchall():
        print(f"  #{row['id']} [{row['status']}] {row['platform']} "
              f"| {row['product_name'][:25]} "
              f"| {row['source_found_by']} "
              f"| {row['url'][:70]}")
