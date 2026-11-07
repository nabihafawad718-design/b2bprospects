#!/usr/bin/env python3
"""Pre-engagement assessment for a candidate directory.

Run this before quoting a customer or writing an adapter:

    python scripts/assess_site.py https://example.com/agents

It reports what the server sends, whether robots.txt permits the path, whether the
page needs a browser, and what data is visibly present. It does NOT decide whether
you have permission to collect or redistribute — that is a business and legal
judgement the checklist at the end asks you to make explicitly.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.browser.embedded import blob_keys                      # noqa: E402
from core.browser.structure import analyze_html                   # noqa: E402
from core.inspect_site import inspect_url                         # noqa: E402
from core.net import FetchError, PoliteFetcher, RobotsDisallowed  # noqa: E402
from core.normalize import host_of                                # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Assess a directory before committing to it.")
    parser.add_argument("url", help="The listing/roster URL")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between requests")
    args = parser.parse_args()

    fetcher = PoliteFetcher()
    print("=" * 72)
    print("B2BProspects — directory assessment")
    print("=" * 72)
    print(f"URL     : {args.url}")
    print(f"Host    : {host_of(args.url)}")
    print()

    try:
        report = inspect_url(args.url, fetcher)
    except RobotsDisallowed as exc:
        print(f"STOP: robots.txt disallows this path.\n  {exc}")
        print("\nDo not proceed without written permission from the site owner.")
        return 1
    except FetchError as exc:
        print(f"Could not fetch the page: {exc}")
        return 2

    print(f"HTTP status      : {report.status_code}")
    print(f"Final URL        : {report.final_url}")
    print(f"Page title       : {report.page_title or '(none)'}")
    print(f"robots.txt       : {report.robots_detail or 'not checked'}")
    print(f"Content type     : HTML")
    print()

    print("--- What the server sends (no JavaScript) ---")
    print(f"JSON-LD blocks   : {report.jsonld_blocks}  types={report.jsonld_types[:5]}")
    print(f"Profile links    : {report.profile_link_count}")
    print(f"Repeated blocks  : {report.container_count}  selector={report.container_selector or '—'}")
    print(f"Pagination       : {report.pagination_type}")
    print(f"Load-more        : {'yes' if report.load_more_detected else 'no'}")
    print()

    html = _safe_html(fetcher, report.final_url)
    structures = analyze_html(html, report.final_url)
    if structures:
        best = structures[0]
        print("--- Static extraction appears possible ---")
        print(f"Rows detected    : {best.row_count}")
        for column in best.columns[:8]:
            sample = (column.sample_values[0] if column.sample_values else "")[:40]
            print(f"  [{column.index}] {column.kind_hint:10s} “{sample}”")
    else:
        print("--- No static records detected ---")
        print("This page is probably rendered by JavaScript. Use the browser engine,")
        print("or check whether the site exposes an embedded JSON payload.")

    blobs = blob_keys(html)
    if blobs:
        print(f"Embedded payloads: {', '.join(blobs[:5])}")
    print()

    print("--- Permission checklist (you must confirm each of these) ---")
    print("  [ ] The site's terms of use permit this collection for your purpose.")
    print("  [ ] The data is a public professional/business listing, not private data.")
    print("  [ ] You are not bypassing a login, paywall, CAPTCHA or access control.")
    print("  [ ] Your customer agreement covers source, scope and collection date.")
    print("  [ ] Where required by law, you have a lawful basis for processing.")
    print()
    print("robots.txt is one input, not proof of permission.")
    return 0


def _safe_html(fetcher: PoliteFetcher, url: str) -> str:
    try:
        return fetcher.get(url).text
    except FetchError:
        return ""


if __name__ == "__main__":
    sys.exit(main())
