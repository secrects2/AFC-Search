"""Deactivate products NOT in the latest CSV."""
import csv
import sqlite3

csv_path = r"C:\Users\secre\Downloads\AFC商品.csv"
db_path = "data/price_monitor.db"

# Load CSV names
csv_names = set()
with open(csv_path, encoding="utf-8-sig") as f:
    for row in csv.reader(f):
        if len(row) >= 2 and row[1].strip():
            csv_names.add(row[1].strip())

print(f"CSV products: {len(csv_names)}")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Get active products not in CSV
cur.execute("SELECT id, product_name FROM products WHERE is_active = 1")
all_active = cur.fetchall()

to_deactivate = [(pid, name) for pid, name in all_active if name not in csv_names]
print(f"Active products not in CSV: {len(to_deactivate)}")

for pid, name in to_deactivate:
    cur.execute("UPDATE products SET is_active = 0 WHERE id = ?", (pid,))
    print(f"  Deactivated: [{pid}] {name}")

conn.commit()

# Summary
cur.execute("SELECT COUNT(*) FROM products WHERE is_active = 1")
print(f"\nActive products remaining: {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(*) FROM products WHERE is_active = 0")
print(f"Inactive products: {cur.fetchone()[0]}")
conn.close()
