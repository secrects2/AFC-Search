import csv
import sqlite3

csv_path = r"C:\Users\secre\Downloads\AFC商品.csv"
db_path = "data/price_monitor.db"

csv_names = {}
with open(csv_path, encoding="utf-8-sig") as f:
    for row in csv.reader(f):
        if len(row) >= 2 and row[1].strip():
            csv_names[row[1].strip()] = float(row[0]) if row[0] else 0

print(f"CSV has {len(csv_names)} products:")
for name, price in csv_names.items():
    print(f"  {name} (${price})")

conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT product_name, is_active FROM products")
db_names = {r[0]: r[1] for r in cur.fetchall()}

print("\n=== CSV products NOT active in DB ===")
for name in csv_names:
    if name not in db_names:
        print(f"  MISSING: {name}")
    elif not db_names[name]:
        print(f"  INACTIVE: {name}")

print("\n=== Active DB products ===")
cur.execute("SELECT id, product_name, suggested_price FROM products WHERE is_active = 1 ORDER BY id")
for r in cur.fetchall():
    print(f"  [{r[0]}] {r[1]} ${r[2]}")
conn.close()
