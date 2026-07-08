"""AFC Price Monitor MCP Server — AI operation layer.

Wraps existing services for safe AI access.
All calls are logged to logs/mcp_tool_calls.log.

Usage:
    python mcp_server.py

Note: Requires 'mcp' package. Install with: pip install mcp
This is optional and will gracefully fail if 'mcp' is not installed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent
MCP_LOG = PROJECT_ROOT / "logs" / "mcp_tool_calls.log"


def _log_call(tool_name: str, args: dict, result: str) -> None:
    """Log MCP tool call to file."""
    MCP_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "args": {k: v for k, v in args.items() if k not in ("api_key", "token", "cookie")},
        "result_preview": result[:500],
    }
    with MCP_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _get_db():
    from src.database import Database
    return Database(PROJECT_ROOT / "data" / "price_monitor.db")


def _get_config():
    from src.config import load_config
    return load_config(PROJECT_ROOT / "config.yaml")


# ---------------------------------------------------------------------------
# MCP Tools (functions that can be called by AI)
# ---------------------------------------------------------------------------

def import_products(csv_path: str = "data/AFC商品.csv") -> str:
    """匯入商品 CSV 到資料庫。"""
    from src.csv_importer import import_products_csv
    db = _get_db()
    path = PROJECT_ROOT / csv_path
    result = import_products_csv(db, path)
    msg = json.dumps(result, ensure_ascii=False)
    _log_call("import_products", {"csv_path": csv_path}, msg)
    return msg


def list_products(active_only: bool = True) -> str:
    """列出所有商品。"""
    db = _get_db()
    products = db.list_products(active_only=active_only)
    data = [{"id": p.id, "name": p.product_name, "price": p.suggested_price,
             "brand": p.brand, "active": p.is_active} for p in products]
    msg = json.dumps(data, ensure_ascii=False)
    _log_call("list_products", {"active_only": active_only}, msg)
    return msg


def run_daily_monitor() -> str:
    """執行每日監測（只查已知 URL）。"""
    from src.services.daily_monitor import DailyMonitorService
    from src.services.report_service import ReportService
    db = _get_db()
    config = _get_config()
    service = DailyMonitorService(db, config, PROJECT_ROOT)
    result = service.run()
    ReportService(db, PROJECT_ROOT).export_daily_report()
    ReportService(db, PROJECT_ROOT).export_violations_report()
    msg = json.dumps(result.__dict__, ensure_ascii=False)
    _log_call("run_daily_monitor", {}, msg)
    return msg


def run_discovery_search(product_id: int | None = None) -> str:
    """執行新連結發現搜尋。"""
    from src.services.discovery_search import DiscoverySearchService
    db = _get_db()
    config = _get_config()
    service = DiscoverySearchService(db, config, PROJECT_ROOT)
    if product_id:
        result = service.search_product(product_id)
    else:
        result = service.search_all_products()
    msg = json.dumps(result, ensure_ascii=False)
    _log_call("run_discovery_search", {"product_id": product_id}, msg)
    return msg


def list_candidates(product_id: int | None = None, status: str | None = None) -> str:
    """列出候選連結。"""
    db = _get_db()
    candidates = db.list_candidates(product_id=product_id, status=status)
    data = [{"id": c.id, "product": c.product_name, "url": c.url,
             "platform": c.platform, "price": c.last_price, "status": c.status}
            for c in candidates]
    msg = json.dumps(data, ensure_ascii=False)
    _log_call("list_candidates", {"product_id": product_id, "status": status}, msg)
    return msg


def add_candidate_url(product_id: int, url: str, platform: str = "") -> str:
    """手動新增候選 URL。"""
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": "URL must start with http"})
    from src.search.serp_api import detect_platform
    db = _get_db()
    plat = platform or detect_platform(url)
    cid = db.upsert_candidate(product_id=product_id, url=url, platform=plat, source_found_by="mcp")
    msg = json.dumps({"candidate_id": cid, "platform": plat})
    _log_call("add_candidate_url", {"product_id": product_id, "url": url}, msg)
    return msg


def disable_candidate_url(candidate_id: int) -> str:
    """停用候選 URL。"""
    db = _get_db()
    db.disable_candidate(candidate_id)
    msg = json.dumps({"disabled": candidate_id})
    _log_call("disable_candidate_url", {"candidate_id": candidate_id}, msg)
    return msg


def list_violations(days: int = 7) -> str:
    """列出疑似破價。"""
    db = _get_db()
    snapshots = db.get_snapshots(violation_only=True, limit=100)
    data = [{"product": s.product_name, "price": s.price, "suggested": s.suggested_price,
             "diff": s.price_diff, "url": s.url, "time": s.checked_at}
            for s in snapshots]
    msg = json.dumps(data, ensure_ascii=False)
    _log_call("list_violations", {"days": days}, msg)
    return msg


def export_report() -> str:
    """產出 Excel 報表。"""
    from src.services.report_service import ReportService
    db = _get_db()
    svc = ReportService(db, PROJECT_ROOT)
    daily = svc.export_daily_report()
    violations = svc.export_violations_report()
    msg = json.dumps({"daily": str(daily), "violations": str(violations)})
    _log_call("export_report", {}, msg)
    return msg


def get_api_usage() -> str:
    """取得 API 使用量。"""
    from src.services.budget_tracker import BudgetTracker
    db = _get_db()
    budget = BudgetTracker(db).usage_summary()
    msg = json.dumps(budget)
    _log_call("get_api_usage", {}, msg)
    return msg


# ---------------------------------------------------------------------------
# MCP Server entry point
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
    except ImportError:
        print("MCP server 需要安裝 mcp 套件：pip install mcp")
        print("MCP tools 已定義，可直接作為 Python 函式呼叫。")
        return

    server = Server("afc-price-monitor")

    # Register all tools
    for name, func in [
        ("import_products", import_products),
        ("list_products", list_products),
        ("run_daily_monitor", run_daily_monitor),
        ("run_discovery_search", run_discovery_search),
        ("list_candidates", list_candidates),
        ("add_candidate_url", add_candidate_url),
        ("disable_candidate_url", disable_candidate_url),
        ("list_violations", list_violations),
        ("export_report", export_report),
        ("get_api_usage", get_api_usage),
    ]:
        server.tool()(func)

    import asyncio
    asyncio.run(stdio_server(server))


if __name__ == "__main__":
    main()
