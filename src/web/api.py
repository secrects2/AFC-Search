"""FastAPI web application for AFC Price Monitor dashboard.

Replaces the Flask-based dashboard with FastAPI + Jinja2.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.config import load_config
from src.database import Database
from src.csv_importer import import_products_csv
from src.services.budget_tracker import BudgetTracker

LOGGER = logging.getLogger(__name__)


def create_app(project_root: Path | None = None) -> FastAPI:
    root = project_root or Path(__file__).resolve().parents[2]
    config = load_config(root / "config.yaml")
    db = Database(root / "data" / "price_monitor.db")

    # Auto-import if DB is empty
    if not db.list_products():
        from src.csv_importer import full_import
        full_import(db, root)

    app = FastAPI(title="AFC 價格監控", docs_url=None, redoc_url=None)

    templates_dir = Path(__file__).parent / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Shared state for background runner
    runner_state: dict[str, Any] = {
        "running": False,
        "message": "尚未執行",
        "warning": "",
    }
    runner_lock = threading.Lock()

    def _render(request: Request, template: str, active: str = "", **kwargs: Any) -> HTMLResponse:
        """Render a template with common context."""
        ctx = {
            "request": request,
            "active": active,
            "run_status": dict(runner_state),
            **kwargs,
        }
        return templates.TemplateResponse(request=request, name=template, context=ctx)

    # -- Dashboard ----------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        stats = db.summary_stats()
        budget = BudgetTracker(db).usage_summary()
        return _render(
            request, "dashboard.html", active="dashboard",
            summary=stats,
            budget=budget,
            violations=db.get_snapshots(violation_only=True, limit=8),
            recent=db.get_snapshots(limit=12),
        )

    # -- Products -----------------------------------------------------------

    @app.get("/products", response_class=HTMLResponse)
    async def products_page(request: Request):
        products = db.list_products(active_only=False)
        return _render(request, "products.html", active="products", products=products)

    @app.post("/products/import")
    async def import_products(request: Request, file: UploadFile = File(...)):
        """Import products from uploaded CSV."""
        tmp_path = root / "data" / "upload_tmp.csv"
        content = await file.read()
        tmp_path.write_bytes(content)
        try:
            result = import_products_csv(db, tmp_path)
            message = f"匯入完成：{result['imported']} 筆商品"
        except Exception as exc:
            message = f"匯入失敗：{exc}"
        finally:
            tmp_path.unlink(missing_ok=True)
        return RedirectResponse(url=f"/products?message={message}", status_code=303)

    # -- Candidates ---------------------------------------------------------

    @app.get("/candidates", response_class=HTMLResponse)
    async def candidates_page(
        request: Request,
        product_id: int | None = None,
        status: str | None = None,
    ):
        candidates = db.list_candidates(product_id=product_id, status=status)
        products = db.list_products(active_only=False)
        return _render(
            request, "candidates.html", active="candidates",
            candidates=candidates,
            products=products,
            filter_product_id=product_id,
            filter_status=status,
        )

    @app.post("/candidates")
    async def add_candidate(
        request: Request,
        product_id: int = Form(...),
        url: str = Form(...),
        platform: str = Form(""),
        seller: str = Form(""),
    ):
        if not url.startswith(("http://", "https://")):
            return RedirectResponse(url="/candidates?error=URL必須以http開頭", status_code=303)
        if not platform:
            from src.search.serp_api import detect_platform
            platform = detect_platform(url)
        db.upsert_candidate(
            product_id=product_id,
            url=url,
            platform=platform,
            seller=seller,
            source_found_by="manual",
        )
        return RedirectResponse(url="/candidates", status_code=303)

    @app.post("/candidates/{candidate_id}/disable")
    async def disable_candidate(candidate_id: int):
        db.disable_candidate(candidate_id)
        return RedirectResponse(url="/candidates", status_code=303)

    @app.post("/candidates/{candidate_id}/recheck")
    async def recheck_candidate(candidate_id: int):
        from src.services.daily_monitor import DailyMonitorService
        service = DailyMonitorService(db, config, root)
        try:
            service.check_single_candidate(candidate_id)
        except Exception as exc:
            LOGGER.warning("Recheck failed: %s", exc)
        return RedirectResponse(url="/candidates", status_code=303)

    # -- Monitor Results ----------------------------------------------------

    @app.get("/monitor/results", response_class=HTMLResponse)
    async def monitor_results(
        request: Request,
        status: str | None = None,
    ):
        violation_only = status == "suspected_violation"
        snapshots = db.get_snapshots(violation_only=violation_only, limit=500)
        if status and status != "suspected_violation":
            snapshots = [s for s in snapshots if s.candidate_status == status]
        return _render(
            request, "results.html", active="results",
            snapshots=snapshots,
            filter_status=status or "",
            title="疑似破價" if violation_only else "監測結果",
        )

    @app.get("/monitor/violations", response_class=HTMLResponse)
    async def monitor_violations(request: Request):
        snapshots = db.get_snapshots(violation_only=True, limit=200)
        return _render(
            request, "results.html", active="violations",
            snapshots=snapshots,
            filter_status="suspected_violation",
            title="疑似破價",
        )

    # -- Run Monitor --------------------------------------------------------

    @app.post("/monitor/run")
    async def run_daily_monitor(request: Request):
        with runner_lock:
            if runner_state["running"]:
                return RedirectResponse(url="/", status_code=303)
            runner_state["running"] = True
            runner_state["message"] = "監測執行中..."

        def _run():
            try:
                from src.services.daily_monitor import DailyMonitorService
                from src.services.report_service import ReportService
                service = DailyMonitorService(db, config, root)
                result = service.run()
                ReportService(db, root).export_daily_report()
                ReportService(db, root).export_violations_report()
                with runner_lock:
                    runner_state["message"] = (
                        f"監測完成：{result.total_checked} 筆, "
                        f"{result.violations} 疑似破價"
                    )
            except Exception as exc:
                with runner_lock:
                    runner_state["message"] = f"監測失敗：{exc}"
            finally:
                with runner_lock:
                    runner_state["running"] = False

        threading.Thread(target=_run, daemon=True).start()
        return RedirectResponse(url="/", status_code=303)

    @app.post("/discovery/run")
    async def run_discovery(request: Request):
        with runner_lock:
            if runner_state["running"]:
                return RedirectResponse(url="/", status_code=303)
            runner_state["running"] = True
            runner_state["message"] = "搜尋新連結中..."

        def _run():
            try:
                from src.services.discovery_search import DiscoverySearchService
                service = DiscoverySearchService(db, config, root)
                result = service.search_all_products()
                with runner_lock:
                    runner_state["message"] = (
                        f"搜尋完成：{result['searched']} 商品, "
                        f"{result['total_new']} 新連結"
                    )
            except Exception as exc:
                with runner_lock:
                    runner_state["message"] = f"搜尋失敗：{exc}"
            finally:
                with runner_lock:
                    runner_state["running"] = False

        threading.Thread(target=_run, daemon=True).start()
        return RedirectResponse(url="/", status_code=303)

    # -- API Usage ----------------------------------------------------------

    @app.get("/api-usage", response_class=HTMLResponse)
    async def api_usage_page(request: Request):
        budget = BudgetTracker(db).usage_summary()
        logs = db.get_api_usage_logs(limit=100)
        return _render(
            request, "api_usage.html", active="api-usage",
            budget=budget,
            logs=logs,
        )

    # -- Downloads ----------------------------------------------------------

    @app.get("/downloads/{filename}")
    async def download_file(filename: str):
        allowed = {"daily_report.xlsx", "suspected_violations.xlsx"}
        if filename not in allowed:
            return HTMLResponse("Not found", status_code=404)
        path = root / "output" / filename
        if not path.exists():
            return HTMLResponse("報表尚未產出", status_code=404)
        return FileResponse(path, filename=filename)

    # -- Run Status API -----------------------------------------------------

    @app.get("/run-status")
    async def run_status():
        return runner_state

    return app
