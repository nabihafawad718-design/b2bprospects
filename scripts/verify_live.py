#!/usr/bin/env python3
"""One-command live acceptance test for the browser engine.

    python scripts/verify_live.py https://permitted-directory.example/agents

What it does: launches a real Chromium, renders the page, detects the repeated
records, maps fields automatically, extracts with pagination, then writes CSV and
Excel to data/exports and prints a PASS/FAIL summary.

Run this against a directory you are permitted to collect from. It contacts the
real site, so it is deliberately conservative: two pages, ten records, one profile
unless you raise the limits.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.browser.driver import BrowserBlocked, BrowserError, diagnose      # noqa: E402
from core.browser.engine import inspect_in_browser, run_browser_extraction  # noqa: E402
from core.browser.extract import suggest_mapping                            # noqa: E402
from core.exporters import basename_for, to_csv_bytes, to_excel_bytes       # noqa: E402
from core.settings import EXPORT_DIR, ensure_dirs                           # noqa: E402

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


def _line(check: str, status: str, detail: str = "") -> None:
    print(f"[{status:4s}] {check}" + (f" — {detail}" if detail else ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the browser engine against a real directory.")
    parser.add_argument("url")
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--max-records", type=int, default=10)
    parser.add_argument("--enrich", action="store_true", help="Also read one profile page")
    parser.add_argument("--headless", action="store_true", default=True)
    args = parser.parse_args()

    print("=" * 72)
    print("B2BProspects — live browser verification")
    print("=" * 72)
    print(f"Target: {args.url}\n")

    ok, message = diagnose()
    _line("Browser available", PASS if ok else FAIL, message.splitlines()[0] if message else "")
    if not ok:
        print(message)
        return 2

    failures = 0

    # 1. Open and inspect
    try:
        report = inspect_in_browser(args.url)
    except BrowserBlocked as exc:
        _line("Open page", FAIL, str(exc))
        return 1
    except BrowserError as exc:
        _line("Open page", FAIL, str(exc))
        return 1

    _line("Browser opened the page", PASS, f"HTTP {report.status_code}, “{report.title[:50]}”")
    _line("Records detected", PASS if report.row_count else FAIL, f"{report.row_count} rows")
    _line("Pagination", WARN if report.pagination.get("strategy") in (None, "none") else PASS,
          str(report.pagination.get("strategy")))

    if not report.structure:
        print("\nNo repeated structure found. This layout needs a dedicated adapter.")
        print("Run scripts/assess_site.py for details, and check the page manually.")
        return 1

    print("\nDetected columns:")
    for column in report.structure.columns[:12]:
        sample = (column.sample_values[0] if column.sample_values else "")[:44]
        print(f"  [{column.index:2d}] {column.kind_hint:10s} fill={column.fill_rate:.0%}  “{sample}”")

    # 2. Extract
    mapping = suggest_mapping(report.structure)
    print(f"\nAuto-mapping: {mapping.columns}")
    try:
        result = run_browser_extraction(
            args.url,
            mapping=mapping,
            structure=report.structure,
            max_pages=args.max_pages,
            max_records=args.max_records,
            enrich_profiles=args.enrich,
            max_profiles=1,
        )
    except BrowserBlocked as exc:
        _line("Extraction", FAIL, f"blocked: {exc}")
        return 1
    except BrowserError as exc:
        _line("Extraction", FAIL, str(exc))
        return 1

    print()
    _line("Records extracted", PASS if result.records else FAIL, f"{len(result.records)}")
    named = sum(1 for r in result.records if r.agent_name)
    contactable = sum(1 for r in result.records if r.phone or r.email)
    _line("Records with a name", PASS if named else FAIL, f"{named}/{len(result.records)}")
    _line("Records with phone or email", PASS if contactable else WARN, f"{contactable}/{len(result.records)}")
    _line("Pages walked", PASS, str(result.pages_fetched))
    _line("Duplicates removed", PASS, str(result.quality.duplicates_removed))
    _line("Quality level", PASS if result.quality.level == "ok" else WARN, result.quality.level)

    for warning in (result.quality.warnings + result.inspection.warnings)[:6]:
        print(f"       ! {warning}")

    if not result.records:
        failures += 1

    # 3. Exports
    ensure_dirs()
    basename = basename_for(result)
    csv_path = EXPORT_DIR / f"{basename}.csv"
    xlsx_path = EXPORT_DIR / f"{basename}.xlsx"
    try:
        csv_path.write_bytes(to_csv_bytes(result.records, result.selected_fields))
        _line("CSV export", PASS, str(csv_path))
    except Exception as exc:
        _line("CSV export", FAIL, str(exc))
        failures += 1
    try:
        xlsx_path.write_bytes(to_excel_bytes(result, result.selected_fields))
        _line("Excel export", PASS, str(xlsx_path))
    except Exception as exc:
        _line("Excel export", FAIL, str(exc))
        failures += 1

    print("\n" + "-" * 72)
    if failures:
        print(f"RESULT: FAIL ({failures} check(s) failed)")
        return 1
    print("RESULT: PASS — this layout extracted end to end.")
    print()
    print("Still unverified by this run:")
    print("  • other brokerages and layouts (each needs its own run)")
    print("  • pagination beyond the page limit used here")
    print("  • profile contact extraction unless --enrich was passed")
    print("  • whether your use of the data is permitted — that is your call, not the tool's")
    return 0


if __name__ == "__main__":
    sys.exit(main())
