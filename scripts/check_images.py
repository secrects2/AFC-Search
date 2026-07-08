import sqlite3

conn = sqlite3.connect("data/price_monitor.db")
c = conn.cursor()

c.execute("PRAGMA table_info(products)")
print("Products columns:")
for r in c.fetchall():
    print(f"  {r[1]} ({r[2]})")

c.execute("SELECT id, product_name, official_image_url FROM products LIMIT 5")
print("\nSample products:")
for r in c.fetchall():
    img = r[2] or "NONE"
    print(f"  #{r[0]} {r[1][:35]} | img: {img[:60]}")

c.execute("SELECT COUNT(*) FROM products WHERE official_image_url IS NOT NULL AND official_image_url != ''")
print(f"\nProducts with images: {c.fetchone()[0]}")

c.execute("SELECT COUNT(*) FROM products")
print(f"Total products: {c.fetchone()[0]}")
