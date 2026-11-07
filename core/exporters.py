"""CSV and Excel export.

Every workbook carries a second sheet documenting how the data was produced:
source, timestamp, adapter, field coverage and warnings. A customer receiving an
export can see exactly what was and was not collected.
"""
from __future__ import annotations

import io
import re
from copy import copy
from datetime import datetime, timezone

import pandas as pd

from .models import EXPORT_HEADERS, FIELD_BY_KEY, Record, RunResult
from .quality import export_columns


def records_to_frame(records: list[Record], selected_fields: list[str]) -> pd.DataFrame:
    columns = export_columns(selected_fields)
    rows = []
    for record in records:
        row = record.to_row()
        rows.append({column: row.get(column, "") for column in columns})
    frame = pd.DataFrame(rows, columns=columns)
    return frame.rename(columns={c: EXPORT_HEADERS.get(c, c) for c in frame.columns})


def safe_slug(value: str, fallback: str = "extract") -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")
    return (slug or fallback)[:60]


def basename_for(run: RunResult) -> str:
    host = safe_slug(run.inspection.host or "site")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{safe_slug(run.adapter_id or 'extract')}_{host}_{stamp}"


def to_csv_bytes(records: list[Record], selected_fields: list[str]) -> bytes:
    frame = records_to_frame(records, selected_fields)
    return frame.to_csv(index=False).encode("utf-8-sig")


def _summary_rows(run: RunResult) -> list[tuple[str, str]]:
    quality = run.quality
    health = run.health
    rows = [
        ("Source URL", run.url),
        ("Site", run.inspection.host),
        ("Adapter", f"{run.adapter_label} ({run.adapter_id})"),
        ("Run ID", run.run_id),
        ("Started (UTC)", run.started_at),
        ("Finished (UTC)", run.finished_at),
        ("Duration (seconds)", f"{run.elapsed_seconds:.1f}"),
        ("Records exported", str(quality.unique)),
        ("Profiles attempted", str(quality.attempted)),
        ("Duplicates removed", str(quality.duplicates_removed)),
        ("Extraction success rate", f"{quality.success_rate:.1%}"),
        ("Records with phone or email", str(quality.with_any_contact)),
        ("Quality level", quality.level.upper()),
        ("Adapter health", health.status.upper()),
        ("Baseline record count", str(health.baseline_records if health.baseline_records is not None else "n/a")),
        ("Markup signature changed", "yes" if health.structure_changed else "no"),
        ("Pages fetched", str(run.pages_fetched)),
        ("Robots.txt", run.inspection.robots_detail or "not checked"),
        ("", ""),
        ("Field coverage", "share of exported records with a value"),
    ]
    for key, rate in quality.field_fill.items():
        label = FIELD_BY_KEY[key].label if key in FIELD_BY_KEY else key
        rows.append((f"  {label}", f"{rate:.0%}"))

    warnings = []
    for message in [*health.reasons, *quality.warnings, *run.inspection.warnings, *run.errors]:
        if message and message not in warnings:
            warnings.append(message)
    if warnings:
        rows.append(("", ""))
        rows.append(("Warnings", ""))
        rows.extend(("  •", message) for message in warnings)

    rows.append(("", ""))
    rows.append((
        "Data statement",
        "Collected from publicly accessible pages using the source and timestamp above. "
        "Fields not published by the source are left blank rather than inferred. "
        "Records have not been independently verified unless stated in writing.",
    ))
    return rows


def to_excel_bytes(run: RunResult, selected_fields: list[str]) -> bytes:
    """Raises on failure so the caller can show a real error instead of hiding the button."""
    frame = records_to_frame(run.records, selected_fields)
    summary = pd.DataFrame(_summary_rows(run), columns=["Item", "Detail"])

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Records")
        summary.to_excel(writer, index=False, sheet_name="Run Summary")

        records_sheet = writer.sheets["Records"]
        for index, column in enumerate(frame.columns, start=1):
            longest = frame[column].astype(str).str.len().max() if len(frame) else 0
            longest = int(longest) if longest == longest else 0  # NaN guard on empty frames
            width = max(12, min(44, longest + 2))
            records_sheet.column_dimensions[records_sheet.cell(row=1, column=index).column_letter].width = width
        records_sheet.freeze_panes = "A2"

        summary_sheet = writer.sheets["Run Summary"]
        summary_sheet.column_dimensions["A"].width = 34
        summary_sheet.column_dimensions["B"].width = 95
        for row in summary_sheet.iter_rows(min_row=1, max_row=summary_sheet.max_row, max_col=2):
            for cell in row:
                alignment = copy(cell.alignment)
                alignment.wrap_text = True
                alignment.vertical = "top"
                cell.alignment = alignment
    return buffer.getvalue()
