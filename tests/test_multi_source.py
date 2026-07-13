from pathlib import Path
import pytest
from src.database import Database
from src.services.final_price import select_final_price

from types import SimpleNamespace

def test_select_final_price_single_direct():
    """Test final price selection with single direct crawl result."""
    obs = [SimpleNamespace(
        source="direct_html",
        price=120,
        status="success",
        match_score=100,
        confidence=0.9,
    )]
    decision = select_final_price(1, obs, 120)
    assert decision.final_price == 120
    assert decision.final_status == "verified_price"
    assert decision.final_price_source == "direct_html"

def test_select_final_price_feebee_fallback():
    """A Feebee price is retained but requires review after direct failure."""
    obs = [
        SimpleNamespace(
            source="direct_html",
            price=None,
            status="blocked",
            match_score=0,
            confidence=0.0,
        ),
        SimpleNamespace(
            source="feebee",
            price=120,
            status="success",
            match_score=90,
            confidence=0.8,
        )
    ]
    decision = select_final_price(1, obs, 120)
    assert decision.final_price == 120
    assert decision.final_status == "needs_review"
    assert decision.final_price_source == "feebee"

def test_select_final_price_pchome_blocked_fallback_needs_review():
    """PChome fallback is not trusted when direct verification is blocked."""
    obs = [
        SimpleNamespace(
            platform="pchome",
            source="direct_html",
            price=None,
            status="blocked",
            match_score=0,
            confidence=0.0,
        ),
        SimpleNamespace(
            platform="pchome",
            source="feebee",
            price=2880,
            status="success",
            match_score=90,
            confidence=0.8,
        )
    ]
    decision = select_final_price(1, obs, 2480)
    assert decision.final_price == 2880
    assert decision.final_price_source == "feebee"
    assert decision.final_status == "needs_review"
    assert "待人工確認" in decision.decision_reason

def test_select_final_price_coupang_blocked_fallback_needs_review():
    """Coupang fallback is not trusted when direct verification is blocked."""
    obs = [
        SimpleNamespace(
            platform="coupang",
            source="direct_html",
            price=None,
            status="blocked",
            match_score=0,
            confidence=0.0,
        ),
        SimpleNamespace(
            platform="coupang",
            source="feebee",
            price=2280,
            status="success",
            match_score=100,
            confidence=0.8,
        )
    ]
    decision = select_final_price(1, obs, 2380)
    assert decision.final_price == 2280
    assert decision.final_price_source == "feebee"
    assert decision.final_status == "needs_review"
    assert "待人工確認" in decision.decision_reason


def test_select_final_price_includes_biggo_fallback_weight():
    """BigGo can win when it has a stronger product match than Feebee."""
    obs = [
        SimpleNamespace(
            platform="pchome",
            source="direct_html",
            price=None,
            status="blocked",
            match_score=0,
            confidence=0.0,
        ),
        SimpleNamespace(
            platform="pchome",
            source="feebee",
            price=3000,
            status="success",
            match_score=70,
            confidence=0.8,
        ),
        SimpleNamespace(
            platform="pchome",
            source="biggo",
            price=3000,
            status="success",
            match_score=100,
            confidence=0.8,
        ),
    ]

    decision = select_final_price(1, obs, 3000)

    assert decision.final_price_source == "biggo"
    assert decision.final_status == "needs_review"

def test_select_final_price_manual_override():
    """Test manual review price takes precedence."""
    obs = [
        SimpleNamespace(
            source="manual",
            price=120,
            status="success",
            match_score=100,
            confidence=1.0,
        ),
        SimpleNamespace(
            source="direct_html",
            price=150,
            status="success",
            match_score=100,
            confidence=0.9,
        )
    ]
    decision = select_final_price(1, obs, 120)
    assert decision.final_price == 120
    assert decision.final_status == "normal"
    assert decision.final_price_source == "manual"

def test_select_final_price_needs_review():
    """Test low confidence feebee result triggers manual review."""
    obs = [
        SimpleNamespace(
            source="direct_html",
            price=None,
            status="blocked",
            match_score=0,
            confidence=0.0,
        ),
        SimpleNamespace(
            source="feebee",
            price=110,
            status="success",
            match_score=50,
            confidence=0.3, # low confidence
        )
    ]
    decision = select_final_price(1, obs, 120)
    assert decision.final_price == 110
    assert decision.final_status == "needs_review"
    assert decision.final_price_source == "feebee"


@pytest.mark.parametrize("platform", ["momo", "yahoo", "ruten", "shopee", "pchome", "coupang"])
def test_all_platform_fallback_prices_require_review(platform: str):
    """Every platform uses the same review rule for a backup price."""
    obs = [
        SimpleNamespace(
            platform=platform,
            source="direct_html",
            price=None,
            status="price_unknown",
            match_score=0,
            confidence=0.0,
        ),
        SimpleNamespace(
            platform=platform,
            source="findprice",
            price=120,
            status="success",
            match_score=95,
            confidence=0.8,
        ),
    ]

    decision = select_final_price(1, obs, 120)

    assert decision.final_price == 120
    assert decision.final_price_source == "findprice"
    assert decision.final_status == "needs_review"

def test_select_final_price_suspected_violation():
    """Test violation detection."""
    obs = [
        SimpleNamespace(
            source="direct_html",
            price=80,
            status="success",
            match_score=100,
            confidence=0.85, # high confidence, suspected or verified violation
        )
    ]
    # Suggested price is 120, so 80 is a violation
    decision = select_final_price(1, obs, 120)
    assert decision.final_price == 80
    assert decision.final_status == "verified_violation"
