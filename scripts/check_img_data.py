import sqlite3
conn = sqlite3.connect("data/price_monitor.db")
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM products WHERE official_image_url != ''")
with_url = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM products WHERE official_image_path != ''")
with_path = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM products")
total = cur.fetchone()[0]
print(f"Total: {total}, with image URL: {with_url}, with image path: {with_path}")

cur.execute("SELECT id, product_name, official_image_url, official_image_path FROM products WHERE official_image_url != '' LIMIT 5")
for r in cur.fetchall():
    print(f"  [{r[0]}] {r[1]}")
    print(f"    URL: {r[2][:100]}")
    print(f"    Path: {r[3]}")
conn.close()
