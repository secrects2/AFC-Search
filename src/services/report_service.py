"""Report service — generate Excel reports from database."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.database import Database, SnapshotRow

LOGGER = logging.getLogger(__name__)


# Column headers for daily report
DAILY_COLUMNS = [
    ("商品名稱", "product_name"),
    ("品牌", "brand"),
    ("建議售價", "suggested_price"),
    ("平台", "platform"),
    ("賣場商品標題", "title"),
    ("賣家", "seller"),
    ("商品網址", "url"),
    ("抓到價格", "price"),
    ("價差", "price_diff"),
    ("是否疑似破價", "is_violation"),
    ("狀態", "candidate_status"),
    ("上次檢查時間", "checked_at"),
    ("第一次發現時間", "first_seen_at"),
    ("搜尋來源", "source_found_by"),
    ("截圖路徑", "screenshot_path"),
    ("錯誤訊息", "error_message"),
]


class ReportService:
    """Generate Excel reports from database snapshots."""

    def __init__(self, db: Database, project_root: Path) -> None:
        self.db = db
        self.output_dir = project_root / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_daily_report(self, date: str | None = None) -> Path:
        """Export daily_report.xlsx with all snapshots for a given date."""
        today = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        snapshots = self.db.get_snapshots(date=today)

        if not snapshots:
            # Fall back to latest snapshots if no data for today
            snapshots = self.db.get_snapshots(limit=500)

        path = self.output_dir / "daily_report.xlsx"
        self._write_xlsx(path, snapshots, f"每日報表 {today}")
        LOGGER.info("每日報表已產出：%s (%d 筆)", path, len(snapshots))
        return path

    def export_violations_report(self, date: str | None = None) -> Path:
        """Export suspected_violations.xlsx with only violation snapshots."""
        today = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        snapshots = self.db.get_snapshots(date=today, violation_only=True)

        if not snapshots:
            snapshots = self.db.get_snapshots(violation_only=True, limit=200)

        path = self.output_dir / "suspected_violations.xlsx"
        self._write_xlsx(path, snapshots, f"疑似破價 {today}")
        LOGGER.info("破價報表已產出：%s (%d 筆)", path, len(snapshots))
        return path

    def _write_xlsx(
        self, path: Path, snapshots: list[SnapshotRow], sheet_title: str
    ) -> None:
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        except ImportError:
            LOGGER.warning("openpyxl 未安裝，改用 CSV 輸出")
            self._write_csv_fallback(path.with_suffix(".csv"), snapshots)
            return

        wb = Workbook()
        ws = wb.active
        ws.title = sheet_title[:31]  # Excel sheet name limit

        # Header style
        header_font = Font(bold=True, color="FFFFFF", size=11)
        header_fill = PatternFill("solid", fgColor="2B579A")
        header_align = Alignment(horizontal="center", vertical="center")
        thin_border = Border(
            left=Side(style="thin"),
            right=Side(style="thin"),
            top=Side(style="thin"),
            bottom=Side(style="thin"),
        )

        # Write headers
        for col_idx, (label, _) in enumerate(DAILY_COLUMNS, start=1):
            cell = ws.cell(row=1, column=col_idx, value=label)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin_border

        # Write data rows
        violation_fill = PatternFill("solid", fgColor="FFF2CC")
        for row_idx, snap in enumerate(snapshots, start=2):
            for col_idx, (_, attr) in enumerate(DAILY_COLUMNS, start=1):
                value = getattr(snap, attr, "")
                if attr == "is_violation":
                    value = "是" if snap.is_violation else ""
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.border = thin_border
                if snap.is_violation:
                    cell.fill = violation_fill

        # Auto-adjust column widths
        for col_idx, (label, _) in enumerate(DAILY_COLUMNS, start=1):
            ws.column_dimensions[chr(64 + col_idx) if col_idx <= 26 else "A"].width = max(
                len(label) * 2, 12
            )

        # Freeze header row
        ws.freeze_panes = "A2"

        wb.save(path)

    def _write_csv_fallback(self, path: Path, snapshots: list[SnapshotRow]) -> None:
        import csv
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([label for label, _ in DAILY_COLUMNS])
            for snap in snapshots:
                row = []
                for _, attr in DAILY_COLUMNS:
                    value = getattr(snap, attr, "")
                    if attr == "is_violation":
                        value = "是" if snap.is_violation else ""
                    row.append(value)
                writer.writerow(row)
