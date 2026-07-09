"""Fix specific mismatched candidates - migrate them to the correct product."""
import sqlite3

conn = sqlite3.connect("data/price_monitor.db")
conn.execute("PRAGMA foreign_keys = ON")
cur = conn.cursor()

# All candidates for 活力源 products (id 89, 90) should go to 新究極活力源 (id 91)
# because the search results all say "新究極活力源"
migrations = [
    # (candidate_id, from_product, to_product_id=91 新究極活力源)
    (701, 89, 91),  # 【AFC】新究極活力源90粒 -> should be 新究極活力源
    (744, 89, 91),  # AFC 新究極活力源膠囊食品90粒 -> should be 新究極活力源
    (791, 89, 91),  # AFC 新究極活力源膠囊90粒 -> should be 新究極活力源
    (745, 90, 91),  # 【日本AFC】新究極活力源膠囊90粒 -> should be 新究極活力源
]

# Also migrate inactive product candidates to their correct new products
# 煥妍SPF胎盤素(id=113) -> AFC_胎盤素膠囊食品(id=33)
# 菁鑽珊瑚鈣(id=120) -> 菁鑽新珊瑚鈣S錠狀食品(id=55)
inactive_migrations = [
    (703, 113, 33),   # 煥妍SPF胎盤素 -> AFC_胎盤素膠囊食品
    (706, 120, 55),   # 菁鑽珊瑚鈣 -> 菁鑽新珊瑚鈣S錠狀食品
    (707, 120, 55),   # 菁鑽珊瑚鈣 -> 菁鑽新珊瑚鈣S錠狀食品
]

all_migrations = migrations + inactive_migrations

for cand_id, from_id, to_id in all_migrations:
    # Check candidate exists
    cur.execute("SELECT title FROM product_candidates WHERE id = ?", (cand_id,))
    row = cur.fetchone()
    if not row:
        print(f"  Candidate #{cand_id} not found, skipping")
        continue
    
    # Check target product
    cur.execute("SELECT product_name, suggested_price FROM products WHERE id = ?", (to_id,))
    target = cur.fetchone()
    
    # Check for duplicate URL
    cur.execute("SELECT url FROM product_candidates WHERE id = ?", (cand_id,))
    url = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM product_candidates WHERE product_id = ? AND url = ?", (to_id, url))
    if cur.fetchone()[0] > 0:
        # Duplicate, just delete this one
        cur.execute("DELETE FROM price_snapshots WHERE candidate_id = ?", (cand_id,))
        cur.execute("DELETE FROM product_candidates WHERE id = ?", (cand_id,))
        print(f"  Deduped: #{cand_id} (already exists on prod #{to_id})")
    else:
        cur.execute("UPDATE product_candidates SET product_id = ? WHERE id = ?", (to_id, cand_id))
        print(f"  Migrated: #{cand_id} from prod #{from_id} -> #{to_id} ({target[0]} ${target[1]})")

conn.commit()

# Verify
print("\n=== Verification ===")
cur.execute("""
    SELECT pc.id, pc.product_id, p.product_name, p.suggested_price, pc.title, pc.status
    FROM product_candidates pc
    JOIN products p ON pc.product_id = p.id
    WHERE p.product_name LIKE '%活力源%'
""")
for r in cur.fetchall():
    print(f"  cand #{r[0]} -> [{r[1]}] {r[2]} ${r[3]}  status={r[5]}")
    print(f"    {r[4][:60]}")

# Check no more orphans on inactive products
cur.execute("""
    SELECT COUNT(*) FROM product_candidates pc
    JOIN products p ON pc.product_id = p.id
    WHERE p.is_active = 0
""")
print(f"\nCandidates still on inactive products: {cur.fetchone()[0]}")

conn.close()
