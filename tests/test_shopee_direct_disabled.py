from unittest.mock import MagicMock
from pathlib import Path
from src.services.daily_monitor import DailyMonitorService, DailyMonitorResult
from src.database import CandidateRow

def test_shopee_direct_disabled_skips_crawl():
    db = MagicMock()
    db.find_matching_global_exclusion.return_value = None
    config = MagicMock()
    config.platform_rate_limits = {}
    config.shopee_direct_daily_crawl_enabled = False
    
    service = DailyMonitorService(db, config, Path("."))
    
    # Mock rate limiter allowed
    service.rate_limiter.before_request = MagicMock(return_value=True)
    service.extractor.extract = MagicMock()
    
    candidate = CandidateRow(
        id=1, product_id=1, platform="shopee", url="http://shopee",
        title="", seller="", source_found_by="manual", status="active",
        product_name="test", suggested_price=100.0, brand=""
    )
    
    service.feebee_provider.observe = MagicMock(return_value=None)
    
    result = DailyMonitorResult()
    service._check_candidate(candidate, Path("."), result)
    
    # Check that extractor was NOT called
    assert not service.extractor.extract.called
    
    # Check that it inserted observation with skipped_direct_crawl
    assert db.insert_observation.called
    
    # Find the call that has source="direct_html"
    direct_call = None
    for call in db.insert_observation.call_args_list:
        if call[1].get("source") == "direct_html":
            direct_call = call[1]
            break
            
    assert direct_call is not None
    assert direct_call["status"] == "skipped_direct_crawl"
