"""Check if candidate migrations were correct based on 究極新 vs 新究極 distinction."""
import sqlite3

conn = sqlite3.connect("data/price_monitor.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Show all 究極-related products
print("=== All 究極-related products ===")
cur.execute("""
    SELECT id, product_name, suggested_price, is_active 
    FROM products 
    WHERE product_name LIKE '%究極%'
    ORDER BY product_name
""")
for r in cur.fetchall():
    cur2 = conn.cursor()
    cur2.execute("SELECT COUNT(*) FROM product_candidates WHERE product_id = ?", (r["id"],))
    cnt = cur2.fetchone()[0]
    active = "✅" if r["is_active"] else "❌"
    print(f"  {active} [{r['id']}] {r['product_name']} ${r['suggested_price']} ({cnt} candidates)")

# Show all candidates for 究極 products
print("\n=== Candidates for active 究極 products ===")
cur.execute("""
    SELECT pc.id, pc.product_id, p.product_name, p.suggested_price, pc.title, pc.status
    FROM product_candidates pc
    JOIN products p ON pc.product_id = p.id
    WHERE p.product_name LIKE '%究極%' AND p.is_active = 1
    ORDER BY p.product_name, pc.id
""")
for r in cur.fetchall():
    pid = r["product_id"]
    pname = r["product_name"]
    pprice = r["suggested_price"]
    title = (r["title"] or "")[:70]
    status = r["status"]
    cid = r["id"]
    
    # Check if the candidate title matches the product
    # 新究極 in title should go to 新究極 product
    # 究極新 in title should go to 究極新 product
    title_has_xinjiuji = "新究極" in title  # New series
    title_has_jiujixin = "究極新" in title  # Old series
    prod_is_xinjiuji = pname.startswith("新究極")
    prod_is_jiujixin = "究極新" in pname or (pname.startswith("究極") and not pname.startswith("新究極"))
    
    match_ok = ""
    if title_has_xinjiuji and prod_is_jiujixin:
        match_ok = " ⚠️ MISMATCH! title=新究極 but product=究極新"
    elif title_has_jiujixin and prod_is_xinjiuji:
        match_ok = " ⚠️ MISMATCH! title=究極新 but product=新究極"
    
    print(f"  #{cid} -> [{pid}] {pname} ${pprice} | {status}")
    print(f"    title: {title}{match_ok}")

conn.close()
