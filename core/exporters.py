"""CSV, Excel and JSON export.

Every workbook carries a second sheet documenting how the data was produced:
source, timestamp, adapter, field coverage and warnings. A customer receiving an
export can see exactly what was and was not collected.

Optional extras:
  * `records` exports a filtered/cleaned subset without mutating the run;
  * `include_issues` adds a third sheet listing every flagged record;
  * `extra_keys` controls which arbitrary columns appear.
"""
from __future__ import annotations

import io
import json
import re
from copy import copy
from datetime import datetime, timezone

import pandas as pd

from .cleaning import available_extra_keys, issue_rows
from .models import FIELD_BY_KEY, Record, RunResult, header_for
from .quality import export_columns


def _safe_cell(value):
    """Stop spreadsheet formula injection without mangling phone numbers.

    A value beginning with =, @, tab or CR is always escaped. A leading + or - is
    escaped only when it is not the start of a phone-like number.
    """
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(("=", "@", "\t", "\r")):
        return "'" + value
    if value[0] in "+-" and not re.search(r"\d", value[:4]):
        return "'" + value
    return value


def records_to_frame(records: list[Record], selected_fields: list[str],
                     extra_keys: list[str] | None = None) -> pd.DataFrame:
    keys = available_extra_keys(records) if extra_keys is None else list(extra_keys)
    columns = export_columns(selected_fields, keys)
    rows = []
    for record in records:
        row = record.to_row()
        rows.append({column: _safe_cell(row.get(column, "")) for column in columns})
    frame = pd.DataFrame(rows, columns=columns)
    return frame.rename(columns={c: header_for(c) for c in frame.columns})


def safe_slug(value: str, fallback: str = "extract") -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")
    return (slug or fallback)[:60]


def basename_for(run: RunResult) -> str:
    host = safe_slug(run.inspection.host or "site")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{safe_slug(run.adapter_id or 'extract')}_{host}_{stamp}"


def to_csv_bytes(records: list[Record], selected_fields: list[str],
                 extra_keys: list[str] | None = None) -> bytes:
    frame = records_to_frame(records, selected_fields, extra_keys)
    return frame.to_csv(index=False).encode("utf-8-sig")


def to_json_bytes(records: list[Record], selected_fields: list[str],
                  extra_keys: list[str] | None = None, pretty: bool = True) -> bytes:
    frame = records_to_frame(records, selected_fields, extra_keys)
    payload = frame.to_dict(orient="records")
    return json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None).encode("utf-8")


def _summary_rows(run: RunResult, records: list[Record],
                  context: dict[str, str] | None = None) -> list[tuple[str, str]]:
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
        ("Records in run", str(quality.unique)),
        ("Records exported", str(len(records))),
        ("Profiles attempted", str(quality.attempted)),
        ("Duplicates removed by engine", str(quality.duplicates_removed)),
        ("Extraction success rate", f"{quality.success_rate:.1%}"),
        ("Records with phone or email", str(quality.with_any_contact)),
        ("Quality level", quality.level.upper()),
        ("Adapter health", health.status.upper()),
        ("Baseline record count", str(health.baseline_records if health.baseline_records is not None else "n/a")),
        ("Markup signature changed", "yes" if health.structure_changed else "no"),
        ("Pages fetched", str(run.pages_fetched)),
        ("Robots.txt", run.inspection.robots_detail or "not checked"),
    ]
    diagnosis = getattr(run, "diagnosis", None)
    if diagnosis is not None and getattr(diagnosis, "attempts", None):
        rows.append(("", ""))
        rows.append(("Extraction methods tried", ""))
        for attempt in diagnosis.attempts:
            rows.append((
                f"  {attempt.label}",
                f"{attempt.status} · {attempt.records} record(s) · {attempt.detail}",
            ))
        if diagnosis.selected_label:
            rows.append(("  Delivered using", diagnosis.selected_label))
        if diagnosis.failure_code:
            rows.append(("  Failure diagnosis", f"{diagnosis.failure_code}: {diagnosis.summary}"))
        for recommendation in diagnosis.recommendations:
            rows.append(("  Next step", recommendation))
    combined = {**run.export_context, **(context or {})}
    if combined:
        rows.append(("", ""))
        rows.append(("Delivery / cleaning", ""))
        rows.extend((f"  {key}", str(value)) for key, value in combined.items())

    rows.extend([
        ("", ""),
        ("Field coverage", "share of exported records with a value"),
    ])
    for key, rate in quality.field_fill.items():
        label = FIELD_BY_KEY[key].label if key in FIELD_BY_KEY else header_for(key)
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


def to_excel_bytes(run: RunResult, selected_fields: list[str],
                   extra_keys: list[str] | None = None,
                   records: list[Record] | None = None,
                   include_issues: bool = False,
                   context: dict[str, str] | None = None) -> bytes:
    """Raises on failure so the caller can show a real error instead of hiding the button."""
    exported = run.records if records is None else records
    frame = records_to_frame(exported, selected_fields, extra_keys)
    summary = pd.DataFrame(_summary_rows(run, exported, context), columns=["Item", "Detail"])
    issues = issue_rows(exported) if include_issues else []

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Records")
        summary.to_excel(writer, index=False, sheet_name="Run Summary")
        if include_issues:
            issues_frame = pd.DataFrame(issues, columns=[
                "Row", "Name", "Brokerage", "Phone", "Email", "Profile URL", "Issues", "Quality flags"
            ])
            issues_frame.to_excel(writer, index=False, sheet_name="Issues")

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

        if include_issues and "Issues" in writer.sheets:
            issues_sheet = writer.sheets["Issues"]
            issues_sheet.freeze_panes = "A2"
            for index, column in enumerate(issues_frame.columns, start=1):
                longest = issues_frame[column].astype(str).str.len().max() if len(issues_frame) else 0
                longest = int(longest) if longest == longest else 0
                issues_sheet.column_dimensions[issues_sheet.cell(row=1, column=index).column_letter].width = max(
                    10, min(60, longest + 2)
                )
    return buffer.getvalue()
