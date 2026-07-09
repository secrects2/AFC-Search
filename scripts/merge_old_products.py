"""
Merge old duplicate products into new CSV products.
- Migrate candidates from old product to the matching new product
- Deactivate old products
"""
import csv
import sqlite3
import sys
from pathlib import Path
from difflib import SequenceMatcher

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

db_path = root / "data" / "price_monitor.db"
conn = sqlite3.connect(str(db_path))
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA foreign_keys = ON")
cur = conn.cursor()

# Load new CSV product names
new_csv = {}
with open(r"C:\Users\secre\Downloads\AFC商品.csv", encoding="utf-8-sig") as f:
    for row in csv.reader(f):
        if len(row) >= 2 and row[1].strip():
            new_csv[row[1].strip()] = float(row[0]) if row[0] else 0

# Get all DB products
cur.execute("SELECT id, product_name, suggested_price FROM products ORDER BY id")
all_products = [dict(r) for r in cur.fetchall()]

# Identify old products (not in new CSV)
new_names = set(new_csv.keys())
old_products = [p for p in all_products if p["product_name"] not in new_names]
new_products = [p for p in all_products if p["product_name"] in new_names]

print(f"New CSV products in DB: {len(new_products)}")
print(f"Old products to process: {len(old_products)}")

def normalize(name):
    """Normalize product name for matching."""
    import re, unicodedata
    text = unicodedata.normalize("NFKC", name or "").lower()
    text = re.sub(r"[【】〖〗\[\]（）()｜|]", "", text)
    text = re.sub(r"afc[_ ]?", "", text)
    text = re.sub(r"日本原裝", "", text)
    text = re.sub(r"親子部落客首選.*$", "", text)
    text = re.sub(r"日本人氣.*$", "", text)
    text = re.sub(r"\d+\s*(粒|錠|顆|包|盒|瓶|日份)", "", text)
    text = re.sub(r"[/／]", "", text)
    text = re.sub(r"\s+", "", text)
    return text

# Build a mapping: old_product_id -> best matching new_product_id
migration_map = {}
for old in old_products:
    old_norm = normalize(old["product_name"])
    best_match = None
    best_score = 0
    for new in new_products:
        new_norm = normalize(new["product_name"])
        # Check containment first
        if old_norm in new_norm or new_norm in old_norm:
            score = 95
        else:
            score = int(SequenceMatcher(None, old_norm, new_norm).ratio() * 100)
        if score > best_score:
            best_score = score
            best_match = new
    
    if best_match and best_score >= 60:
        migration_map[old["id"]] = best_match
        print(f"\n  OLD: [{old['id']}] {old['product_name']} (${old['suggested_price']})")
        print(f"  NEW: [{best_match['id']}] {best_match['product_name']} (${best_match['suggested_price']})")
        print(f"  Score: {best_score}")
    else:
        print(f"\n  NO MATCH: [{old['id']}] {old['product_name']} (best={best_score})")

# Now migrate candidates
print("\n\n=== MIGRATING CANDIDATES ===")
total_migrated = 0
total_deduped = 0

for old_id, new_prod in migration_map.items():
    new_id = new_prod["id"]
    
    # Get candidates from old product
    cur.execute("SELECT id, url FROM product_candidates WHERE product_id = ?", (old_id,))
    old_candidates = cur.fetchall()
    
    if not old_candidates:
        continue
    
    # Get existing URLs for new product to avoid duplicates
    cur.execute("SELECT url FROM product_candidates WHERE product_id = ?", (new_id,))
    existing_urls = {r["url"] for r in cur.fetchall()}
    
    for cand in old_candidates:
        if cand["url"] in existing_urls:
            # Delete duplicate
            cur.execute("DELETE FROM price_snapshots WHERE candidate_id = ?", (cand["id"],))
            cur.execute("DELETE FROM product_candidates WHERE id = ?", (cand["id"],))
            total_deduped += 1
            print(f"  Deduped: cand #{cand['id']} (URL already exists on new product)")
        else:
            # Migrate to new product
            cur.execute("UPDATE product_candidates SET product_id = ? WHERE id = ?", (new_id, cand["id"]))
            total_migrated += 1
            print(f"  Migrated: cand #{cand['id']} from prod #{old_id} -> #{new_id}")

# Deactivate old products
print("\n\n=== DEACTIVATING OLD PRODUCTS ===")
for old_id in [p["id"] for p in old_products]:
    cur.execute("UPDATE products SET is_active = 0 WHERE id = ?", (old_id,))
    old_name = next(p["product_name"] for p in old_products if p["id"] == old_id)
    print(f"  Deactivated: [{old_id}] {old_name}")

conn.commit()

# Summary
cur.execute("SELECT COUNT(*) FROM products WHERE is_active = 1")
active_count = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM product_candidates")
cand_count = cur.fetchone()[0]

print(f"\n=== SUMMARY ===")
print(f"Candidates migrated: {total_migrated}")
print(f"Duplicate candidates removed: {total_deduped}")
print(f"Old products deactivated: {len(old_products)}")
print(f"Active products remaining: {active_count}")
print(f"Total candidates remaining: {cand_count}")

conn.close()
