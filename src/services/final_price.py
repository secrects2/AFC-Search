"""Final price decision logic."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.database import ObservationRow

LOGGER = logging.getLogger(__name__)


@dataclass
class FinalPriceDecision:
    final_price: float | None
    final_price_source: str
    final_confidence: float
    final_status: str
    decision_reason: str
    observations_used: int
    all_prices: dict[str, float | None]


def select_final_price(
    product_id: int,
    observations: list[ObservationRow],
    suggested_price: float | None = None,
) -> FinalPriceDecision:
    """Select the best price from multiple observations for a candidate."""
    if not observations:
        return FinalPriceDecision(
            final_price=None,
            final_price_source="",
            final_confidence=0.0,
            final_status="price_unknown",
            decision_reason="無觀測記錄",
            observations_used=0,
            all_prices={},
        )

    all_prices = {}
    valid_obs = []

    # 1. 排除失效與非商品價格
    for obs in observations:
        all_prices[obs.source] = obs.price

        if obs.status in (
            "blocked", "captcha_required", "traffic_verify", 
            "rate_limited", "source_dead", "error", "skipped", "skipped_direct_crawl"
        ):
            continue
            
        if obs.price is None or obs.price <= 0 or obs.price > 50000:
            continue

        valid_obs.append(obs)

    if not valid_obs:
        # Determine the most severe status from failed observations
        failed_statuses = [obs.status for obs in observations]
        if "rate_limited" in failed_statuses:
            fallback_status = "rate_limited"
            fallback_reason = "Platform cooldown or HTTP 429"
        elif "blocked" in failed_statuses or "captcha_required" in failed_statuses or "traffic_verify" in failed_statuses:
            fallback_status = "blocked"
            fallback_reason = "遭到平台阻擋或需要驗證碼"
        else:
            fallback_status = "price_unknown"
            fallback_reason = "所有來源皆失敗或無效價格"

        return FinalPriceDecision(
            final_price=None,
            final_price_source="",
            final_confidence=0.0,
            final_status=fallback_status,
            decision_reason=fallback_reason,
            observations_used=len(observations),
            all_prices=all_prices,
        )

    # 4. 優先級權重
    source_weights = {
        "manual": 100,
        "direct_dom": 80,
        "direct_json": 80,
        "direct_html": 70,
        "feebee": 60,
        "findprice": 55,
        "biggo": 55,
        "lbj": 65,
        "serpapi": 50,
        "brave": 50,
        "shopee": 50,
        "visual_ocr": 40,
    }

    best_obs = None
    best_score = -1

    for obs in valid_obs:
        weight = source_weights.get(obs.source, 0)
        # 加權公式：來源權重 * 信心度 * (名稱相符度/100)
        score = weight * obs.confidence * (obs.match_score / 100.0 if obs.match_score > 0 else 0.5)
        
        # manual 絕對優先
        if obs.source == "manual":
            score += 1000

        if score > best_score:
            best_score = score
            best_obs = obs

    if not best_obs:
        return FinalPriceDecision(
            final_price=None,
            final_price_source="",
            final_confidence=0.0,
            final_status="price_unknown",
            decision_reason="無法決定最佳價格",
            observations_used=len(observations),
            all_prices=all_prices,
        )

    # 判定最終狀態
    final_status = "normal"
    reason = f"依加權分數選出 ({best_obs.source})"

    # 5. verified_price: direct_dom 成功 且 match_score >= 85 且 confidence >= 0.85
    if "direct" in best_obs.source and best_obs.match_score >= 85 and best_obs.confidence >= 0.85:
        final_status = "verified_price"
        reason = f"高可信度直接抓取 ({best_obs.source})"
        
    # 6. likely_price: feebee 成功 且 match_score >= 80 且 confidence >= 0.75
    elif best_obs.source == "feebee" and best_obs.match_score >= 80 and best_obs.confidence >= 0.75:
        final_status = "likely_price"
        reason = "高可信度飛比抓取"

    # 7. needs_review: 價格低於 suggested_price 但 confidence < 0.8
    if suggested_price is not None and best_obs.price < suggested_price:
        if best_obs.confidence < 0.8:
            final_status = "needs_review"
            reason = f"疑似破價但可信度不足 ({best_obs.confidence:.2f})"
        else:
            # 高可信度且破價
            final_status = "suspected_violation"
            if "direct" in best_obs.source and best_obs.match_score >= 85:
                final_status = "verified_violation"

    # Any price found through a backup search after direct verification failed
    # is evidence for review, not an automatic normal/violation decision. This
    # applies consistently to every platform, not only PChome or Coupang.
    fallback_sources = {
        "feebee", "findprice", "biggo", "lbj", "serpapi", "brave", "shopee",
    }
    direct_failure_statuses = {
        "blocked",
        "captcha_required",
        "traffic_verify",
        "rate_limited",
        "source_dead",
        "error",
        "price_unknown",
        "skipped",
        "skipped_direct_crawl",
    }
    fallback_after_direct_failure = (
        best_obs.source in fallback_sources
        and any(
            obs.source == "direct_html"
            and obs.status in direct_failure_statuses
            for obs in observations
        )
    )
    if fallback_after_direct_failure:
        final_status = "needs_review"
        platform_name = getattr(best_obs, "platform", "").capitalize()
        reason = (
            f"{platform_name or '商品'} 直連價格不可用，備援來源價格僅供參考，待人工確認"
        )

    return FinalPriceDecision(
        final_price=best_obs.price,
        final_price_source=best_obs.source,
        final_confidence=best_obs.confidence,
        final_status=final_status,
        decision_reason=reason,
        observations_used=len(observations),
        all_prices=all_prices,
    )
