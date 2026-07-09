import sqlite3
conn = sqlite3.connect('data/price_monitor.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("""
    SELECT pc.id, pc.title, pc.url, pc.status, p.product_name 
    FROM product_candidates pc 
    JOIN products p ON pc.product_id = p.id
""")
rows = cur.fetchall()

# Brand check keywords
brand_kws = ["afc", "genki", "frura", "華舞", "爽快柑", "髮優", "究極", "菁鑽", "子供", "宇勝"]

non_afc = []
for r in rows:
    title = (r["title"] or "").lower()
    if not title:
        continue
    if not any(b in title for b in brand_kws):
        non_afc.append(dict(r))

print(f"Total candidates: {len(rows)}")
print(f"Non-AFC candidates (by title): {len(non_afc)}")
for r in non_afc:
    pid = r["id"]
    pname = r["product_name"]
    status = r["status"]
    title = r["title"][:50]
    print(f"  #{pid} [{pname}] {status} -> {title}")
