from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, send_from_directory, url_for

from src.web.data_store import (
    REPORT_DOWNLOADS,
    build_summary,
    filter_results,
    latest_dir,
    latest_report_path,
    load_products_for_choices,
    read_official_review_rows,
    read_latest_results,
    read_latest_violations,
    read_log_tail,
    save_review_decision,
)
from src.web.runner import MonitorRunner


def _fromjson(value: str) -> object:
    try:
        return json.loads(value)
    except Exception:
        return {}


def _format_tz(iso_str: str) -> str:
    if not iso_str or iso_str == "-":
        return "-"
    try:
        if iso_str.endswith("Z"):
            iso_str = iso_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_str[:16]


def _status_label(status: str) -> str:
    labels = {
        "normal": "正常",
        "suspected_violation": "疑似破價",
        "price_unknown": "未抓到價格",
        "error": "錯誤",
        "blocked": "遭到阻擋",
        "active": "待監測",
        "excluded": "已排除",
        "takedown_notified": "已通知下架",
    }
    return labels.get(status, status) if status else ""


def create_app(project_root: Path | None = None) -> Flask:
    root = project_root or Path(__file__).resolve().parents[2]
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["PROJECT_ROOT"] = root
    app.config["RUNNER"] = MonitorRunner(root)
    app.jinja_env.filters["fromjson"] = _fromjson
    app.jinja_env.filters["format_tz"] = _format_tz
    app.jinja_env.filters["status_label"] = _status_label

    @app.context_processor
    def inject_globals() -> dict[str, object]:
        return {
            "run_status": app.config["RUNNER"].status(),
            "downloads": REPORT_DOWNLOADS,
        }

    @app.get("/")
    def dashboard() -> str:
        rows = read_latest_results(root)
        violations = read_latest_violations(root)
        recent_rows = rows[:12]
        return render_template(
            "dashboard.html",
            active="dashboard",
            summary=build_summary(root),
            budget={
                "daily_used": 0,
                "daily_budget": 0,
                "monthly_used": 0,
                "monthly_budget": 0,
            },
            violations=violations[:8],
            recent=recent_rows,
            recent_rows=recent_rows,
            logs=read_log_tail(root, "scheduler.log", 12),
        )

    @app.get("/violations")
    def violations() -> str:
        return render_template(
            "results.html",
            active="violations",
            title="疑似破價",
            rows=read_latest_violations(root),
            filters={},
            show_filters=False,
        )

    @app.get("/results")
    def results() -> str:
        query = request.args.get("q", "")
        platform = request.args.get("platform", "")
        status = request.args.get("status", "")
        rows = filter_results(read_latest_results(root), query, platform, status)
        all_rows = read_latest_results(root)
        platforms = sorted({row.get("platform", "") for row in all_rows if row.get("platform")})
        statuses = sorted(
            {row.get("violation_status", "") for row in all_rows if row.get("violation_status")}
        )
        return render_template(
            "results.html",
            active="results",
            title="全部結果",
            rows=rows,
            filters={"q": query, "platform": platform, "status": status},
            platforms=platforms,
            statuses=statuses,
            show_filters=True,
        )


    @app.get("/review")
    def review() -> str:
        status = request.args.get("status", "")
        return render_template(
            "review.html",
            active="review",
            rows=read_official_review_rows(root, status),
            products=load_products_for_choices(root),
            selected_status=status,
            error=request.args.get("error", ""),
        )

    @app.post("/review/decision")
    def review_decision() -> object:
        try:
            save_review_decision(
                root,
                request.form.get("official_product_url", ""),
                request.form.get("official_product_name", ""),
                request.form.get("decision", ""),
                request.form.get("matched_db_product_name", ""),
                request.form.get("reviewer", ""),
                request.form.get("note", ""),
            )
            return redirect(url_for("review", status=request.form.get("return_status", "")))
        except ValueError as exc:
            return redirect(url_for("review", error=str(exc)))

    @app.post("/run")
    def run_monitor() -> object:
        app.config["RUNNER"].start()
        return redirect(url_for("dashboard"))

    @app.get("/run-status")
    def run_status() -> dict[str, object]:
        return app.config["RUNNER"].status().__dict__

    @app.get("/logs")
    def logs() -> str:
        return render_template(
            "logs.html",
            active="logs",
            scheduler_log=read_log_tail(root, "scheduler.log"),
            run_log=read_log_tail(root, "run.log"),
            dashboard_log=read_log_tail(root, "dashboard_run.log"),
        )

    @app.get("/downloads/<path:filename>")
    def download(filename: str) -> object:
        if filename not in REPORT_DOWNLOADS:
            abort(404)
        path = latest_report_path(root, filename)
        if not path.exists():
            abort(404)
        return send_from_directory(latest_dir(root), filename, as_attachment=True)

    return app
