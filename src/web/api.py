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

def snapshot_status(snapshot: Any) -> str:
    """Return the user-facing status for a monitoring snapshot."""
    if isinstance(snapshot, dict):
        if snapshot.get("final_status") == "needs_review":
            return "needs_review"
        return str(snapshot.get("candidate_status") or snapshot.get("violation_status") or "")
    if getattr(snapshot, "final_status", "") == "needs_review":
        return "needs_review"
    return getattr(snapshot, "candidate_status", "") or ""

def status_label(status: str) -> str:
    from markupsafe import Markup
    mapping = {
        "needs_review": "待人工確認",
        "normal": "正常",
        "suspected_violation": "監控中",
        "price_unknown": "未抓到價格",
        "error": "錯誤",
        "blocked": "遭到阻擋",
        "takedown_notified": "取消監控",
    }
    label = mapping.get(status, status) if status else ""
    color = "#333"
    if status == "suspected_violation": color = "#d93025"
    elif status == "normal": color = "#166534"
    elif status == "needs_review": color = "#b45309"
    elif status == "takedown_notified": color = "#b45309"
    elif status in ("error", "blocked"): color = "#d93025"
    return Markup(f'<span style="color: {color}; font-weight: 600;">{label}</span>')

def format_source_label(source: str) -> str:
    from markupsafe import Markup
    if not source:
        return "-"
    source = source.lower()
    mapping = {
        "feebee": "飛比",
        "findprice": "比價王",
        "findprice_shopee": "比價王(蝦皮)",
        "serpapi": "Google搜尋",
        "serper": "Google搜尋",
        "brave": "Brave搜尋",
        "manual": "手動新增",
        "direct_html": "網頁爬取",
        "crawler": "自動爬蟲",
        "mcp": "AI 代理",
        "shopee": "蝦皮搜尋",
        "biggo": "BigGo",
        "lbj": "歷史價格",
        "fallback": "備援系統"
    }
    name = mapping.get(source, source)
    
    color = "#657184" # default gray
    if source == "feebee":
        color = "#166534" # green
    elif source in ("biggo", "lbj"):
        color = "#0369a1" # dark blue
    elif source == "fallback":
        color = "#b45309" # orange
    elif "findprice" in source:
        color = "#b45309" # orange/yellow
    elif source in ("serpapi", "serper", "brave"):
        color = "#1a73e8" # blue
    elif source == "manual":
        color = "#657184" # gray
    elif source in ("direct_html", "crawler", "shopee"):
        color = "#6b21a8" # purple
    elif source == "mcp":
        color = "#d93025" # red
        
    return Markup(f'<span style="color: {color}; font-weight: 600;">{name}</span>')

def canonical_url(url: str) -> str:
    """Rewrite URLs to canonical forms to avoid browser or redirect blocks."""
    if not url:
        return ""
    # Rewrite Shopee SEO URL to canonical /product/shop_id/item_id
    # e.g., https://shopee.tw/xxx-i.1146071435.28055610078 -> https://shopee.tw/product/1146071435/28055610078
    if "shopee.tw" in url:
        import re
        m = re.search(r'-i\.(\d+)\.(\d+)', url)
        if m:
            return f"https://shopee.tw/product/{m.group(1)}/{m.group(2)}"
    return url

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
    templates.env.filters["snapshot_status"] = snapshot_status
    templates.env.filters["format_source"] = format_source_label
    templates.env.filters["canonical_url"] = canonical_url

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
        # Exclude takedown_notified from dashboard lists
        all_violations = db.get_snapshots(violation_only=True, limit=50)
        violations = [s for s in all_violations if s.candidate_status != "takedown_notified"][:8]
        all_recent = db.get_snapshots(limit=50)
        recent = [s for s in all_recent if s.candidate_status != "takedown_notified"][:12]
        return _render(
            request, "dashboard.html", active="dashboard",
            summary=stats,
            budget=budget,
            violations=violations,
            recent=recent,
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
    async def disable_candidate(candidate_id: int, request: Request):
        db.disable_candidate(candidate_id)
        referer = request.headers.get("referer") or "/candidates"
        return RedirectResponse(url=referer, status_code=303)

    @app.post("/candidates/{candidate_id}/restore")
    async def restore_candidate(candidate_id: int, request: Request):
        db.update_candidate_status(candidate_id, "normal")
        referer = request.headers.get("referer") or "/candidates"
        return RedirectResponse(url=referer, status_code=303)

    @app.post("/candidates/{candidate_id}/recheck")
    async def recheck_candidate(candidate_id: int, request: Request):
        from src.config import load_config, load_env_file
        load_env_file(root / ".env")
        fresh_config = load_config(root / "config.yaml")

        from src.services.daily_monitor import DailyMonitorService
        service = DailyMonitorService(db, fresh_config, root)
        
        referer = request.headers.get("referer") or "/candidates"
        if "#" in referer:
            referer = referer.split("#")[0]
            
        import urllib.parse
        try:
            service.check_single_candidate(candidate_id)
        except Exception as exc:
            LOGGER.warning("Recheck failed: %s", exc)
            err_msg = urllib.parse.quote(f"重查失敗：{exc}")
            if "?" in referer:
                return RedirectResponse(url=f"{referer}&error={err_msg}#row-{candidate_id}", status_code=303)
            return RedirectResponse(url=f"{referer}?error={err_msg}#row-{candidate_id}", status_code=303)
            
        success_msg = urllib.parse.quote("重查完畢")
        if "?" in referer:
            # strip existing message if any?
            import re
            referer = re.sub(r'([?&])message=[^&]*&?', r'\1', referer)
            referer = re.sub(r'([?&])error=[^&]*&?', r'\1', referer)
            if referer.endswith('?') or referer.endswith('&'):
                referer = referer[:-1]
            sep = "&" if "?" in referer else "?"
            return RedirectResponse(url=f"{referer}{sep}message={success_msg}#row-{candidate_id}", status_code=303)
            
        return RedirectResponse(url=f"{referer}?message={success_msg}#row-{candidate_id}", status_code=303)

    @app.post("/candidates/{candidate_id}/confirm-monitoring")
    async def confirm_candidate_monitoring(candidate_id: int, request: Request):
        """Confirm the latest reviewed price and resume scheduled monitoring."""
        from src.config import load_config, load_env_file
        from src.services.daily_monitor import DailyMonitorService

        load_env_file(root / ".env")
        fresh_config = load_config(root / "config.yaml")
        service = DailyMonitorService(db, fresh_config, root)

        referer = request.headers.get("referer") or "/monitor/results"
        if "#" in referer:
            referer = referer.split("#")[0]

        import urllib.parse
        try:
            service.confirm_candidate_and_start_monitoring(candidate_id)
        except Exception as exc:
            LOGGER.warning("Confirm monitoring failed: %s", exc)
            error_msg = urllib.parse.quote(f"確認失敗：{exc}")
            separator = "&" if "?" in referer else "?"
            return RedirectResponse(
                url=f"{referer}{separator}error={error_msg}#row-{candidate_id}",
                status_code=303,
            )

        success_msg = urllib.parse.quote("已確認商品並開始監控")
        separator = "&" if "?" in referer else "?"
        return RedirectResponse(
            url=f"{referer}{separator}message={success_msg}#row-{candidate_id}",
            status_code=303,
        )

    @app.post("/candidates/batch_recheck")
    async def batch_recheck_candidates(request: Request):
        form_data = await request.form()
        candidate_ids = form_data.getlist("candidate_ids")
        if not candidate_ids:
            return RedirectResponse(url="/candidates?error=未選擇任何項目", status_code=303)
            
        from src.config import load_config, load_env_file
        load_env_file(root / ".env")
        fresh_config = load_config(root / "config.yaml")

        from src.services.daily_monitor import DailyMonitorService
        service = DailyMonitorService(db, fresh_config, root)
        
        success_count = 0
        for cid_str in candidate_ids:
            try:
                service.check_single_candidate(int(cid_str))
                success_count += 1
            except Exception as exc:
                LOGGER.warning("Batch recheck failed for candidate %s: %s", cid_str, exc)
                
        import urllib.parse
        success_msg = urllib.parse.quote(f"已完成 {success_count} 筆批次重查")
        return RedirectResponse(url=f"/candidates?message={success_msg}", status_code=303)

    @app.post("/candidates/{candidate_id}/takedown")
    async def mark_takedown_notified(candidate_id: int, request: Request):
        """Mark a candidate as 'takedown notified'."""
        db.update_candidate_status(candidate_id, "takedown_notified")
        referer = request.headers.get("referer") or "/monitor/results"
        if "#" in referer: referer = referer.split("#")[0]
        return RedirectResponse(url=f"{referer}#row-{candidate_id}", status_code=303)

    @app.post("/candidates/{candidate_id}/restore_violation")
    async def restore_violation(candidate_id: int, request: Request):
        """Restore a candidate to 'suspected_violation' from 'takedown_notified'."""
        db.update_candidate_status(candidate_id, "normal")
        referer = request.headers.get("referer") or "/monitor/results"
        if "#" in referer: referer = referer.split("#")[0]
        return RedirectResponse(url=f"{referer}#row-{candidate_id}", status_code=303)

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
        if status == "needs_review":
            snapshots = [s for s in snapshots if s.final_status == "needs_review"]
        elif status and status != "suspected_violation":
            snapshots = [s for s in snapshots if s.candidate_status == status]
        return _render(
            request, "results.html", active="results",
            snapshots=snapshots,
            filter_status=status or "",
            title="疑似破價" if violation_only else "每日掃描",
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
                from src.config import load_config, load_env_file
                load_env_file(root / ".env")
                fresh_config = load_config(root / "config.yaml")
                
                from src.services.daily_monitor import DailyMonitorService
                from src.services.report_service import ReportService
                service = DailyMonitorService(db, fresh_config, root)
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
                from src.config import load_config, load_env_file
                load_env_file(root / ".env")
                fresh_config = load_config(root / "config.yaml")

                from src.services.discovery_search import DiscoverySearchService
                service = DiscoverySearchService(db, fresh_config, root)

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

    # -- Multi-Source Dashboard Pages ---------------------------------------

    @app.get("/observations", response_class=HTMLResponse)
    async def observations_page(request: Request, product_id: int | None = None):
        observations = db.get_observations(product_id=product_id, limit=300)
        products = db.list_products(active_only=False)
        return _render(
            request, "observations.html", active="observations",
            observations=observations,
            products=products,
            filter_product_id=product_id,
        )

    @app.get("/source-health", response_class=HTMLResponse)
    async def source_health_page(request: Request):
        health_stats = db.get_source_health()
        return _render(
            request, "source_health.html", active="source-health",
            health_stats=health_stats,
        )

    @app.get("/manual-review", response_class=HTMLResponse)
    async def manual_review_page(request: Request):
        # We need to find candidates that need review.
        # This includes blocked, captcha_required, or final_status=needs_review
        # We can approximate this by querying snapshots where final_status=needs_review
        snapshots = db.get_snapshots(limit=300)
        review_items = [s for s in snapshots if s.final_status == "needs_review" or s.candidate_status in ("blocked", "captcha_required")]
        return _render(
            request, "manual_review.html", active="manual-review",
            review_items=review_items,
        )

    @app.post("/manual-review/{candidate_id}/price")
    async def manual_review_submit(
        candidate_id: int,
        price: float = Form(...),
        product_id: int = Form(...),
    ):
        """Submit a manual price observation for a candidate."""
        db.insert_observation(
            product_id=product_id,
            candidate_id=candidate_id,
            platform="manual",
            source="manual",
            price=price,
            match_score=100,
            confidence=1.0,
            status="success",
            error_message="Manual input",
        )
        return RedirectResponse(url="/manual-review?message=已新增人工觀測價格", status_code=303)

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
