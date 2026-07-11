from pathlib import Path

from src.database import Database

def test_disable_findprice_obsolete_urls(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    p_id = db.upsert_product("Test Product")
    
    # Insert some candidates
    db.upsert_candidate(product_id=p_id, url="https://shopee.tw/123", platform="shopee", title="Normal Shopee")
    db.upsert_candidate(product_id=p_id, url="https://www.findprice.com.tw/go/shopee/xxx", platform="shopee", title="Old FindPrice Shopee")
    db.upsert_candidate(product_id=p_id, url="https://feebee.com.tw/s/123", platform="feebee", title="Feebee")
    
    # Ensure they are active
    assert len(db.get_active_candidates()) == 3
    
    # Run the equivalent of --disable-dead-findprice
    updated = db.disable_obsolete_findprice_urls()
    assert updated == 1
    
    active_candidates = db.get_active_candidates()
    assert len(active_candidates) == 2
    
    # Check the exact URL was marked as source_dead
    all_candidates = db.list_candidates(include_excluded=True, status=None)
    for c in all_candidates:
        if "findprice.com.tw/go" in c.url:
            assert c.status == "source_dead"
        else:
            assert c.status == "active"
