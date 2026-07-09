"""FastAPI web application for AFC Price Monitor dashboard.

Replaces the Flask-based dashboard with FastAPI + Jinja2.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone, timedelta
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


def format_tz(iso_str: str) -> str:
    """Convert ISO UTC string to Asia/Taipei (+8) and format."""
    if not iso_str or iso_str == "-":
        return "-"
    try:
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_tw = dt.astimezone(timezone(timedelta(hours=8)))
        return dt_tw.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_str[:16]

def status_label(status: str) -> str:
    mapping = {
        "normal": "正常",
        "suspected_violation": "疑似破價",
        "price_unknown": "未抓到價格",
        "error": "錯誤",
        "blocked": "遭到阻擋",
    }
    return mapping.get(status, status) if status else ""

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
    templates.env.filters["fromjson"] = json.loads
    templates.env.filters["format_tz"] = format_tz
    templates.env.filters["status_label"] = status_label

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Serve official product images as static files
    images_dir = root / "data" / "official_images"
    if images_dir.exists():
        app.mount("/product-images", StaticFiles(directory=str(images_dir)), name="product-images")

    # Shared state for background runner
    runner_state: dict[str, Any] = {
        "running": False,
        "message": "尚未執行",
        "warning": "",
        "progress": 0,
        "total": 0,
        "percent": 0,
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
    async def products_page(request: Request, show_all: int = 0):
        products = db.list_products(active_only=not bool(show_all))
        return _render(
            request, "products.html", active="products",
            products=products, show_all=bool(show_all),
        )

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

    @app.post("/products/add")
    async def add_product(
        request: Request,
        product_name: str = Form(...),
        suggested_price: float = Form(0),
        brand: str = Form(""),
        keywords: str = Form(""),
        exclude_keywords: str = Form(""),
        priority: int = Form(0),
    ):
        """Add a new product."""
        if not product_name.strip():
            return RedirectResponse(url="/products?message=商品名稱不可為空", status_code=303)
        db.upsert_product(
            product_name=product_name.strip(),
            suggested_price=suggested_price or None,
            brand=brand.strip(),
            keywords=keywords.strip(),
            exclude_keywords=exclude_keywords.strip(),
            priority=priority,
            is_active=True,
        )
        return RedirectResponse(url=f"/products?message=已新增商品：{product_name}", status_code=303)

    @app.post("/products/{product_id}/update")
    async def update_product(
        product_id: int,
        product_name: str = Form(...),
        suggested_price: float = Form(0),
        brand: str = Form(""),
        keywords: str = Form(""),
        exclude_keywords: str = Form(""),
        priority: int = Form(0),
        is_active: int = Form(0),
    ):
        """Update an existing product."""
        existing = db.get_product(product_id)
        if not existing:
            return RedirectResponse(url="/products?message=找不到商品", status_code=303)
        db.upsert_product(
            product_name=product_name.strip(),
            suggested_price=suggested_price or None,
            brand=brand.strip(),
            keywords=keywords.strip(),
            exclude_keywords=exclude_keywords.strip(),
            priority=priority,
            is_active=bool(is_active),
            official_image_url=existing.official_image_url,
            official_image_path=existing.official_image_path,
            official_image_hash=existing.official_image_hash,
        )
        return RedirectResponse(url="/products?message=已更新商品", status_code=303)

    @app.post("/products/{product_id}/delete")
    async def delete_product(product_id: int):
        """Delete a product and all related data."""
        product = db.get_product(product_id)
        name = product.product_name if product else "Unknown"
        deleted = db.delete_product(product_id)
        if deleted:
            msg = f"已刪除商品：{name}"
        else:
            msg = "找不到商品"
        return RedirectResponse(url=f"/products?message={msg}", status_code=303)

    @app.post("/products/{product_id}/toggle")
    async def toggle_product(product_id: int):
        """Toggle a product's active status."""
        product = db.get_product(product_id)
        if not product:
            return RedirectResponse(url="/products?message=找不到商品", status_code=303)
        db.upsert_product(
            product_name=product.product_name,
            suggested_price=product.suggested_price,
            brand=product.brand,
            keywords=product.keywords,
            exclude_keywords=product.exclude_keywords,
            priority=product.priority,
            is_active=not product.is_active,
            official_image_url=product.official_image_url,
            official_image_path=product.official_image_path,
            official_image_hash=product.official_image_hash,
        )
        status = "啟用" if not product.is_active else "停用"
        return RedirectResponse(url=f"/products?message=已{status}：{product.product_name}", status_code=303)

    # -- Candidates ---------------------------------------------------------

    @app.get("/candidates", response_class=HTMLResponse)
    async def candidates_page(
        request: Request,
        product_id: int | None = None,
        status: str | None = None,
    ):
        candidates = db.list_candidates(
            product_id=product_id,
            status=status,
            include_excluded=bool(status),
        )
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

    # -- Global Exclusions --------------------------------------------------
    @app.get("/exclusions", response_class=HTMLResponse)
    async def exclusions_page(request: Request):
        exclusions = db.list_global_exclusions()
        return _render(
            request, "exclusions.html", active="exclusions",
            exclusions=exclusions
        )
        
    @app.post("/exclusions")
    async def add_exclusion(keyword: str = Form(...)):
        if keyword and keyword.strip():
            db.add_global_exclusion(keyword.strip())
            # Retroactively exclude existing candidates
            count = db.retroactively_exclude_candidates(keyword.strip())
            LOGGER.info("Added global exclusion '%s', retro-excluded %d candidates", keyword.strip(), count)
        return RedirectResponse(url="/exclusions", status_code=303)
        
    @app.post("/exclusions/{exclusion_id}/delete")
    async def delete_exclusion(exclusion_id: int):
        db.remove_global_exclusion(exclusion_id)
        return RedirectResponse(url="/exclusions", status_code=303)

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
            runner_state["progress"] = 0
            runner_state["total"] = 0
            runner_state["percent"] = 0

        def _progress(current: int, total: int, message: str):
            with runner_lock:
                runner_state["progress"] = current
                runner_state["total"] = total
                runner_state["percent"] = int(current / total * 100) if total > 0 else 0
                runner_state["message"] = message

        def _run():
            try:
                from src.services.daily_monitor import DailyMonitorService
                from src.services.report_service import ReportService
                service = DailyMonitorService(db, config, root)
                result = service.run(progress_callback=_progress)
                ReportService(db, root).export_daily_report()
                ReportService(db, root).export_violations_report()
                with runner_lock:
                    runner_state["message"] = (
                        f"✅ 監測完成：{result.total_checked} 筆, "
                        f"{result.violations} 疑似破價, "
                        f"{result.price_unknown} 未知價格"
                    )
                    runner_state["progress"] = result.total_checked
                    runner_state["total"] = result.total_checked
                    runner_state["percent"] = 100
            except Exception as exc:
                with runner_lock:
                    runner_state["message"] = f"❌ 監測失敗：{exc}"
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

                # Get products to show total count
                products = db.list_products(active_only=True)
                total = len(products)
                total_new = 0
                searched = 0

                with runner_lock:
                    runner_state["message"] = f"搜尋新連結中... (0/{total})"

                for i, product in enumerate(products, 1):
                    with runner_lock:
                        runner_state["message"] = (
                            f"搜尋中 [{i}/{total}] {product.product_name[:20]}..."
                        )
                    try:
                        from src.services.budget_tracker import BudgetExhausted
                        service.budget.check_budget()
                        result = service.search_product(product.id)
                        total_new += result["new"]
                        searched += 1
                    except BudgetExhausted as exc:
                        LOGGER.warning("預算不足，停止搜尋：%s", exc)
                        with runner_lock:
                            runner_state["warning"] = f"預算不足，已搜尋 {searched}/{total}"
                        break
                    except Exception as exc:
                        LOGGER.warning("搜尋失敗：%s - %s", product.product_name, exc)

                with runner_lock:
                    runner_state["message"] = (
                        f"搜尋完成：{searched}/{total} 商品, "
                        f"{total_new} 筆新連結"
                    )
            except Exception as exc:
                LOGGER.exception("Discovery search failed")
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
