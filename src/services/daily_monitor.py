"""DailyMonitorService — check known URLs for prices without calling search APIs.

This is the core daily monitoring loop. It:
1. Reads all active product_candidates from DB
2. Uses platform extractors to fetch current price from each URL
3. Writes a price_snapshot for each check
4. Updates candidate status (normal / suspected_violation / price_unknown / error)
5. Exports daily Excel reports
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import AppConfig, load_config
from src.database import Database, CandidateRow
from src.extractors import ProductPageExtractor, ExtractionResult
from src.services.platform_rate_limiter import PlatformRateLimiter
from src.services.source_health import SourceHealthTracker
from src.services.fallback_price_provider import FallbackPriceProvider
from src.services.final_price import select_final_price

LOGGER = logging.getLogger(__name__)


@dataclass
class DailyMonitorResult:
    total_checked: int = 0
    violations: int = 0
    price_unknown: int = 0
    normal: int = 0
    errors: int = 0
    run_time: str = ""


class DailyMonitorService:
    """Daily price check for all known candidate URLs."""

    def __init__(
        self,
        db: Database,
        config: AppConfig,
        project_root: Path,
    ) -> None:
        self.db = db
        self.config = config
        self.project_root = project_root
        self.extractor = ProductPageExtractor(config)
        self.rate_limiter = PlatformRateLimiter(config.platform_rate_limits, db)
        self.health_tracker = SourceHealthTracker(db)
        self.fallback_provider = FallbackPriceProvider(
            {"request_timeout_seconds": config.request_timeout_seconds}, db
        )

    def run(
        self,
        product_id: int | None = None,
        progress_callback: Any | None = None,
    ) -> DailyMonitorResult:
        """Run daily monitor for all (or one) product's candidates.

        Args:
            product_id: If set, only monitor this product's candidates.
            progress_callback: Optional callable(current, total, message) for
                progress reporting. Called after each candidate is checked.
        """
        run_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
        screenshot_dir = self.project_root / "output" / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        candidates = self.db.get_active_candidates(product_id)
        total = len(candidates)
        LOGGER.info("每日監測開始：%d 個候選連結", total)

        result = DailyMonitorResult(run_time=run_time)

        if progress_callback:
            progress_callback(0, total, f"正在準備監測 {total} 個連結...")

        for idx, candidate in enumerate(candidates, 1):
            product_name = candidate.product_name or ""
            platform = candidate.platform or ""

            if progress_callback:
                progress_callback(
                    idx, total,
                    f"({idx}/{total}) [{platform}] {product_name[:20]}...",
                )

            try:
                self._check_candidate(candidate, screenshot_dir, result)
            except Exception as exc:
                LOGGER.exception("候選連結檢查失敗：%s", candidate.url)
                self.db.update_candidate_status(candidate.id, "error")
                self.db.insert_snapshot(
                    candidate_id=candidate.id,
                    product_id=candidate.product_id,
                    price=None,
                    suggested_price=candidate.suggested_price,
                    error_message=str(exc),
                )
                result.errors += 1

        self.health_tracker.auto_cooldown_rules()

        result.total_checked = total
        LOGGER.info(
            "每日監測完成：total=%d violations=%d unknown=%d normal=%d errors=%d",
            result.total_checked, result.violations,
            result.price_unknown, result.normal, result.errors,
        )
        return result

    def check_single_candidate(self, candidate_id: int) -> ExtractionResult:
        """Re-check a single candidate URL. Used by dashboard 'recheck' button."""
        candidates = self.db.list_candidates()
        target = None
        for c in candidates:
            if c.id == candidate_id:
                target = c
                break
        if target is None:
            raise ValueError(f"Candidate {candidate_id} not found")

        screenshot_dir = self.project_root / "output" / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        dummy_result = DailyMonitorResult()
        extraction = self._check_candidate(target, screenshot_dir, dummy_result, force_direct=True)
        return extraction

    def _mark_candidate_excluded(
        self, candidate: CandidateRow, result: DailyMonitorResult, keyword: str
    ) -> ExtractionResult:
        self.db.update_candidate_status(candidate.id, "excluded")
        self.db.insert_snapshot(
            candidate_id=candidate.id,
            product_id=candidate.product_id,
            price=None,
            suggested_price=candidate.suggested_price,
            error_message=f"Excluded by keyword: {keyword}",
        )
        # Excluded is often counted as "normal" so it doesn't alarm the user
        result.normal += 1
        
        # We also need to insert an observation so it shows up in dashboard
        self.db.insert_observation(
            product_id=candidate.product_id,
            candidate_id=candidate.id,
            platform=candidate.platform or "",
            source="direct_html",
            url=candidate.url,
            status="error",
            error_message=f"Excluded by keyword: {keyword}",
        )
        
        return ExtractionResult(
            platform=candidate.platform or "",
            parse_status="excluded",
            error_message=f"Excluded by keyword: {keyword}",
        )

    def _check_candidate(
        self,
        candidate: CandidateRow,
        screenshot_dir: Path,
        result: DailyMonitorResult,
        force_direct: bool = False,
    ) -> ExtractionResult:
        """Check one candidate URL and update DB."""
        LOGGER.info(
            "檢查：[%s] %s → %s",
            candidate.platform,
            candidate.product_name,
            candidate.url[:80],
        )

        exclusion_keyword = self.db.find_matching_global_exclusion(candidate)
        if exclusion_keyword:
            return self._mark_candidate_excluded(candidate, result, exclusion_keyword)

        product = self.db.get_product(candidate.product_id)

        # -------------------------------------------------------------------
        # Source 1: Direct Crawl
        # -------------------------------------------------------------------
        direct_extraction: ExtractionResult | None = None
        platform_lower = (candidate.platform or "").lower()

        # Check conditions for direct crawl
        skip_direct = False
        direct_status = ""
        
        if "findprice.com.tw/go/" in candidate.url:
            skip_direct = True
            direct_status = "source_dead"
            LOGGER.info("FindPrice URL obsolete, skipping direct crawl: %s", candidate.url[:50])
            self.db.insert_observation(
                product_id=candidate.product_id,
                candidate_id=candidate.id,
                platform="findprice",
                source="direct_html",
                url=candidate.url,
                status="source_dead",
                error_message="FindPrice go URL is permanently obsolete",
            )
        elif "findprice.com.tw/go/" in candidate.url.lower():
            skip_direct = True
            direct_status = "skipped"
            LOGGER.info("Skipping direct crawl for FindPrice redirect URL: %s", candidate.url[:50])
            self.db.insert_observation(
                product_id=candidate.product_id,
                candidate_id=candidate.id,
                platform="findprice",
                source="direct_html",
                url=candidate.url,
                status="skipped",
                error_message="FindPrice redirects are handled via Feebee fallback",
            )
        elif "biggo.com.tw" in candidate.url.lower():
            skip_direct = True
            direct_status = "skipped"
            LOGGER.info("Skipping direct crawl for BigGo URL: %s", candidate.url[:50])
            self.db.insert_observation(
                product_id=candidate.product_id,
                candidate_id=candidate.id,
                platform="biggo",
                source="direct_html",
                url=candidate.url,
                status="skipped",
                error_message="BigGo is handled via fallback",
            )
        elif "lbj.tw" in candidate.url.lower():
            skip_direct = True
            direct_status = "skipped"
            LOGGER.info("Skipping direct crawl for LBJ URL: %s", candidate.url[:50])
            self.db.insert_observation(
                product_id=candidate.product_id,
                candidate_id=candidate.id,
                platform="lbj",
                source="direct_html",
                url=candidate.url,
                status="skipped",
                error_message="LBJ is handled via fallback",
            )
        elif platform_lower == "shopee" and not self.config.shopee_direct_daily_crawl_enabled and not force_direct:
            skip_direct = True
            direct_status = "skipped_direct_crawl"
            LOGGER.info("Shopee direct crawl disabled by config, skipping: %s", candidate.url[:50])
            self.db.insert_observation(
                product_id=candidate.product_id,
                candidate_id=candidate.id,
                platform="shopee",
                source="direct_html",
                url=candidate.url,
                status="skipped_direct_crawl",
                error_message="shopee_direct_daily_crawl_enabled=False",
            )
        elif not force_direct and not self.rate_limiter.before_request(platform_lower):
            skip_direct = True
            direct_status = "rate_limited"
            LOGGER.warning("Platform %s is in cooldown, skipping direct crawl.", platform_lower)
            self.db.insert_observation(
                product_id=candidate.product_id,
                candidate_id=candidate.id,
                platform=platform_lower,
                source="direct_html",
                url=candidate.url,
                status="rate_limited",
                error_message="Platform cooldown due to previous HTTP 429",
            )

        if not skip_direct:
            # Extract
            direct_extraction = self.extractor.extract(
                url=candidate.url,
                platform=platform_lower,
                screenshot_dir=screenshot_dir,
            )
            
            # Map parse_status to observation status
            obs_status = "success"
            score = 0
            conf = 0.0
            
            if direct_extraction.parse_status == "ok":
                # Check exclusion on the extracted title/seller!
                from src.database import _matches_keyword
                exclusion_keywords = self.db.get_all_exclusion_keywords()
                matched_ex = None
                for ex in exclusion_keywords:
                    if _matches_keyword(ex, (direct_extraction.title, direct_extraction.seller)):
                        matched_ex = ex
                        break
                if matched_ex:
                    LOGGER.info("Candidate %s excluded by extracted content: %s", candidate.id, matched_ex)
                    return self._mark_candidate_excluded(candidate, result, matched_ex)
                    
                if direct_extraction.price is not None:
                    from src.matcher import match_score
                    score = match_score(candidate.product_name, direct_extraction.title or "")
                    conf = 0.9 if score > 80 else 0.5
                else:
                    obs_status = "price_unknown"
            elif direct_extraction.parse_status == "page_blocked":
                obs_status = "blocked"
            elif direct_extraction.parse_status == "captcha_required":
                obs_status = "captcha_required"
            elif direct_extraction.parse_status == "traffic_verify":
                obs_status = "traffic_verify"
            elif direct_extraction.parse_status == "timeout":
                obs_status = "error"
            elif "429" in str(direct_extraction.error_message or "") or direct_extraction.parse_status == "rate_limited":
                obs_status = "rate_limited"
                self.rate_limiter.on_429(platform_lower, "direct_html")
            else:
                obs_status = "error"

            self.db.insert_observation(
                product_id=candidate.product_id,
                candidate_id=candidate.id,
                platform=platform_lower,
                source="direct_html",
                url=candidate.url,
                title=direct_extraction.title or "",
                seller=direct_extraction.seller or "",
                price=direct_extraction.price,
                match_score=score,
                confidence=conf,
                status=obs_status,
                error_message=direct_extraction.error_message,
                raw_data=direct_extraction.raw_data,
            )
            self.health_tracker.record("direct_html", platform_lower, obs_status)

        # -------------------------------------------------------------------
        # Source 2: Fallback Providers (Feebee, BigGo, LBJ)
        # -------------------------------------------------------------------
        fallback_obs = self.fallback_provider.observe(product, candidate)
        if fallback_obs:
            self.db.insert_observation(
                product_id=candidate.product_id,
                candidate_id=candidate.id,
                **fallback_obs
            )
            self.health_tracker.record(fallback_obs["source"], fallback_obs["platform"], fallback_obs["status"])
        else:
            # Record failure
            self.db.insert_observation(
                product_id=candidate.product_id,
                candidate_id=candidate.id,
                platform=platform_lower,
                source="fallback",
                url="",
                status="price_unknown",
                error_message="Fallback providers returned no results or excluded"
            )
            self.health_tracker.record("fallback", platform_lower, "price_unknown")

        # -------------------------------------------------------------------
        # Make Final Decision
        # -------------------------------------------------------------------
        recent_obs = self.db.get_observations_for_decision(candidate.product_id)
        # Filter to only this candidate's obs + global product manual obs
        candidate_obs = [o for o in recent_obs if o.candidate_id == candidate.id or o.source == "manual"]
        
        decision = select_final_price(
            product_id=candidate.product_id,
            observations=candidate_obs,
            suggested_price=candidate.suggested_price,
        )

        LOGGER.info(
            "決策結果：[%s] price=%s status=%s source=%s reason=%s",
            candidate.product_name, decision.final_price, decision.final_status,
            decision.final_price_source, decision.decision_reason
        )

        # Create a unified ExtractionResult for the caller/reports
        final_extraction = direct_extraction or ExtractionResult(
            platform=platform_lower,
            parse_status="ok" if decision.final_price else "error",
        )
        # Assign url directly since ExtractionResult doesn't take it in its init (only title, price, etc.)
        final_extraction.raw_data["url"] = candidate.url
        
        # Override with decision
        final_extraction.price = decision.final_price
        final_extraction.raw_data["price_source"] = decision.final_price_source
        
        # Update metrics
        if decision.final_status in ("suspected_violation", "verified_violation"):
            result.violations += 1
            # Maintain backward compat with current 'suspected_violation'
            decision.final_status = "suspected_violation" 
            final_extraction.parse_status = "ok"
        elif decision.final_status == "price_unknown":
            result.price_unknown += 1
            final_extraction.parse_status = "price_not_found"
        elif decision.final_status == "needs_review":
            result.normal += 1  # count as normal but needs review
            final_extraction.parse_status = "ok"
        elif decision.final_status in ("verified_price", "likely_price", "normal"):
            decision.final_status = "normal"
            result.normal += 1
            final_extraction.parse_status = "ok"
        else:
            result.errors += 1
            final_extraction.parse_status = "error"

        # Update candidate
        current_status = candidate.status if hasattr(candidate, 'status') else ''
        if current_status == 'takedown_notified':
            db_status = 'takedown_notified'
        else:
            db_status = decision.final_status
            if db_status == "price_unknown" and skip_direct and "FindPrice" in direct_status:
                db_status = "source_dead"

        self.db.update_candidate_status(
            candidate_id=candidate.id,
            status=db_status,
            last_price=decision.final_price,
        )

        # Update snapshot
        snapshot_id = self.db.insert_snapshot(
            candidate_id=candidate.id,
            product_id=candidate.product_id,
            price=decision.final_price,
            suggested_price=candidate.suggested_price,
            is_violation=(db_status == "suspected_violation"),
            screenshot_path=final_extraction.screenshot_path,
            error_message="",
            raw_data={
                "decision_reason": decision.decision_reason,
                "all_prices": decision.all_prices,
            },
        )
        
        # Update final_price columns
        self.db.update_snapshot_final_price(
            snapshot_id=snapshot_id,
            final_price=decision.final_price,
            final_price_source=decision.final_price_source,
            final_confidence=decision.final_confidence,
            final_status=decision.final_status,
            decision_reason=decision.decision_reason,
        )

        return final_extraction


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """Run daily monitor from command line."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--disable-dead-findprice", action="store_true",
                        help="Mark all findprice.com.tw/go/ candidates as source_dead")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent.parent
    config = load_config(root / "config.yaml")
    db = Database(root / "data" / "price_monitor.db")

    if args.disable_dead_findprice:
        db.disable_findprice_candidates()
        LOGGER.info("FindPrice清理完畢。")
        return 0

    # Auto-import products if DB is empty
    if not db.list_products():
        from src.csv_importer import full_import
        full_import(db, root)

    service = DailyMonitorService(db, config, root)
    result = service.run()

    # Export reports
    from src.services.report_service import ReportService
    report_svc = ReportService(db, root)
    report_svc.export_daily_report()
    report_svc.export_violations_report()

    LOGGER.info("每日監測結果：%s", result)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
