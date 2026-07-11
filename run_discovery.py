"""Run targeted discovery search for products missing active candidates."""
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))

from src.config import load_config
from src.database import Database
from src.services.discovery_search import DiscoverySearchService

config = load_config(root / "config.yaml")
db = Database(root / "data" / "price_monitor.db")
service = DiscoverySearchService(db, config, root)

# Find products with fewer than 2 active candidates
products = db.list_products()
needs_search = []
for p in products:
    candidates = db.list_candidates(product_id=p.id)
    active = [c for c in candidates if c.status in ("normal", "active", "takedown_notified")]
    if len(active) < 2:
        needs_search.append(p)

print(f"Products needing search: {len(needs_search)} / {len(products)} total")
print()

searched = 0
for p in needs_search:
    try:
        print(f"[{searched+1}/{len(needs_search)}] Searching: {p.product_name} (id={p.id})")
        result = service.search_product(p.id)
        print(f"  → found={result.get('found',0)} new={result.get('new',0)}")
        searched += 1
    except Exception as exc:
        print(f"  ✗ Error: {exc}")
        continue

print()
print(f"Done! Searched {searched} products.")
