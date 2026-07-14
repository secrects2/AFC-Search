import asyncio
from pathlib import Path

from src.database import Database
from src.web.api import create_app


def test_legacy_takedown_action_uses_excluded_soft_delete(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "price_monitor.db"
    db = Database(db_path)
    product_id = db.upsert_product("AFC 測試商品", suggested_price=1000)
    candidate_id = db.upsert_candidate(
        product_id=product_id,
        platform="momo",
        url="https://example.com/product/1",
        title="AFC 測試商品",
    )

    app = create_app(tmp_path)
    route = next(
        route for route in app.routes
        if getattr(route, "path", "") == "/candidates/{candidate_id}/takedown"
    )

    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/candidates/1/takedown",
            "raw_path": b"/candidates/1/takedown",
            "query_string": b"",
            "headers": [(b"referer", b"/monitor/results")],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "http_version": "1.1",
        }
    )
    response = asyncio.run(route.endpoint(candidate_id, request))

    assert response.status_code == 303
    candidate = Database(db_path).list_candidates()[0]
    assert candidate.status == "excluded"


def test_legacy_takedown_status_is_migrated_to_excluded(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "price_monitor.db"
    db = Database(db_path)
    product_id = db.upsert_product("AFC 舊版狀態測試", suggested_price=1000)
    db.upsert_candidate(
        product_id=product_id,
        platform="pchome",
        url="https://example.com/product/legacy",
        title="AFC 舊版狀態測試",
        status="takedown_notified",
    )

    migrated = Database(db_path).list_candidates()[0]

    assert migrated.status == "excluded"
