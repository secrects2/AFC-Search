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

from src.config import AppConfig, load_config
from src.database import Database, CandidateRow
from src.extractors import ProductPageExtractor, ExtractionResult

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

    def run(self, product_id: int | None = None) -> DailyMonitorResult:
        """Run daily monitor for all (or one) product's candidates."""
        run_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
        screenshot_dir = self.project_root / "output" / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        candidates = self.db.get_active_candidates(product_id)
        LOGGER.info("每日監測開始：%d 個候選連結", len(candidates))

        result = DailyMonitorResult(run_time=run_time)

        for candidate in candidates:
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

            # Rate limiting
            if float(self.config.request_delay_seconds) > 0:
                time.sleep(float(self.config.request_delay_seconds))

        result.total_checked = len(candidates)
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
        extraction = self._check_candidate(target, screenshot_dir, dummy_result)
        return extraction

    def _check_candidate(
        self,
        candidate: CandidateRow,
        screenshot_dir: Path,
        result: DailyMonitorResult,
    ) -> ExtractionResult:
        """Check one candidate URL and update DB."""
        LOGGER.info(
            "檢查：[%s] %s → %s",
            candidate.platform,
            candidate.product_name,
            candidate.url[:80],
        )

        extraction = self.extractor.extract(
            url=candidate.url,
            platform=candidate.platform,
            screenshot_dir=screenshot_dir,
        )

        # --- Name cross-validation ---
        # If we got a title from the page, verify it matches the expected product
        from src.matcher import match_score
        title_score = 0
        if extraction.title and extraction.parse_status == "ok":
            title_score = match_score(candidate.product_name, extraction.title)
            if title_score < 40:
                LOGGER.warning(
                    "名稱不符 (score=%d)：預期 [%s] 但頁面標題為 [%s]，標記為 excluded",
                    title_score, candidate.product_name, extraction.title[:50],
                )
                self.db.update_candidate_status(candidate.id, "excluded")
                self.db.insert_snapshot(
                    candidate_id=candidate.id,
                    product_id=candidate.product_id,
                    price=extraction.price,
                    suggested_price=candidate.suggested_price,
                    is_violation=False,
                    screenshot_path=extraction.screenshot_path,
                    error_message=f"名稱不符 (score={title_score}): {extraction.title[:80]}",
                    raw_data={"title_match_score": title_score, "page_title": extraction.title},
                )
                result.errors += 1
                return extraction

        # --- Image cross-validation ---
        image_score = 0
        if (
            self.config.enable_image_match
            and extraction.screenshot_path
            and extraction.parse_status == "ok"
        ):
            from src.image_matcher import average_hash_file, hamming_similarity
            product = self.db.get_product(candidate.product_id)
            if product and product.official_image_hash:
                try:
                    screen_hash = average_hash_file(Path(extraction.screenshot_path))
                    image_score = hamming_similarity(product.official_image_hash, screen_hash)
                    threshold = int(self.config.image_match_threshold or 80)
                    
                    if image_score < threshold:
                        LOGGER.warning(
                            "圖片不符 (score=%d < %d)：商品 [%s] 截圖比對失敗，標記為 excluded",
                            image_score, threshold, candidate.product_name,
                        )
                        self.db.update_candidate_status(candidate.id, "excluded")
                        self.db.insert_snapshot(
                            candidate_id=candidate.id,
                            product_id=candidate.product_id,
                            price=extraction.price,
                            suggested_price=candidate.suggested_price,
                            is_violation=False,
                            screenshot_path=extraction.screenshot_path,
                            error_message=f"圖片不符 (score={image_score})",
                            raw_data={
                                "title_match_score": title_score,
                                "image_match_score": image_score,
                                "page_title": extraction.title
                            },
                        )
                        result.errors += 1
                        return extraction
                except Exception as exc:
                    LOGGER.warning("圖片比對失敗: %s", exc)

        # Determine status
        suggested = candidate.suggested_price
        price = extraction.price
        is_violation = False

        if extraction.parse_status in ("error", "page_blocked", "timeout"):
            status = "error"
            result.errors += 1
        elif price is None:
            status = "price_unknown"
            result.price_unknown += 1
        elif suggested is not None and price < suggested - float(self.config.price_tolerance):
            status = "suspected_violation"
            is_violation = True
            result.violations += 1
        else:
            status = "normal"
            result.normal += 1

        # Update candidate
        self.db.update_candidate_status(
            candidate_id=candidate.id,
            status=status,
            last_price=price,
        )

        # Insert snapshot
        self.db.insert_snapshot(
            candidate_id=candidate.id,
            product_id=candidate.product_id,
            price=price,
            suggested_price=suggested,
            is_violation=is_violation,
            screenshot_path=extraction.screenshot_path,
            error_message=extraction.error_message,
            raw_data={
                "evidence_text": extraction.raw_data.get("evidence_text", ""),
                "title_match_score": title_score,
                "page_title": extraction.title,
            },
        )

        return extraction


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """Run daily monitor from command line."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    root = Path(__file__).resolve().parent.parent.parent
    config = load_config(root / "config.yaml")
    db = Database(root / "data" / "price_monitor.db")

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
