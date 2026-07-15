from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import load_config
from src.image_text import scan_image_urls_for_text
from src.image_matcher import best_image_match
from src.loader import Product, read_products
from src.matcher import classify_match, match_score
from src.monitor_catalog import load_monitor_products
from src.parsers import get_parser
from src.reporter import write_reports
from src.search import ManualSearchProvider
from src.search.search_api import build_chain_provider
from src.utils import copy_latest_reports, ensure_dir, resolve_project_path, run_timestamp, setup_logging


LOGGER = logging.getLogger("price_monitor")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AFC 電商價格監控系統")
    parser.add_argument("--products", default="data/AFC商品.csv", help="商品主檔 CSV 路徑")
    parser.add_argument("--manual-links", default="", help="手動連結 CSV 路徑")
    parser.add_argument("--config", default="config.yaml", help="設定檔路徑")
    parser.add_argument("--scheduled", action="store_true", help="Windows 工作排程器模式")
    parser.add_argument(
        "--disable-dead-find",
        action="store_true",
        help="清理資料庫，將過期的 FindPrice 網址標記為 source_dead",
    )
    return parser.parse_args(argv)


def make_result_row(
    run_time: str,
    product: Product,
    platform: str,
    url: str,
    found_title: str = "",
    found_price: float | None = None,
    score: int = 0,
    violation_status: str = "search_failed",
    seller: str = "",
    screenshot_path: str = "",
    parse_status: str = "search_failed",
    ocr_status: str = "disabled",
    evidence_text: str = "",
    official_image_url: str = "",
    image_match_status: str = "",
    image_match_score: int = 0,
) -> dict[str, Any]:
    price_gap = None
    price_gap_percent = None
    if found_price is not None:
        price_gap = round(found_price - product.suggested_price, 2)
        if product.suggested_price:
            price_gap_percent = round(price_gap / product.suggested_price * 100, 2)

    return {
        "run_time": run_time,
        "platform": platform,
        "product_name": product.product_name,
        "sku_code": product.sku_code,
        "suggested_price": product.suggested_price,
        "found_title": found_title,
        "found_price": found_price,
        "price_gap": price_gap,
        "price_gap_percent": price_gap_percent,
        "match_score": score,
        "violation_status": violation_status,
        "seller": seller,
        "url": url,
        "screenshot_path": screenshot_path,
        "parse_status": parse_status,
        "ocr_status": ocr_status,
        "evidence_text": evidence_text,
        "official_image_url": official_image_url,
        "image_match_status": image_match_status,
        "image_match_score": image_match_score,
    }


def determine_violation_status(
    found_price: float | None,
    suggested_price: float,
    score: int,
    threshold: int,
    tolerance: float,
    parse_status: str,
) -> str:
    if found_price is None:
        return parse_status or "price_not_found"
    match_class = classify_match(score, threshold)
    if match_class == "unmatched":
        return "low_match_score"
    if match_class == "needs_review":
        return "needs_review"
    if found_price < suggested_price - tolerance:
        return "suspected_violation"
    return "ok"


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path(__file__).resolve().parent
    setup_logging(project_root / "logs")

    run_time = datetime.now().isoformat(timespec="seconds")
    config = load_config(resolve_project_path(project_root, args.config))
    output_root = ensure_dir(project_root / "output")
    run_output_dir = ensure_dir(output_root / run_timestamp())
    ensure_dir(run_output_dir / "screenshots")

    try:
        product_path = resolve_project_path(project_root, args.products)
        catalog = load_monitor_products(project_root, product_path)
        products = catalog.products
    except Exception as exc:
        LOGGER.exception("重大錯誤：商品主檔讀取失敗：%s", exc)
        return 2

    # Check for disable-dead-find
    if args.disable_dead_find:
        LOGGER.info("執行過期 FindPrice 網址清理...")
        db_path = resolve_project_path(project_root, config.database_path)
        db = Database(db_path)
        count = db.disable_obsolete_findprice_urls()
        LOGGER.info("清理完成，共停用 %d 筆過期網址。", count)
        return 0

    manual_path = (
        resolve_project_path(project_root, args.manual_links)
        if args.manual_links
        else project_root / "data" / "manual_links.csv"
    )
    manual_provider = ManualSearchProvider(manual_path)

    chain_provider = build_chain_provider(
        serpapi_key=config.serpapi_api_key,
        brave_key=config.brave_api_key,
        platforms=config.platforms,
        cache_path=project_root / "data" / "search_cache.json",
        cache_hours=int(config.search_cache_hours),
        timeout=float(config.request_timeout_seconds),
    )

    rows: list[dict[str, Any]] = []

    LOGGER.info(
        "開始監控：products=%s manual_links=%s search=%s source=%s total_catalog=%s",
        len(products),
        len(manual_provider.links),
        "enabled" if chain_provider.enabled else "disabled",
        catalog.source,
        catalog.total_count,
    )

    for product in products:
        try:
            # Collect links from manual + auto search, deduplicate by URL
            manual_links = manual_provider.search(product, config.max_results_per_product)
            seen_urls: set[str] = {link.url for link in manual_links}
            all_links = list(manual_links)

            if chain_provider.enabled:
                search_links = chain_provider.search(product, config.max_results_per_product)
                for link in search_links:
                    if link.url not in seen_urls:
                        seen_urls.add(link.url)
                        all_links.append(link)

            if not all_links:
                rows.append(
                    make_result_row(
                        run_time,
                        product,
                        platform="none",
                        url="",
                        violation_status="search_failed",
                        parse_status="search_failed",
                        evidence_text="No links found. Add manual links or enable Google CSE.",
                    )
                )
                continue

            for link in all_links:
                parser = get_parser(link.platform, link.url, config)
                output = parser.parse(link.url, run_output_dir)
                title_for_match = output.title or link.product_name
                score = match_score(product.product_name, title_for_match)
                if (output.platform or link.platform).lower() == "momo" and config.enable_ocr:
                    image_scan = scan_image_urls_for_text(
                        output.image_urls or [],
                        marker="官方",
                        timeout_seconds=int(config.request_timeout_seconds),
                    )
                    output.raw_data.update(image_scan.as_raw_data())
                    if image_scan.matched:
                        LOGGER.info(
                            "MOMO 圖片含官方字樣，排除報表結果：%s",
                            link.url[:100],
                        )
                        continue
                if (
                    config.enable_image_match
                    and score < int(config.match_threshold)
                    and product.official_image_hash
                ):
                    image_result = best_image_match(
                        product,
                        output.image_urls or [],
                        int(config.image_match_threshold),
                        int(config.request_timeout_seconds),
                    )
                    output.image_match_status = image_result.status
                    output.image_match_score = image_result.score
                    if image_result.status == "matched":
                        score = max(score, int(config.match_threshold))
                violation_status = determine_violation_status(
                    output.price,
                    product.suggested_price,
                    score,
                    int(config.match_threshold),
                    float(config.price_tolerance),
                    output.parse_status,
                )
                rows.append(
                    make_result_row(
                        run_time,
                        product,
                        platform=output.platform or link.platform,
                        url=link.url,
                        found_title=output.title,
                        found_price=output.price,
                        score=score,
                        violation_status=violation_status,
                        seller=output.seller,
                        screenshot_path=output.screenshot_path,
                        parse_status=output.parse_status,
                        ocr_status=output.ocr_status,
                        evidence_text=output.evidence_text,
                        official_image_url=product.official_image_url,
                        image_match_status=output.image_match_status,
                        image_match_score=output.image_match_score,
                    )
                )
                if config.request_delay_seconds and link.url.lower().startswith(("http://", "https://")):
                    time.sleep(float(config.request_delay_seconds))
        except Exception as exc:
            LOGGER.exception("商品處理失敗：%s", product.product_name)
            rows.append(
                make_result_row(
                    run_time,
                    product,
                    platform="unknown",
                    url="",
                    violation_status="search_failed",
                    parse_status="search_failed",
                    evidence_text=str(exc),
                )
            )

    summary = {
        "run_time": run_time,
        "scheduled": args.scheduled,
        "total_products": len(products),
        "total_results": len(rows),
        "violations": sum(1 for row in rows if row.get("violation_status") == "suspected_violation"),
        "missing_price": sum(1 for row in rows if row.get("found_price") in ("", None)),
        "manual_links": len(manual_provider.links),
        "product_source": catalog.source,
        "active_products": catalog.active_count,
        "catalog_products": catalog.total_count,
        "match_threshold": config.match_threshold,
        "price_tolerance": config.price_tolerance,
        "output_dir": str(run_output_dir),
    }

    try:
        write_reports(rows, run_output_dir, summary)
        copy_latest_reports(run_output_dir, output_root)
    except Exception as exc:
        LOGGER.exception("重大錯誤：報表輸出失敗：%s", exc)
        return 3

    LOGGER.info("監控完成：%s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(run())
