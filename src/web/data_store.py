from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.loader import read_products
from src.monitor_catalog import (
    ACTIVE_STATUS,
    MISSING_PRICE_STATUS,
    PENDING_REVIEW_STATUS,
    REJECTED_STATUS,
    read_official_products,
    read_review_decisions,
    upsert_review_decision,
)


REPORT_DOWNLOADS = {
    "all_results.csv": "全部結果 CSV",
    "violations.csv": "疑似破價 CSV",
    "price_monitor_report.xlsx": "Excel 報表",
}

MONITOR_STATUS_LABELS = {
    ACTIVE_STATUS: "納入監控",
    PENDING_REVIEW_STATUS: "待人工確認",
    MISSING_PRICE_STATUS: "缺建議售價",
    REJECTED_STATUS: "不納入監控",
    "": "未分類",
}

REVIEW_DECISIONS = {"approved", PENDING_REVIEW_STATUS, MISSING_PRICE_STATUS, REJECTED_STATUS}


@dataclass(frozen=True)
class ManualLink:
    index: int
    product_name: str
    url: str
    platform: str


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            with path.open("r", encoding=encoding, newline="") as csv_file:
                return [dict(row) for row in csv.DictReader(csv_file)]
        except UnicodeDecodeError:
            continue
    return []


def latest_dir(project_root: Path) -> Path:
    return project_root / "output" / "latest"


def latest_report_path(project_root: Path, filename: str) -> Path:
    if filename not in REPORT_DOWNLOADS:
        raise ValueError("Unsupported report file")
    return latest_dir(project_root) / filename


def read_latest_results(project_root: Path) -> list[dict[str, str]]:
    return read_csv_rows(latest_report_path(project_root, "all_results.csv"))


def read_latest_violations(project_root: Path) -> list[dict[str, str]]:
    return read_csv_rows(latest_report_path(project_root, "violations.csv"))


def load_products_for_choices(project_root: Path) -> list[str]:
    try:
        products = read_products(project_root / "data" / "AFC商品.csv")
        return [product.product_name for product in products]
    except Exception:
        return []


def build_summary(project_root: Path) -> dict[str, Any]:
    rows = read_latest_results(project_root)
    violations = read_latest_violations(project_root)
    status_counts = Counter(row.get("violation_status", "") for row in rows)
    official_rows = read_official_products(project_root)
    official_status_counts = Counter(row.get("monitor_status", "") for row in official_rows)
    latest = latest_dir(project_root)
    report_files = []
    for filename, label in REPORT_DOWNLOADS.items():
        path = latest / filename
        report_files.append(
            {
                "filename": filename,
                "label": label,
                "exists": path.exists(),
                "size_kb": round(path.stat().st_size / 1024, 1) if path.exists() else 0,
            }
        )

    # AFC商品.csv product stats
    try:
        db_products = read_products(project_root / "data" / "AFC商品.csv")
        total_db_products = len(db_products)
        synced_products = sum(1 for p in db_products if p.official_image_hash)
    except Exception:
        total_db_products = 0
        synced_products = 0

    product_names = {row.get("product_name", "") for row in rows if row.get("product_name")}
    run_time = rows[0].get("run_time", "") if rows else ""
    return {
        "run_time": run_time,
        "total_products": len(product_names),
        "total_results": len(rows),
        "violations": len(violations),
        "missing_price": sum(1 for row in rows if not row.get("found_price")),
        "needs_review": status_counts.get("needs_review", 0),
        "ok": status_counts.get("ok", 0),
        "search_failed": status_counts.get("search_failed", 0),
        "total_db_products": total_db_products,
        "synced_products": synced_products,
        "official_total": len(official_rows),
        "official_active": official_status_counts.get(ACTIVE_STATUS, 0),
        "official_pending_review": official_status_counts.get(PENDING_REVIEW_STATUS, 0),
        "official_missing_price": official_status_counts.get(MISSING_PRICE_STATUS, 0),
        "review_decisions": len(read_review_decisions(project_root)),
        "latest_exists": latest.exists(),
        "report_files": report_files,
    }


def _score_value(row: dict[str, str]) -> int:
    try:
        return int(float(row.get("match_score", "0") or 0))
    except ValueError:
        return 0


def read_official_review_rows(project_root: Path, status: str = "") -> list[dict[str, str]]:
    status = status.strip()
    rows = []
    for row in read_official_products(project_root):
        if status and row.get("monitor_status") != status:
            continue
        enriched = dict(row)
        enriched["status_label"] = MONITOR_STATUS_LABELS.get(
            enriched.get("monitor_status", ""), enriched.get("monitor_status", "") or "未分類"
        )
        rows.append(enriched)

    status_order = {
        PENDING_REVIEW_STATUS: 0,
        MISSING_PRICE_STATUS: 1,
        ACTIVE_STATUS: 2,
        REJECTED_STATUS: 3,
    }
    rows.sort(
        key=lambda row: (
            status_order.get(row.get("monitor_status", ""), 9),
            -_score_value(row),
            row.get("official_product_name", ""),
        )
    )
    return rows


def save_review_decision(
    project_root: Path,
    official_product_url: str,
    official_product_name: str,
    decision: str,
    matched_db_product_name: str,
    reviewer: str,
    note: str,
) -> None:
    official_product_url = official_product_url.strip()
    official_product_name = official_product_name.strip()
    decision = decision.strip()
    matched_db_product_name = matched_db_product_name.strip()
    if not official_product_url or not official_product_name:
        raise ValueError("缺少官網商品資料，無法寫入審核結果")
    if decision not in REVIEW_DECISIONS:
        raise ValueError("不支援的審核決定")
    if decision == "approved" and not matched_db_product_name:
        raise ValueError("核准監控時必須選擇對應的產品名稱")

    upsert_review_decision(
        project_root=project_root,
        products_path=project_root / "data" / "AFC商品.csv",
        official_product_url=official_product_url,
        official_product_name=official_product_name,
        decision=decision,
        matched_db_product_name=matched_db_product_name,
        reviewer=reviewer.strip(),
        note=note.strip(),
    )


def filter_results(
    rows: list[dict[str, str]],
    query: str = "",
    platform: str = "",
    status: str = "",
) -> list[dict[str, str]]:
    query = query.strip().lower()
    platform = platform.strip().lower()
    status = status.strip()
    filtered = rows
    if query:
        filtered = [
            row
            for row in filtered
            if query in row.get("product_name", "").lower()
            or query in row.get("found_title", "").lower()
        ]
    if platform:
        filtered = [row for row in filtered if row.get("platform", "").lower() == platform]
    if status:
        filtered = [row for row in filtered if row.get("violation_status", "") == status]
    return filtered


def manual_links_path(project_root: Path) -> Path:
    return project_root / "data" / "manual_links.csv"


def read_manual_links(project_root: Path) -> list[ManualLink]:
    rows = read_csv_rows(manual_links_path(project_root))
    links = []
    for index, row in enumerate(rows):
        links.append(
            ManualLink(
                index=index,
                product_name=(row.get("product_name") or "").strip(),
                url=(row.get("url") or "").strip(),
                platform=(row.get("platform") or "manual").strip() or "manual",
            )
        )
    return [link for link in links if link.product_name and link.url]


def write_manual_links(project_root: Path, links: list[ManualLink]) -> None:
    path = manual_links_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=["product_name", "url", "platform"])
        writer.writeheader()
        for link in links:
            writer.writerow(
                {
                    "product_name": link.product_name,
                    "url": link.url,
                    "platform": link.platform,
                }
            )


def upsert_manual_link(project_root: Path, product_name: str, url: str, platform: str) -> None:
    product_name = product_name.strip()
    url = url.strip()
    platform = platform.strip() or "manual"
    if not product_name or not url:
        raise ValueError("商品名稱與網址不可空白")
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("網站管理介面只接受 http 或 https 商品頁網址")

    links = read_manual_links(project_root)
    normalized_url = url.lower()
    updated = False
    next_links: list[ManualLink] = []
    for link in links:
        if link.product_name == product_name and link.url.lower() == normalized_url:
            next_links.append(ManualLink(link.index, product_name, url, platform))
            updated = True
        else:
            next_links.append(link)
    if not updated:
        next_links.append(ManualLink(len(next_links), product_name, url, platform))
    write_manual_links(project_root, next_links)


def delete_manual_link(project_root: Path, index: int) -> None:
    links = [link for link in read_manual_links(project_root) if link.index != index]
    write_manual_links(project_root, links)


def read_log_tail(project_root: Path, filename: str, max_lines: int = 80) -> list[str]:
    if filename not in {"run.log", "scheduler.log", "dashboard_run.log"}:
        return []
    path = project_root / "logs" / filename
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    return lines[-max_lines:]
