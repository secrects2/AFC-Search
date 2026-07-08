from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path


def resolve_project_path(project_root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def setup_logging(log_dir: Path) -> None:
    ensure_dir(log_dir)
    log_file = log_dir / "run.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )


def sanitize_filename(value: str, max_length: int = 80) -> str:
    value = re.sub(r'[<>:"/\\|?*\s]+', "_", value.strip())
    value = value.strip("._")
    return (value or "item")[:max_length]


def copy_latest_reports(run_output_dir: Path, output_root: Path) -> None:
    latest_dir = ensure_dir(output_root / "latest")
    for filename in ("all_results.csv", "violations.csv", "price_monitor_report.xlsx"):
        source = run_output_dir / filename
        if source.exists():
            shutil.copy2(source, latest_dir / filename)

