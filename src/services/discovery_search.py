"""DiscoverySearchService — find new product candidate URLs via search APIs.

NOT part of daily monitoring. Only runs when:
- Manually triggered from dashboard
- Weekly scheduled task
- Monthly full scan
- High-risk product targeted search
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.config import AppConfig, load_config
from src.database import Database, is_coupon_source, is_disabled_platform
from src.image_text import ImageTextScanResult, scan_image_urls_for_text
from src.matcher import match_score, normalize_name
from src.parsers import get_parser
from src.search.serp_api import detect_platform
from src.search.search_api import build_chain_provider
from src.services.budget_tracker import BudgetExhausted, BudgetTracker

LOGGER = logging.getLogger(__name__)


def _best_discovery_match_score(
    product_name: str,
    keywords: str,
    found_title: str,
) -> int:
    """Score the product name and sufficiently specific configured aliases."""
    scores = [match_score(product_name, found_title)]
    for keyword in (part.strip() for part in (keywords or "").split(",")):
        if len(normalize_name(keyword)) < 3:
            continue
        scores.append(match_score(keyword, found_title))
    return max(scores, default=0)


class DiscoverySearchService:
    """Search for new product listings on e-commerce platforms."""

    def __init__(
        self,
        db: Database,
        config: AppConfig,
        project_root: Path,
    ) -> None:
        self.db = db
        self.config = config
        self.project_root = project_root
        self.budget = BudgetTracker(db)

    def _scan_momo_official_image(self, url: str) -> ImageTextScanResult:
        if not self.config.enable_ocr:
            return ImageTextScanResult("ocr_disabled", marker="官方")

        try:
            parser = get_parser("momo", url, self.config)
            html_text = parser.fetch_page(url, platform="momo")
            extract_images = getattr(parser, "_extract_image_urls", None)
            if not callable(extract_images):
                return ImageTextScanResult(
                    "image_extract_failed",
                    marker="官方",
                    error_message="MOMO parser cannot extract image URLs",
                )
            image_urls = extract_images(html_text, url)
        except Exception as exc:
            return ImageTextScanResult(
                "page_fetch_failed",
                marker="官方",
                error_message=str(exc),
            )

        return scan_image_urls_for_text(
            image_urls,
            marker="官方",
            timeout_seconds=int(self.config.request_timeout_seconds),
        )

    def search_product(self, product_id: int) -> dict[str, int]:
        """Search for a single product. Returns {found, new, existing}."""
        self.budget.check_budget()

        product = self.db.get_product(product_id)
        if not product:
            raise ValueError(f"Product {product_id} not found")

        provider = build_chain_provider(
            serpapi_key=self.config.serpapi_api_key,
            brave_key=self.config.brave_api_key,
            platforms=self.config.platforms,
            cache_path=self.project_root / "data" / "search_cache.json",
            cache_hours=int(self.config.search_cache_hours),
            timeout=float(self.config.request_timeout_seconds),
        )

        if not provider.enabled:
            LOGGER.warning("搜尋供應商未設定（SERPAPI_API_KEY / BRAVE_SEARCH_API_KEY）")
            return {"found": 0, "new": 0, "existing": 0}

        from src.loader import Product

        # Search the complete product identity. The previous implementation
        # used only the first keyword (for example, "AFC 快調"), which omitted
        # the distinguishing alias "每日快調" from this product.
        search_name = product.product_name.strip()
        brand_prefix = (product.brand or "AFC").strip()
        if brand_prefix and brand_prefix.casefold() not in search_name.casefold():
            search_name = f"{brand_prefix} {search_name}".strip()
        longest_keyword = max(
            (part.strip() for part in (product.keywords or "").split(",")),
            key=len,
            default="",
        )
        if longest_keyword and longest_keyword.casefold() not in search_name.casefold():
            search_name = f"{search_name} {longest_keyword}".strip()

        LOGGER.info("搜尋關鍵字：%s (product=%s)", search_name, product.product_name)

        temp_product = Product(
            suggested_price=product.suggested_price or 0,
            product_name=search_name,
            row_index=product.id,
            raw_suggested_price=str(product.suggested_price or ""),
        )

        results = provider.search(temp_product, int(self.config.max_results_per_product))

        # Log API usage
        provider_attempts = getattr(provider, "last_attempts", [])
        attempt_summary = "; ".join(
            f"{attempt.get('provider', '')}:{attempt.get('status', '')}"
            for attempt in provider_attempts
            if attempt.get("status") in {"blocked", "error", "unavailable"}
        )
        self.db.log_api_usage(
            provider=provider.last_provider,
            query=product.product_name,
            result_count=len(results),
            success=len(results) > 0,
            error_message=attempt_summary,
            purpose="discovery",
        )

        # Get existing URLs for this product
        existing_candidates = self.db.list_candidates(product_id=product_id)
        existing_urls = {c.url for c in existing_candidates}
        global_exclusions = self.db.get_all_exclusion_keywords()

        # Determine brand identifiers to check in result titles.
        # All our products belong to AFC or its sub-brands.
        brand_identifiers = ["afc", "宇勝"]
        if product.brand:
            brand_identifiers.append(product.brand.lower())
        # Sub-brands
        prod_lower = product.product_name.lower()
        for sub in ("genki", "frura", "華舞", "爽快柑", "髮優", "究極", "菁鑽", "子供"):
            if sub in prod_lower:
                brand_identifiers.append(sub)

        new_count = 0
        skipped_count = 0
        for sr in results:
            if sr.url in existing_urls:
                continue

            # --- Brand filtering: reject results that don't mention AFC ---
            result_title_lower = (sr.product_name or "").lower()
            has_brand = any(b in result_title_lower for b in brand_identifiers)
            if not has_brand:
                LOGGER.info(
                    "品牌過濾(非AFC): [%s] → %s",
                    sr.product_name[:40], sr.url[:60],
                )
                skipped_count += 1
                continue

            # --- Name matching: compare search result title vs product name ---
            score = _best_discovery_match_score(
                product.product_name,
                product.keywords,
                sr.product_name,
            )
            if score < 50:
                LOGGER.info(
                    "跳過不相關結果 (score=%d): [%s] vs [%s] → %s",
                    score, product.product_name, sr.product_name[:40], sr.url[:60],
                )
                skipped_count += 1
                continue

            detected_platform = detect_platform(sr.url)
            platform = sr.platform if sr.platform not in {"", "manual", "other"} else detected_platform
            if is_disabled_platform(platform) or is_disabled_platform(detected_platform):
                LOGGER.info(
                    "Skipping permanently disabled platform result: [%s] %s",
                    sr.product_name[:40], sr.url[:60],
                )
                skipped_count += 1
                continue
            raw_data = dict(sr.raw_data or {})
            raw_data["discovery_match_score"] = score
            source_found_by = sr.source or "serpapi"
            coupon_source = any(
                is_coupon_source(value)
                for value in (
                    source_found_by,
                    sr.platform,
                    raw_data.get("source"),
                    raw_data.get("provider"),
                )
            )
            if coupon_source:
                source_found_by = "coupon"
                raw_data.update(
                    {
                        "exclusion_reason": "source_coupon",
                        "coupon_source": sr.source or sr.platform,
                    }
                )
            image_scan: ImageTextScanResult | None = None
            if platform.lower() == "momo" and not coupon_source:
                image_scan = self._scan_momo_official_image(sr.url)
                raw_data.update(image_scan.as_raw_data())

            # --- Global Exclusions ---
            is_excluded = coupon_source or any(ex.lower() in result_title_lower for ex in global_exclusions)
            status = "excluded" if is_excluded else "active"
            if coupon_source:
                LOGGER.info(
                    "Excluding coupon-source result: [%s] %s",
                    sr.product_name[:40], sr.url[:60],
                )
            if is_excluded:
                LOGGER.info(
                    "全域挑除關鍵字過濾: [%s] → %s",
                    sr.product_name[:40], sr.url[:60],
                )

            if image_scan and image_scan.matched:
                status = "excluded"
                raw_data["exclusion_reason"] = "MOMO 圖片含官方字樣"
                LOGGER.info(
                    "MOMO 圖片含官方字樣，排除候選連結：[%s] → %s",
                    sr.product_name[:40], sr.url[:80],
                )
            self.db.upsert_candidate(
                product_id=product_id,
                url=sr.url,
                platform=platform,
                title=sr.product_name,
                seller=sr.seller,
                source_found_by=source_found_by,
                status=status,
                last_price=sr.found_price,
                raw_data=raw_data or None,
            )
            new_count += 1
            if score < 70:
                LOGGER.info(
                    "低信心候選 (score=%d): [%s] → %s",
                    score, sr.product_name[:40], sr.url[:60],
                )

        if skipped_count:
            LOGGER.info("名稱比對過濾掉 %d 筆不相關結果", skipped_count)

        return {"found": len(results), "new": new_count, "existing": len(existing_urls), "skipped": skipped_count}

    def search_all_products(self, active_only: bool = True) -> dict[str, int]:
        """Search all products. Stops when budget exhausted."""
        products = self.db.list_products(active_only=active_only)
        total_found = 0
        total_new = 0
        searched = 0

        for product in products:
            try:
                self.budget.check_budget()
            except BudgetExhausted as exc:
                LOGGER.warning("預算不足，停止搜尋：%s", exc)
                break

            try:
                result = self.search_product(product.id)
                total_found += result["found"]
                total_new += result["new"]
                searched += 1
                LOGGER.info(
                    "[%d/%d] %s → found=%d new=%d",
                    searched, len(products), product.product_name,
                    result["found"], result["new"],
                )
            except Exception as exc:
                LOGGER.warning("搜尋失敗：%s - %s", product.product_name, exc)

        summary = {
            "searched": searched,
            "total_products": len(products),
            "total_found": total_found,
            "total_new": total_new,
            "budget": self.budget.usage_summary(),
        }
        LOGGER.info("搜尋完成：%s", summary)
        return summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="AFC 新連結發現")
    parser.add_argument(
        "--mode", choices=["weekly", "full", "single"], default="weekly",
        help="weekly=高優先, full=全部, single=單一商品",
    )
    parser.add_argument("--product-id", type=int, help="單一商品 ID（mode=single 時使用）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    root = Path(__file__).resolve().parent.parent.parent
    config = load_config(root / "config.yaml")
    db = Database(root / "data" / "price_monitor.db")

    # Auto-import if DB empty
    if not db.list_products():
        from src.csv_importer import full_import
        full_import(db, root)

    service = DiscoverySearchService(db, config, root)

    if args.mode == "single":
        if not args.product_id:
            print("Error: --product-id required for single mode")
            return 1
        result = service.search_product(args.product_id)
        print(f"搜尋結果：{result}")
    else:
        result = service.search_all_products()
        print(f"搜尋結果：{result}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
