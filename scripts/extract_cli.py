#!/usr/bin/env python3
"""Headless extraction from the command line.

Examples
--------
    python scripts/extract_cli.py https://example.com/agents
    python scripts/extract_cli.py https://example.com/agents --inspect
    python scripts/extract_cli.py https://example.com/agents --engine browser --max-pages 5
    python scripts/extract_cli.py http://127.0.0.1:8765/agents --allow-local --format all

The static engine is used by default. `--engine auto` falls back to the browser
when the static page has no usable records and Playwright is installed.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.browser.driver import BrowserBlocked, BrowserError, playwright_available  # noqa: E402
from core.browser.engine import inspect_in_browser, run_browser_extraction           # noqa: E402
from core.browser.extract import suggest_mapping                                     # noqa: E402
from core.cleaning import available_extra_keys                                       # noqa: E402
from core.engine import run_extraction                                               # noqa: E402
from core.exporters import (                                                         # noqa: E402
    basename_for,
    to_csv_bytes,
    to_excel_bytes,
    to_json_bytes,
)
from core.inspect_site import inspect_url                                            # noqa: E402
from core.models import CORE_FIELD_KEYS, FIELD_BY_KEY                                # noqa: E402
from core.net import BlockedError, FetchError, PoliteFetcher, RobotsDisallowed       # noqa: E402
from core.settings import EXPORT_DIR, SETTINGS, ensure_dirs                          # noqa: E402


def _progress(stage: str, done: int, total: int) -> None:
    suffix = f" ({done}/{total})" if total else ""
    print(f"  · {stage}{suffix}")


def _settings_from(args):
    overrides = {
        "allow_local_urls": bool(args.allow_local),
        "request_delay_seconds": float(args.delay),
        "max_retries": int(args.retries),
    }
    if args.robots_policy:
        overrides["robots_policy"] = args.robots_policy
        overrides["respect_robots_txt"] = args.robots_policy == "enforce"
    return dataclasses.replace(SETTINGS, **overrides)


def _fields_from(raw: str | None) -> list[str]:
    if not raw:
        return list(CORE_FIELD_KEYS)
    fields = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [field for field in fields if field not in FIELD_BY_KEY]
    if unknown:
        raise SystemExit(f"Unknown field(s): {', '.join(unknown)}. "
                         f"Valid: {', '.join(FIELD_BY_KEY)}")
    return fields


def _print_result(result) -> None:
    if result is None:
        print("\nNo extraction result was produced.")
        return
    print()
    print(f"Adapter        : {result.adapter_label} ({result.adapter_id})")
    print(f"Pages fetched  : {result.pages_fetched}")
    print(f"Profiles tried : {result.profiles_attempted}")
    print(f"Records        : {len(result.records)}")
    print(f"Duplicates     : {result.quality.duplicates_removed} removed")
    print(f"Contact share  : {result.quality.contact_share:.0%}")
    print(f"Quality        : {result.quality.level}")
    if result.quality.field_fill:
        print("Field coverage : " + ", ".join(
            f"{key}={rate:.0%}" for key, rate in result.quality.field_fill.items()
        ))
    for warning in (result.quality.warnings + result.inspection.warnings)[:8]:
        print(f"  ! {warning}")
    for error in result.errors[:8]:
        print(f"  × {error}")


def _write_exports(result, args) -> list[Path]:
    ensure_dirs()
    selected = list(result.selected_fields)
    extras = available_extra_keys(result.records)
    basename = basename_for(result)
    target = Path(args.out) if args.out else None
    if target and target.suffix:
        base_path = target.with_suffix("")
    else:
        base_path = (target or EXPORT_DIR / basename)
    base_path.parent.mkdir(parents=True, exist_ok=True)

    formats = [args.format] if args.format != "all" else ["csv", "xlsx", "json"]
    written: list[Path] = []
    for fmt in formats:
        path = base_path.with_suffix("." + fmt)
        if fmt == "csv":
            path.write_bytes(to_csv_bytes(result.records, selected, extras))
        elif fmt == "xlsx":
            path.write_bytes(to_excel_bytes(
                result, selected, extra_keys=extras, records=result.records,
                include_issues=True, context=result.export_context,
            ))
        elif fmt == "json":
            path.write_bytes(to_json_bytes(result.records, selected, extras))
        written.append(path)
    return written


def _inspect(url: str, args, settings) -> int:
    if args.engine == "browser":
        if not playwright_available():
            print("Playwright is not installed. Run: pip install playwright && "
                  "python -m playwright install chromium")
            return 2
        try:
            report = inspect_in_browser(url, settings=settings, progress=_progress)
        except (BrowserBlocked, BrowserError) as exc:
            print(f"Could not inspect in a browser: {exc}")
            return 1
        print(json.dumps(report.to_dict(), indent=2, default=str))
        return 0

    fetcher = PoliteFetcher(settings)
    try:
        report = inspect_url(url, fetcher)
    except (RobotsDisallowed, BlockedError, FetchError) as exc:
        print(f"Could not inspect: {exc}")
        return 1
    print(json.dumps(report.to_dict(), indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract a public directory into a clean dataset.")
    parser.add_argument("url")
    parser.add_argument("--engine", choices=["static", "browser", "auto"], default="static")
    parser.add_argument("--inspect", action="store_true", help="Print the inspection report and stop.")
    parser.add_argument("--fields", help="Comma-separated field keys. Default: core fields.")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--max-profiles", type=int, default=500)
    parser.add_argument("--max-records", type=int, default=5000, help="Browser record cap.")
    parser.add_argument("--enrich", action="store_true", help="Browser: read profile pages.")
    parser.add_argument("--prefer-profiles", action="store_true",
                        help="Fetch profile pages even when listing cards already carry the fields.")
    parser.add_argument("--format", choices=["csv", "xlsx", "json", "all"], default="all")
    parser.add_argument("--out", help="Output basename or file path.")
    parser.add_argument("--delay", type=float, default=SETTINGS.request_delay_seconds)
    parser.add_argument("--retries", type=int, default=SETTINGS.max_retries)
    parser.add_argument("--robots-policy", choices=["warn", "enforce", "ignore"],
                        default=None, help="Default is warn: report robots.txt, do not reject automatically.")
    parser.add_argument("--allow-local", action="store_true",
                        help="Permit 127.0.0.1/localhost (for the bundled demo server).")
    args = parser.parse_args()

    settings = _settings_from(args)
    fields = _fields_from(args.fields)

    print("=" * 72)
    print("B2BProspects extraction")
    print("=" * 72)
    print(f"URL    : {args.url}")
    print(f"Engine : {args.engine}")
    print(f"Policy : robots.txt={settings.robots_policy}")
    print()

    if args.inspect:
        return _inspect(args.url, args, settings)

    result = None
    if args.engine in ("static", "auto"):
        try:
            result = run_extraction(
                args.url,
                selected_fields=fields,
                settings=settings,
                max_list_pages=args.max_pages,
                max_profiles=args.max_profiles,
                progress=_progress,
                persist=False,
                prefer_listing_records=not args.prefer_profiles,
            )
        except (RobotsDisallowed, BlockedError) as exc:
            print(f"Stopped for compliance reasons: {exc}")
            return 1
        except FetchError as exc:
            print(f"Could not start: {exc}")
            if args.engine == "static":
                return 1
        except Exception as exc:
            print(f"Static extraction failed: {exc.__class__.__name__}: {exc}")
            if args.engine == "static":
                return 1

        if result is not None and result.records:
            _print_result(result)
        elif args.engine == "auto" and playwright_available():
            print("\nStatic extraction produced no records; trying the browser engine.")
            result = None
        elif args.engine == "auto":
            if result is not None:
                _print_result(result)
            print("\nNo records. Install Playwright to use the browser engine on JavaScript sites.")
            return 1
        else:
            _print_result(result)
            return 1

    if args.engine == "browser" or result is None:
        if not playwright_available():
            print("Playwright is not installed. Run: pip install playwright && "
                  "python -m playwright install chromium")
            return 2
        try:
            report = inspect_in_browser(args.url, settings=settings, progress=_progress)
            mapping = suggest_mapping(report.structure) if report.structure else None
            if report.structure is None and report.embedded_record_count < 3:
                print("No repeating records or embedded payload found in the browser.")
                return 1
            result = run_browser_extraction(
                args.url,
                mapping=mapping,
                structure=report.structure,
                selected_fields=fields,
                max_pages=args.max_pages,
                max_records=args.max_records,
                enrich_profiles=args.enrich,
                max_profiles=args.max_profiles,
                settings=settings,
                progress=_progress,
                persist=False,
            )
        except (BrowserBlocked, BrowserError) as exc:
            print(f"Browser extraction failed: {exc}")
            return 1
        _print_result(result)

    if not result or not result.records:
        print("\nNo records were produced. Nothing was written.")
        return 1

    written = _write_exports(result, args)
    print("\nWrote:")
    for path in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
