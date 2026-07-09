import csv
import sys
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from src.database import Database

csv_path = Path(r"C:\Users\secre\Downloads\AFC商品.csv")
rows = list(csv.reader(csv_path.read_text(encoding="utf-8-sig").splitlines()))

csv_products = {}
for r in rows:
    if len(r) >= 2:
        price = float(r[0]) if r[0] else 0
        name = r[1].strip()
        if name:
            csv_products[name] = price

db = Database(root / "data" / "price_monitor.db")
db_products = db.list_products(active_only=False)
db_names = {p.product_name: p for p in db_products}

# New products
new_names = set(csv_products.keys()) - set(db_names.keys())
# Removed from CSV
removed_names = set(db_names.keys()) - set(csv_products.keys())
# Price changes
price_changes = []
for name, price in csv_products.items():
    if name in db_names:
        old_price = db_names[name].suggested_price
        if old_price and abs(old_price - price) > 0.01:
            price_changes.append((name, old_price, price))

print(f"CSV: {len(csv_products)} products")
print(f"DB: {len(db_names)} products")
print(f"New: {len(new_names)}")
print(f"Removed from CSV: {len(removed_names)}")
print(f"Price changes: {len(price_changes)}")

print("\n=== NEW PRODUCTS ===")
for n in sorted(new_names):
    print(f"  + {n} (${csv_products[n]})")

print("\n=== REMOVED FROM CSV ===")
for n in sorted(removed_names):
    print(f"  - {n}")

print("\n=== PRICE CHANGES ===")
for name, old, new in price_changes:
    print(f"  {name}: ${old} -> ${new}")

# Do the import
print("\n=== IMPORTING ===")
imported = 0
updated = 0
for name, price in csv_products.items():
    if name in db_names:
        old = db_names[name]
        if old.suggested_price and abs(old.suggested_price - price) > 0.01:
            db.upsert_product(
                product_name=name,
                suggested_price=price,
                brand=old.brand,
                keywords=old.keywords,
                exclude_keywords=old.exclude_keywords,
                priority=old.priority,
                is_active=old.is_active,
                official_image_url=old.official_image_url,
                official_image_path=old.official_image_path,
                official_image_hash=old.official_image_hash,
            )
            updated += 1
    else:
        db.upsert_product(
            product_name=name,
            suggested_price=price,
            is_active=True,
        )
        imported += 1

print(f"New products imported: {imported}")
print(f"Price updates: {updated}")
print(f"Total DB products now: {len(db.list_products(active_only=False))}")
