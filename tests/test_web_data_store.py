from pathlib import Path

import pytest

from src.web.data_store import (
    build_summary,
    delete_manual_link,
    latest_report_path,
    read_manual_links,
    upsert_manual_link,
)


def test_latest_report_path_rejects_unknown_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        latest_report_path(tmp_path, "secret.env")


def test_summary_reads_latest_results(tmp_path: Path) -> None:
    latest = tmp_path / "output" / "latest"
    latest.mkdir(parents=True)
    (latest / "all_results.csv").write_text(
        "run_time,product_name,found_price,violation_status\n"
        "2026-07-07T08:00:00,AFC胺基酸,299,suspected_violation\n"
        "2026-07-07T08:00:00,AFC綠藻,,search_failed\n",
        encoding="utf-8",
    )
    (latest / "violations.csv").write_text(
        "run_time,product_name,found_price,violation_status\n"
        "2026-07-07T08:00:00,AFC胺基酸,299,suspected_violation\n",
        encoding="utf-8",
    )

    summary = build_summary(tmp_path)

    assert summary["total_products"] == 2
    assert summary["violations"] == 1
    assert summary["missing_price"] == 1


def test_manual_links_upsert_and_delete(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    upsert_manual_link(tmp_path, "AFC胺基酸", "https://example.com/a", "manual")
    upsert_manual_link(tmp_path, "AFC胺基酸", "https://example.com/a", "shopee")
    links = read_manual_links(tmp_path)

    assert len(links) == 1
    assert links[0].platform == "shopee"

    delete_manual_link(tmp_path, links[0].index)
    assert read_manual_links(tmp_path) == []


def test_manual_links_rejects_local_file_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        upsert_manual_link(tmp_path, "AFC胺基酸", "file:///C:/secret.txt", "manual")

