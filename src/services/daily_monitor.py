"""DailyMonitorService — check known URLs for prices without calling search APIs.

This is the core daily monitoring loop. It:
1. Reads all active product_candidates from DB
2. Uses platform extractors to fetch current price from each URL
3. Writes a price_snapshot for each check
4. Updates candidate status (normal / suspected_violation / price_unknown / error)
5. Exports daily Excel reports
"""
from __future__ import annotations

import json
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
from src.search.lbj_api import fetch_lbj_query_price
from src.image_text import scan_image_urls_for_text

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
            {
                "request_timeout_seconds": config.request_timeout_seconds,
                "serpapi_api_key": config.serpapi_api_key,
                "brave_api_key": config.brave_api_key,
                "platforms": config.platforms,
                "max_results_per_product": config.max_results_per_product,
            },
            db,
        )
        # Keep the old attribute name available for integrations and tests
        # while the monitor uses the multi-source fallback provider.
        self.feebee_provider = self.fallback_provider

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

    def confirm_candidate_and_start_monitoring(self, candidate_id: int) -> None:
        """Confirm the latest reviewed price and return the candidate to monitoring.

        The current fallback price is recorded as a manual observation so the
        approval is auditable. The suggested price remains unchanged.
        """
        target = next(
            (candidate for candidate in self.db.list_candidates() if candidate.id == candidate_id),
            None,
        )
        if target is None:
            raise ValueError(f"Candidate {candidate_id} not found")
        if target.status == "excluded":
            raise ValueError("此商品已被排除，無法開始監控")

        latest = next(
            (snapshot for snapshot in self.db.get_snapshots(limit=500) if snapshot.candidate_id == candidate_id),
            None,
        )
        if latest is None:
            raise ValueError("找不到可確認的監測結果，請先重新抓取")

        confirmed_price = latest.final_price
        if confirmed_price is None:
            confirmed_price = latest.price
        if confirmed_price is None or confirmed_price <= 0:
            raise ValueError("目前沒有可確認的價格，請先輸入人工觀測價格")

        product = self.db.get_product(target.product_id)
        if product is None:
            raise ValueError("找不到對應的商品資料")

        confirmed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.db.insert_observation(
            product_id=target.product_id,
            candidate_id=target.id,
            platform=target.platform or "manual",
            source="manual",
            url=target.url,
            title=target.title,
            seller=target.seller,
            price=float(confirmed_price),
            match_score=100,
            confidence=1.0,
            status="success",
            error_message="人工確認商品並開始監控",
            raw_data={
                "review_action": "confirm_and_start_monitoring",
                "confirmed_at": confirmed_at,
                "confirmed_from_snapshot_id": latest.id,
                "confirmed_from_source": latest.final_price_source,
                "confirmed_from_status": latest.final_status,
                "confirmed_url": target.url,
            },
        )

        recent_obs = self.db.get_observations_for_decision(target.product_id)
        candidate_obs = [
            observation
            for observation in recent_obs
            if observation.candidate_id == target.id or observation.source == "manual"
        ]
        decision = select_final_price(
            product_id=target.product_id,
            observations=candidate_obs,
            suggested_price=target.suggested_price,
        )
        if decision.final_price is None:
            raise ValueError("人工確認後仍無法決定價格")

        monitor_status = decision.final_status
        if monitor_status in {"verified_price", "likely_price"}:
            monitor_status = "normal"
        elif monitor_status == "needs_review":
            monitor_status = "price_unknown"

        self.db.update_candidate_status(
            candidate_id=target.id,
            status=monitor_status,
            last_price=decision.final_price,
        )

        snapshot_id = self.db.insert_snapshot(
            candidate_id=target.id,
            product_id=target.product_id,
            price=decision.final_price,
            suggested_price=target.suggested_price,
            is_violation=monitor_status in {"suspected_violation", "verified_violation"},
            screenshot_path=latest.screenshot_path,
            raw_data={
                "review_action": "confirm_and_start_monitoring",
                "confirmed_at": confirmed_at,
                "confirmed_from_snapshot_id": latest.id,
                "decision_reason": decision.decision_reason,
                "all_prices": decision.all_prices,
                "price_source": decision.final_price_source,
                "final_confidence": decision.final_confidence,
                "final_status": monitor_status,
            },
        )
        self.db.update_snapshot_final_price(
            snapshot_id=snapshot_id,
            final_price=decision.final_price,
            final_price_source=decision.final_price_source,
            final_confidence=decision.final_confidence,
            final_status=monitor_status,
            decision_reason=decision.decision_reason,
        )

    def _mark_candidate_excluded(
        self,
        candidate: CandidateRow,
        result: DailyMonitorResult,
        keyword: str,
        extraction: ExtractionResult | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> ExtractionResult:
        raw_data = evidence or {}
        error_message = (
            f"Excluded by image rule: {keyword}"
            if extraction is not None
            else f"Excluded by keyword: {keyword}"
        )
        self.db.update_candidate_status(candidate.id, "excluded")
        self.db.insert_snapshot(
            candidate_id=candidate.id,
            product_id=candidate.product_id,
            price=None,
            suggested_price=candidate.suggested_price,
            error_message=error_message,
            raw_data=raw_data,
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
            title=extraction.title if extraction else "",
            seller=extraction.seller if extraction else "",
            price=extraction.price if extraction else None,
            status="excluded" if extraction else "error",
            error_message=error_message,
            raw_data=raw_data,
        )
        
        return ExtractionResult(
            title=extraction.title if extraction else "",
            price=extraction.price if extraction else None,
            seller=extraction.seller if extraction else "",
            screenshot_path=extraction.screenshot_path if extraction else "",
            platform=candidate.platform or "",
            raw_data=raw_data,
            parse_status="excluded",
            error_message=error_message,
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
        elif "lbj.tw" in candidate.url.lower() and "/rd.ashx" not in candidate.url.lower():
            skip_direct = True
            direct_status = "skipped"
            LOGGER.info("Skipping direct crawl for LBJ URL: %s", candidate.url[:50])
            self.db.insert_observation(
                product_id=candidate.product_id,
                candidate_id=candidate.id,
                platform="lbj",
                source="direct_html",
                url=candidate.url,
                status="skipped_direct_crawl",
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

            if platform_lower == "momo" and self.config.enable_ocr:
                image_scan = scan_image_urls_for_text(
                    direct_extraction.image_urls,
                    marker="官方",
                    timeout_seconds=int(self.config.request_timeout_seconds),
                )
                direct_extraction.raw_data.update(image_scan.as_raw_data())
                if image_scan.matched:
                    LOGGER.info(
                        "MOMO 圖片含官方字樣，排除候選連結：%s",
                        candidate.url[:100],
                    )
                    return self._mark_candidate_excluded(
                        candidate,
                        result,
                        "MOMO 圖片含官方字樣",
                        extraction=direct_extraction,
                        evidence=direct_extraction.raw_data,
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

        # Preserve the price shown on the LBJ comparison page as its own
        # observation. Query.aspx pages are comparison pages, not product
        # pages, so they must not be parsed by the generic product extractor.
        lbj_price = candidate.last_price
        lbj_price_evidence = "candidate stored LBJ price"
        lbj_query_price_found = False
        is_lbj_query = (
            platform_lower == "lbj"
            and "/bj/query.aspx" in candidate.url.lower()
        )
        if is_lbj_query:
            fetched_lbj_price, fetched_lbj_evidence = fetch_lbj_query_price(
                candidate.url,
                timeout=int(self.config.request_timeout_seconds),
            )
            if fetched_lbj_price is not None:
                lbj_price = fetched_lbj_price
                lbj_price_evidence = fetched_lbj_evidence
                lbj_query_price_found = True

        if candidate.source_found_by == "lbj" and not lbj_query_price_found:
            try:
                preserved_price = json.loads(candidate.raw_data or "{}").get("lbj_price")
            except (TypeError, ValueError):
                preserved_price = None
            if isinstance(preserved_price, (int, float)):
                lbj_price = float(preserved_price)

        if (candidate.source_found_by == "lbj" or is_lbj_query) and lbj_price is not None:
            self.db.insert_observation(
                product_id=candidate.product_id,
                candidate_id=candidate.id,
                platform=platform_lower,
                source="lbj",
                url=candidate.url,
                title=candidate.title,
                seller=candidate.seller,
                price=lbj_price,
                match_score=100,
                confidence=0.75,
                status="success",
                raw_data={
                    "source": "lbj_query" if is_lbj_query else "lbj_search",
                    "price_is_from_discovery": True,
                    "discovery_price": lbj_price,
                    "price_evidence": lbj_price_evidence,
                },
            )
            self.health_tracker.record("lbj", platform_lower, "success")

        # -------------------------------------------------------------------
        # Source 2: Universal fallback providers
        # -------------------------------------------------------------------
        # A successful direct price is authoritative enough for this run. The
        # search chain is reserved for blocked pages, missing/invalid prices,
        # skipped direct crawls, and other direct extraction failures.
        direct_failed = not lbj_query_price_found and (
            skip_direct
            or direct_extraction is None
            or direct_extraction.parse_status != "ok"
            or direct_extraction.price is None
            or direct_extraction.price <= 0
            or direct_extraction.price > 500_000
        )
        fallback_obs = None
        if direct_failed:
            fallback_obs = self.fallback_provider.observe(product, candidate)
            direct_failure_status = direct_status or (
                direct_extraction.parse_status if direct_extraction else "not_attempted"
            )
            direct_failure_reason = (
                direct_extraction.error_message
                or direct_extraction.raw_data.get("evidence_text", "")
                if direct_extraction
                else "direct crawl was skipped"
            )

            if fallback_obs:
                fallback_raw_data = dict(fallback_obs.get("raw_data") or {})
                fallback_raw_data.update({
                    "fallback_trigger": "direct_price_unavailable",
                    "direct_failure_status": direct_failure_status,
                    "direct_failure_reason": direct_failure_reason,
                })
                fallback_obs["raw_data"] = fallback_raw_data
                self.db.insert_observation(
                    product_id=candidate.product_id,
                    candidate_id=candidate.id,
                    **fallback_obs
                )
                self.health_tracker.record(
                    fallback_obs["source"], fallback_obs["platform"], fallback_obs["status"]
                )
            else:
                # Persist the complete provider audit even when every backup
                # source fails, so the next review can explain what happened.
                fallback_audit = self.fallback_provider.last_audit
                fallback_audit.update({
                    "fallback_trigger": "direct_price_unavailable",
                    "direct_failure_status": direct_failure_status,
                    "direct_failure_reason": direct_failure_reason,
                })
                self.db.insert_observation(
                    product_id=candidate.product_id,
                    candidate_id=candidate.id,
                    platform=platform_lower,
                    source="fallback",
                    url="",
                    status="price_unknown",
                    error_message="Fallback providers returned no usable results",
                    raw_data=fallback_audit,
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
            result.price_unknown += 1
            final_extraction.parse_status = "price_not_found"
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
            if db_status == "needs_review":
                # Keep the candidate eligible for the next scheduled retry;
                # the snapshot's needs_review status sends it to manual review.
                db_status = "price_unknown"
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
                "price_source": decision.final_price_source,
                "final_confidence": decision.final_confidence,
                "final_status": decision.final_status,
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
