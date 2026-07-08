from __future__ import annotations

import csv
import html
import zipfile
from pathlib import Path
from typing import Any


REPORT_COLUMNS = [
    "run_time",
    "platform",
    "product_name",
    "sku_code",
    "suggested_price",
    "found_title",
    "found_price",
    "price_gap",
    "price_gap_percent",
    "match_score",
    "violation_status",
    "seller",
    "url",
    "screenshot_path",
    "parse_status",
    "ocr_status",
    "evidence_text",
    "official_image_url",
    "image_match_status",
    "image_match_score",
]


def write_reports(rows: list[dict[str, Any]], output_dir: Path, summary: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    violations = [row for row in rows if row.get("violation_status") == "suspected_violation"]
    violations.sort(
        key=lambda row: (
            float(row.get("price_gap") or 0),
            -int(row.get("match_score") or 0),
            str(row.get("platform") or ""),
        )
    )

    missing_price = [row for row in rows if row.get("found_price") in ("", None)]
    needs_review = [
        row
        for row in rows
        if 70 <= int(row.get("match_score") or 0) < int(summary.get("match_threshold", 85))
    ]

    write_csv(output_dir / "all_results.csv", rows, REPORT_COLUMNS)
    write_csv(output_dir / "violations.csv", violations, REPORT_COLUMNS)

    summary_rows = [{"metric": key, "value": value} for key, value in summary.items()]
    write_xlsx(
        output_dir / "price_monitor_report.xlsx",
        [
            ("疑似破價", violations, REPORT_COLUMNS),
            ("全部結果", rows, REPORT_COLUMNS),
            ("未抓到價格", missing_price, REPORT_COLUMNS),
            ("可能相關需人工確認", needs_review, REPORT_COLUMNS),
            ("執行摘要", summary_rows, ["metric", "value"]),
        ],
    )


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _cell_value(row.get(column)) for column in columns})


def write_xlsx(path: Path, sheets: list[tuple[str, list[dict[str, Any]], list[str]]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", _content_types(len(sheets)))
        workbook.writestr("_rels/.rels", _root_rels())
        workbook.writestr("xl/workbook.xml", _workbook_xml([sheet[0] for sheet in sheets]))
        workbook.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(sheets)))
        for index, (_, rows, columns) in enumerate(sheets, start=1):
            workbook.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet_xml(rows, columns))


def _content_types(sheet_count: int) -> str:
    overrides = "\n".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
{overrides}
</Types>'''


def _root_rels() -> str:
    return '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''


def _workbook_rels(sheet_count: int) -> str:
    rels = "\n".join(
        f'<Relationship Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
{rels}
</Relationships>'''


def _workbook_xml(sheet_names: list[str]) -> str:
    sheets_xml = "\n".join(
        f'<sheet name="{_xml(sheet_name[:31])}" sheetId="{index}" r:id="rId{index}"/>'
        for index, sheet_name in enumerate(sheet_names, start=1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>
{sheets_xml}
</sheets>
</workbook>'''


def _worksheet_xml(rows: list[dict[str, Any]], columns: list[str]) -> str:
    all_rows = [dict(zip(columns, columns))] + rows
    row_xml = []
    for row_index, row in enumerate(all_rows, start=1):
        cells = []
        for column_index, column in enumerate(columns, start=1):
            ref = f"{_column_name(column_index)}{row_index}"
            value = row.get(column, "")
            cells.append(_cell_xml(ref, value))
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>
{"".join(row_xml)}
</sheetData>
</worksheet>'''


def _cell_xml(ref: str, value: Any) -> str:
    value = _cell_value(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t>{_xml(str(value))}</t></is></c>'


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _xml(value: str) -> str:
    return html.escape(value, quote=True)


def _cell_value(value: Any) -> Any:
    return "" if value is None else value
