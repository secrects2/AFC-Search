import sqlite3

conn = sqlite3.connect("data/price_monitor.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Find all products with "活力源" in the name
print("=== Products with 活力源 ===")
cur.execute("SELECT id, product_name, suggested_price, is_active FROM products WHERE product_name LIKE '%活力源%'")
for r in cur.fetchall():
    print(f"  id={r['id']}, name={r['product_name']}, price={r['suggested_price']}, active={r['is_active']}")

# Find candidates linked to those products
print("\n=== Candidates for 活力源 products ===")
cur.execute("""
    SELECT pc.id, pc.product_id, p.product_name, p.suggested_price, pc.title, pc.url, pc.status
    FROM product_candidates pc 
    JOIN products p ON pc.product_id = p.id
    WHERE p.product_name LIKE '%活力源%'
""")
for r in cur.fetchall():
    print(f"  cand_id={r['id']}, prod_id={r['product_id']}")
    print(f"    DB product: {r['product_name']} (${r['suggested_price']})")
    print(f"    Found title: {r['title']}")
    print(f"    URL: {r['url'][:90]}")
    print(f"    Status: {r['status']}")
    print()

# Now let's look at ALL old vs new duplicates
print("\n=== Looking for old-style products (from before CSV update) ===")
cur.execute("SELECT id, product_name, suggested_price FROM products ORDER BY product_name")
all_prods = cur.fetchall()

# The old CSV had different naming: "究極新活力源膠囊食品" vs new CSV "新究極活力源"
# Let's find products NOT in the new CSV that look like duplicates
new_csv_names = set()
with open(r"C:\Users\secre\Downloads\AFC商品.csv", encoding="utf-8-sig") as f:
    import csv
    for row in csv.reader(f):
        if len(row) >= 2:
            new_csv_names.add(row[1].strip())

print(f"Products in DB but NOT in new CSV:")
old_only = []
for r in all_prods:
    if r["product_name"] not in new_csv_names:
        old_only.append(dict(r))
        # Check if this product has any candidates
        cur.execute("SELECT COUNT(*) FROM product_candidates WHERE product_id = ?", (r["id"],))
        cand_count = cur.fetchone()[0]
        print(f"  id={r['id']}, name={r['product_name']}, price={r['suggested_price']}, candidates={cand_count}")

print(f"\nTotal old-only products: {len(old_only)}")
conn.close()
