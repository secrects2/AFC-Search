import sqlite3
conn = sqlite3.connect("data/price_monitor.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=== Active 活力源 products ===")
cur.execute("SELECT id, product_name, suggested_price FROM products WHERE product_name LIKE '%活力源%' AND is_active = 1")
for r in cur.fetchall():
    cur2 = conn.cursor()
    cur2.execute("SELECT COUNT(*) FROM product_candidates WHERE product_id = ?", (r["id"],))
    cnt = cur2.fetchone()[0]
    pid = r["id"]
    name = r["product_name"]
    price = r["suggested_price"]
    print(f"  [{pid}] {name} ${price} ({cnt} candidates)")

print("\n=== All 活力源 candidates ===")
cur.execute("""
    SELECT pc.id, pc.product_id, p.product_name, p.suggested_price, pc.title, pc.status
    FROM product_candidates pc
    JOIN products p ON pc.product_id = p.id
    WHERE p.product_name LIKE '%活力源%'
""")
for r in cur.fetchall():
    cid = r["id"]
    pid = r["product_id"]
    pname = r["product_name"]
    pprice = r["suggested_price"]
    title = r["title"][:60] if r["title"] else ""
    status = r["status"]
    print(f"  cand #{cid} -> prod [{pid}] {pname} ${pprice}")
    print(f"    title: {title}  status: {status}")

# Also check: are there candidates still on inactive products?
print("\n=== Candidates on INACTIVE products ===")
cur.execute("""
    SELECT pc.id, pc.product_id, p.product_name, p.suggested_price, pc.title
    FROM product_candidates pc
    JOIN products p ON pc.product_id = p.id
    WHERE p.is_active = 0
""")
rows = cur.fetchall()
print(f"Total: {len(rows)}")
for r in rows:
    cid = r["id"]
    pid = r["product_id"]
    pname = r["product_name"]
    title = r["title"][:50] if r["title"] else ""
    print(f"  cand #{cid} -> [{pid}] {pname} -> {title}")

conn.close()
