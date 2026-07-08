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
from src.database import Database
from src.search.serp_api import detect_platform
from src.search.search_api import build_chain_provider
from src.services.budget_tracker import BudgetExhausted, BudgetTracker

LOGGER = logging.getLogger(__name__)


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

        # Build a temporary Product object for the search provider
        from src.loader import Product
        temp_product = Product(
            suggested_price=product.suggested_price or 0,
            product_name=product.product_name,
            row_index=product.id,
            raw_suggested_price=str(product.suggested_price or ""),
        )

        results = provider.search(temp_product, int(self.config.max_results_per_product))

        # Log API usage
        self.db.log_api_usage(
            provider=provider.last_provider,
            query=product.product_name,
            result_count=len(results),
            success=len(results) > 0,
            purpose="discovery",
        )

        # Get existing URLs for this product
        existing_candidates = self.db.list_candidates(product_id=product_id)
        existing_urls = {c.url for c in existing_candidates}

        from src.matcher import match_score

        new_count = 0
        skipped_count = 0
        for sr in results:
            if sr.url in existing_urls:
                continue

            # Name matching: compare search result title vs product name
            score = match_score(product.product_name, sr.product_name)
            if score < 50:
                LOGGER.info(
                    "跳過不相關結果 (score=%d): [%s] vs [%s] → %s",
                    score, product.product_name, sr.product_name[:40], sr.url[:60],
                )
                skipped_count += 1
                continue

            platform = detect_platform(sr.url)
            self.db.upsert_candidate(
                product_id=product_id,
                url=sr.url,
                platform=platform,
                title=sr.product_name,
                source_found_by=sr.source or "serpapi",
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
