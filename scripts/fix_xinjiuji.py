"""Fix mismatched 新究極 vs 究極新 candidates."""
import sqlite3

conn = sqlite3.connect("data/price_monitor.db")
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys = ON")
cur = conn.cursor()

# Get all 新究極 products for matching
cur.execute("""
    SELECT id, product_name, suggested_price FROM products 
    WHERE product_name LIKE '新究極%' AND is_active = 1
""")
xinjiuji_products = {r["product_name"]: dict(r) for r in cur.fetchall()}
print("新究極 products:")
for name, p in xinjiuji_products.items():
    print(f"  [{p['id']}] {name} ${p['suggested_price']}")

# Fix specific mismatches
fixes = []

# #788: "AFC 新究極命源10粒/盒(輔酶Q10)" -> should be 新究極命源 or 新究極命源10份裝
# It's a 10粒 pack, could be the 10份裝 version
cur.execute("SELECT id, product_name FROM products WHERE product_name = '新究極命源10份裝' AND is_active = 1")
row = cur.fetchone()
if row:
    fixes.append((788, row["id"], row["product_name"]))
else:
    # Fallback to regular 新究極命源
    cur.execute("SELECT id, product_name FROM products WHERE product_name = '新究極命源' AND is_active = 1")
    row = cur.fetchone()
    if row:
        fixes.append((788, row["id"], row["product_name"]))

# #789: "AFC 新究極命源60粒/瓶" -> should be 新究極命源 (60粒 = regular)
cur.execute("SELECT id, product_name FROM products WHERE product_name = '新究極命源' AND is_active = 1")
row = cur.fetchone()
if row:
    fixes.append((789, row["id"], row["product_name"]))

# #790: "AFC 新究極活力源90粒" -> should be 新究極活力源
cur.execute("SELECT id, product_name FROM products WHERE product_name = '新究極活力源' AND is_active = 1")
row = cur.fetchone()
if row:
    fixes.append((790, row["id"], row["product_name"]))

# #702: "AFC 新究極潤節60粒" -> should be 新究極潤節
cur.execute("SELECT id, product_name FROM products WHERE product_name = '新究極潤節' AND is_active = 1")
row = cur.fetchone()
if row:
    fixes.append((702, row["id"], row["product_name"]))

print(f"\n=== Fixing {len(fixes)} candidates ===")
for cand_id, new_prod_id, new_prod_name in fixes:
    # Check current
    cur.execute("""
        SELECT pc.product_id, p.product_name, p.suggested_price, pc.title
        FROM product_candidates pc
        JOIN products p ON pc.product_id = p.id
        WHERE pc.id = ?
    """, (cand_id,))
    old = cur.fetchone()
    if not old:
        print(f"  #{cand_id}: NOT FOUND, skipping")
        continue
    
    # Check for duplicate URL
    cur.execute("SELECT url FROM product_candidates WHERE id = ?", (cand_id,))
    url = cur.fetchone()["url"]
    cur.execute("SELECT COUNT(*) FROM product_candidates WHERE product_id = ? AND url = ?", (new_prod_id, url))
    if cur.fetchone()[0] > 0:
        # Duplicate, delete instead
        cur.execute("DELETE FROM price_snapshots WHERE candidate_id = ?", (cand_id,))
        cur.execute("DELETE FROM product_candidates WHERE id = ?", (cand_id,))
        print(f"  #{cand_id}: DEDUPED (URL already on new product)")
    else:
        cur.execute("UPDATE product_candidates SET product_id = ? WHERE id = ?", (new_prod_id, cand_id))
        print(f"  #{cand_id}: {old['product_name']} ${old['suggested_price']} -> {new_prod_name}")
        print(f"    title: {old['title'][:60]}")

conn.commit()

# Recalculate affected snapshots
print("\n=== Recalculating snapshots ===")
for cand_id, new_prod_id, _ in fixes:
    cur.execute("SELECT id FROM product_candidates WHERE id = ?", (cand_id,))
    if not cur.fetchone():
        continue
    cur.execute("SELECT suggested_price FROM products WHERE id = ?", (new_prod_id,))
    suggested = cur.fetchone()["suggested_price"]
    cur.execute("SELECT id, price FROM price_snapshots WHERE candidate_id = ? AND price IS NOT NULL", (cand_id,))
    for snap in cur.fetchall():
        diff = snap["price"] - suggested
        cur.execute("UPDATE price_snapshots SET price_diff = ? WHERE id = ?", (diff, snap["id"]))
        new_status = "suspected_violation" if diff < 0 else "normal"
        cur.execute("UPDATE product_candidates SET status = ? WHERE id = ?", (new_status, cand_id))
        print(f"  #{cand_id}: price={snap['price']} suggested={suggested} diff={diff} -> {new_status}")

conn.commit()
conn.close()
print("\nDone!")
