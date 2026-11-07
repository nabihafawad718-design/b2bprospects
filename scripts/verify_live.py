#!/usr/bin/env python3
"""One-command acceptance test against a real directory.

    python scripts/verify_live.py https://permitted-directory.example/agents
    python scripts/verify_live.py https://permitted-directory.example/agents --engine static
    python scripts/verify_live.py http://127.0.0.1:8765/agents --allow-local

It opens the page with the chosen engine, reports which extraction layers were
available, extracts a bounded sample, prints the exact method attempts and writes
CSV + Excel evidence to data/exports. It never claims a site works unless records
were actually produced and exported.

The script contacts the real site. Run it only against directories you are
permitted to collect from.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.browser.driver import BrowserBlocked, BrowserError, diagnose      # noqa: E402
from core.browser.engine import inspect_in_browser, run_browser_extraction  # noqa: E402
from core.browser.extract import suggest_mapping                            # noqa: E402
from core.cleaning import available_extra_keys                              # noqa: E402
from core.engine import run_extraction                                      # noqa: E402
from core.exporters import basename_for, to_csv_bytes, to_excel_bytes       # noqa: E402
from core.inspect_site import inspect_url                                   # noqa: E402
from core.net import BlockedError, FetchError, PoliteFetcher, RobotsDisallowed  # noqa: E402
from core.settings import EXPORT_DIR, SETTINGS, ensure_dirs                 # noqa: E402

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


def _line(check: str, status: str, detail: str = "") -> None:
    print(f"[{status:4s}] {check}" + (f" — {detail}" if detail else ""))


def _fields(value: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in (value or "").split(",") if item.strip())
    return items or ("agent_name", "brokerage_name", "phone", "email", "street_address",
                     "city", "state", "postal_code", "website", "profile_url")


def _diagnosis(result) -> None:
    diagnosis = getattr(result, "diagnosis", None)
    if diagnosis is None or not diagnosis.attempts:
        return
    print("\nExtraction methods tried:")
    for attempt in diagnosis.attempts:
        print(f"  {attempt.status:6s} {attempt.label:38s} {attempt.records:4d} record(s)  {attempt.detail}")
    if diagnosis.selected_label:
        print(f"  Delivered using: {diagnosis.selected_label}")
    if diagnosis.failure_code:
        print(f"  Failure code: {diagnosis.failure_code} — {diagnosis.summary}")
        for recommendation in diagnosis.recommendations:
            print(f"    → {recommendation}")


def _report_result(result, args, engine: str) -> int:
    print()
    _line("Records extracted", PASS if result.records else FAIL, f"{len(result.records)}")
    named = sum(1 for record in result.records if record.agent_name)
    contactable = sum(1 for record in result.records if record.phone or record.email)
    _line("Records with a name", PASS if named else FAIL, f"{named}/{len(result.records)}")
    _line("Records with phone or email", PASS if contactable else WARN, f"{contactable}/{len(result.records)}")
    _line("Pages walked", PASS, str(result.pages_fetched))
    _line("Duplicates removed", PASS, str(result.quality.duplicates_removed))
    _line("Quality level", PASS if result.quality.level == "ok" else WARN, result.quality.level)
    for warning in (result.quality.warnings + result.inspection.warnings)[:8]:
        print(f"       ! {warning}")
    _diagnosis(result)

    if result.records:
        print("\nFirst records:")
        for record in result.records[:3]:
            print(f"  • {record.agent_name or '(no name)'} | {record.phone or '—'} | "
                  f"{record.email or '—'} | {record.brokerage_name or '—'}")

    failures = 0 if result.records else 1
    ensure_dirs()
    basename = basename_for(result)
    extras = available_extra_keys(result.records)
    csv_path = EXPORT_DIR / f"{basename}.csv"
    xlsx_path = EXPORT_DIR / f"{basename}.xlsx"
    try:
        csv_path.write_bytes(to_csv_bytes(result.records, result.selected_fields, extras))
        _line("CSV export", PASS, str(csv_path))
    except Exception as exc:
        _line("CSV export", FAIL, str(exc))
        failures += 1
    try:
        xlsx_path.write_bytes(to_excel_bytes(
            result, result.selected_fields, extra_keys=extras,
            include_issues=True, context=result.export_context,
        ))
        _line("Excel export", PASS, str(xlsx_path))
    except Exception as exc:
        _line("Excel export", FAIL, str(exc))
        failures += 1

    if args.json_out:
        evidence = {
            "target": args.url,
            "engine": engine,
            "python": sys.version,
            "platform": platform.platform(),
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "records": len(result.records),
            "named": named,
            "contactable": contactable,
            "pages": result.pages_fetched,
            "quality": result.quality.to_dict(),
            "diagnosis": result.diagnosis.to_dict(),
            "inspection": result.inspection.to_dict(),
            "sample": [record.to_row() for record in result.records[:5]],
        }
        Path(args.json_out).write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
        _line("JSON evidence", PASS, args.json_out)

    print("\n" + "-" * 72)
    if failures:
        print(f"RESULT: FAIL ({failures} check(s) failed)")
        return 1
    print("RESULT: PASS — records were extracted and both exports were written.")
    print()
    print("Still unverified by this run:")
    print("  • other layouts and brokerages (each needs its own run)")
    print("  • pagination beyond the page limit used here")
    print("  • profile contact extraction unless --enrich was passed")
    print("  • whether your use of the data is permitted — that is your call, not the tool's")
    return 0


def _run_browser(args, settings):
    ok, message = diagnose()
    _line("Browser available", PASS if ok else FAIL, message.splitlines()[0] if message else "")
    if not ok:
        print(message)
        print("\nUse --engine static to test the server-rendered path without Chromium.")
        return None, 2

    try:
        report = inspect_in_browser(args.url, settings=settings)
    except BrowserBlocked as exc:
        _line("Open page", FAIL, str(exc))
        return None, 1
    except BrowserError as exc:
        _line("Open page", FAIL, str(exc))
        return None, 1

    _line("Browser opened the page", PASS, f"HTTP {report.status_code}, “{report.title[:50]}”")
    _line("DOM rows detected", PASS if report.row_count else WARN, str(report.row_count))
    _line("JSON-LD listing records", PASS if report.jsonld_record_count else WARN,
          str(report.jsonld_record_count))
    _line("Embedded records", PASS if report.embedded_record_count else WARN,
          str(report.embedded_record_count))
    _line("Profile links", PASS if report.profile_link_count else WARN, str(report.profile_link_count))
    _line("Pagination", WARN if report.pagination.get("strategy") in (None, "none") else PASS,
          str(report.pagination.get("strategy")))
    if report.detected_layers:
        print("       layers: " + " · ".join(report.detected_layers))
    if not report.robots_allowed:
        _line("robots.txt", WARN, report.robots_detail or "disallowed")

    if report.structure:
        print("\nDetected columns:")
        for column in report.structure.columns[:14]:
            sample = (column.sample_values[0] if column.sample_values else "")[:44]
            header = f" [{column.header}]" if column.header else ""
            print(f"  [{column.index:2d}] {column.kind_hint:12s} fill={column.fill_rate:.0%}{header}  “{sample}”")
    if report.sample_records:
        print("\nSample record:")
        for key, value in list(report.sample_records[0].items())[:10]:
            print(f"  {key:16s} {value}")

    if args.inspect_only:
        return None, 0

    mapping = suggest_mapping(report.structure) if report.structure else None
    print(f"\nAuto-mapping: {mapping.columns if mapping else 'embedded/structured data'}")
    try:
        result = run_browser_extraction(
            args.url,
            mapping=mapping,
            structure=report.structure,
            selected_fields=args.fields,
            max_pages=args.max_pages,
            max_records=args.max_records,
            enrich_profiles=args.enrich,
            max_profiles=args.enrich and 1 or 0,
            settings=settings,
        )
    except BrowserBlocked as exc:
        _line("Extraction", FAIL, f"blocked: {exc}")
        return None, 1
    except BrowserError as exc:
        _line("Extraction", FAIL, str(exc))
        return None, 1
    return result, 0


def _run_static(args, settings):
    fetcher = PoliteFetcher(settings)
    if args.inspect_only:
        try:
            report = inspect_url(args.url, fetcher)
        except (FetchError, RobotsDisallowed) as exc:
            _line("Inspect", FAIL, str(exc))
            return None, 1
        _line("HTTP status", PASS, str(report.status_code))
        _line("Profile links", PASS if report.profile_link_count else WARN, str(report.profile_link_count))
        _line("JSON-LD blocks", PASS if report.jsonld_blocks else WARN, str(report.jsonld_blocks))
        _line("Embedded records", PASS if report.embedded_record_count else WARN,
              str(report.embedded_record_count))
        _line("Pagination", WARN if report.pagination_type == "none" else PASS, report.pagination_type)
        _line("Sitemap", PASS if report.sitemap_found else WARN, "found" if report.sitemap_found else "not found")
        if report.detected_layers:
            print("       layers: " + " · ".join(report.detected_layers))
        for warning in report.warnings:
            print(f"       ! {warning}")
        return None, 0

    try:
        result = run_extraction(
            args.url,
            selected_fields=args.fields,
            settings=settings,
            max_list_pages=args.max_pages,
            max_profiles=args.max_records,
        )
    except (RobotsDisallowed, BlockedError) as exc:
        _line("Extraction", FAIL, f"compliance stop: {exc}")
        return None, 1
    except FetchError as exc:
        _line("Extraction", FAIL, str(exc))
        return None, 1
    _line("HTTP status", PASS, str(result.inspection.status_code))
    _line("Server HTML", PASS, "opened")
    return result, 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify extraction against a real directory.")
    parser.add_argument("url")
    parser.add_argument("--engine", choices=("browser", "static", "auto"), default="browser",
                        help="browser uses Chromium; static uses server HTML; auto tries browser then static.")
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--max-records", type=int, default=10)
    parser.add_argument("--enrich", action="store_true", help="Also read one profile page (browser).")
    parser.add_argument("--fields", default="", help="Comma-separated field keys; default core fields.")
    parser.add_argument("--inspect-only", action="store_true", help="Static inspection only.")
    parser.add_argument("--json-out", default="", help="Write a JSON evidence file here.")
    parser.add_argument("--allow-local", action="store_true",
                        help="Permit 127.0.0.1/localhost (for the bundled demo server).")
    parser.add_argument("--headless", action="store_true", default=True)
    args = parser.parse_args()
    args.fields = _fields(args.fields)
    settings = dataclasses.replace(
        SETTINGS, allow_local_urls=args.allow_local, request_delay_seconds=(
            0 if args.allow_local else SETTINGS.request_delay_seconds
        )
    )

    print("=" * 72)
    print("B2BProspects — live verification")
    print("=" * 72)
    print(f"Target: {args.url}")
    print(f"Engine: {args.engine}\n")

    engine = args.engine
    if engine in ("browser", "auto"):
        result, code = _run_browser(args, settings)
        if code == 2 and engine == "auto":
            print("\nFalling back to static extraction.\n")
            engine = "static"
            result, code = _run_static(args, settings)
        elif code:
            return code
    else:
        result, code = _run_static(args, settings)
        if code:
            return code

    if result is None:
        return 0 if args.inspect_only else 1
    return _report_result(result, args, engine)


if __name__ == "__main__":
    sys.exit(main())
