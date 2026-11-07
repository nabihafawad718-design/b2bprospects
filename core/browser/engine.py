"""Browser extraction orchestration.

Layered strategy, cheapest and most reliable first:
  1. embedded JSON payload (no DOM guessing at all),
  2. rendered DOM with a detected repeated structure,
  3. the same structure across pagination,
  4. optional per-profile enrichment.

The engine returns the same `RunResult` as the static pipeline, so quality scoring,
duplicate removal, health tracking, history and export are shared.
"""
from __future__ import annotations

import time
from typing import Callable

from ..engine import _noop
from ..models import (
    BrowserInspection,
    CORE_FIELD_KEYS,
    HealthReport,
    InspectionReport,
    RunResult,
    new_run_id,
    utc_now,
)
from ..net import FetchError
from ..normalize import host_of
from ..quality import build_record, dedupe, evaluate
from ..runs import append_run, compute_health, runs_for
from ..settings import SETTINGS, Settings
from .driver import (
    BrowserBlocked,
    BrowserSession,
    PlaywrightSession,
    RenderedPage,
)
from .embedded import blob_keys, extract_from_embedded
from .extract import (
    FieldMapping,
    enrich_from_profile,
    extract_page,
    same_host,
    suggest_mapping,
    to_raw_records,
)
from .pagination import (
    PaginationPlan,
    detect_pagination,
    walk_pages,
)
from .structure import analyze_html

ProgressFn = Callable[[str, int, int], None]


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------
def inspect_in_browser(
    url: str,
    session: BrowserSession | None = None,
    settings: Settings = SETTINGS,
    progress: ProgressFn | None = None,
) -> BrowserInspection:
    """Open the page in a real browser and describe what is extractable."""
    progress = progress or _noop
    owns_session = session is None
    session = session or PlaywrightSession(settings)
    report = BrowserInspection(url=url)

    try:
        progress("Opening website", 0, 1)
        page: RenderedPage = session.open(url)
        report.final_url = page.final_url
        report.title = page.title
        report.status_code = page.status
        report.rendered = True
        report.rendered_html = page.html

        progress("Analyzing page structure", 0, 1)
        html = page.html
        candidates = analyze_html(html, page.final_url)
        report.candidates = candidates
        report.structure = candidates[0] if candidates else None
        report.row_count = report.structure.row_count if report.structure else 0

        report.embedded_keys = blob_keys(html)
        embedded_records, _payload = extract_from_embedded(html, page.final_url)
        if embedded_records and report.row_count == 0:
            report.warnings.append(
                f"{len(embedded_records)} records are available as an embedded JSON payload "
                "even though the page has no repeated DOM structure."
            )

        plan = detect_pagination(
            html, page.final_url, report.structure.row_selector if report.structure else ""
        )
        report.pagination = plan.to_dict()

        if not candidates:
            report.warnings.append(
                "No repeated records were detected on this page. It may be a single-profile "
                "page, a search form, or a layout this detector does not recognise."
            )
        if report.structure and report.structure.row_count < 3:
            report.warnings.append(
                f"Only {report.structure.row_count} repeated rows were found. Check the preview "
                "carefully before running a full extraction."
            )
        return report
    finally:
        if owns_session:
            session.close()


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def _browser_inspection_report(report: BrowserInspection, structure_row_count: int,
                               fields: list[str]) -> InspectionReport:
    return InspectionReport(
        url=report.url,
        final_url=report.final_url,
        host=host_of(report.final_url or report.url),
        status_code=report.status_code,
        page_title=report.title,
        container_count=structure_row_count,
        container_signature=report.structure.signature if report.structure else "",
        container_selector=report.structure.row_selector if report.structure else "",
        available_fields=fields,
        warnings=list(report.warnings),
    )


def run_browser_extraction(
    url: str,
    mapping: FieldMapping | None = None,
    structure=None,
    selected_fields: list[str] | tuple[str, ...] = CORE_FIELD_KEYS,
    strategy: str = "auto",
    max_pages: int | None = None,
    max_records: int | None = None,
    enrich_profiles: bool = False,
    max_profiles: int = 25,
    session: BrowserSession | None = None,
    settings: Settings = SETTINGS,
    progress: ProgressFn | None = None,
    persist: bool = True,
) -> RunResult:
    """Run a full browser extraction. Raises BrowserBlocked/BrowserError on hard stops."""
    progress = progress or _noop
    owns_session = session is None
    session = session or PlaywrightSession(settings)

    fields = list(dict.fromkeys(selected_fields)) or list(CORE_FIELD_KEYS)
    page_limit = max(1, min(max_pages or settings.max_list_pages, 100))
    record_limit = max(1, min(max_records or settings.max_profiles, 20_000))

    started = time.monotonic()
    result = RunResult(
        run_id=new_run_id(url),
        started_at=utc_now(),
        url=url,
        adapter_id="browser_dom",
        adapter_label="Browser DOM extraction",
        selected_fields=fields,
    )

    try:
        progress("Opening website", 0, 1)
        page = session.open(url)
        html = page.html

        progress("Analyzing page structure", 0, 1)
        if structure is None:
            candidates = analyze_html(html, page.final_url)
            structure = candidates[0] if candidates else None

        browser_report = BrowserInspection(
            url=url, final_url=page.final_url, title=page.title, status_code=page.status,
            rendered=True,
            row_count=structure.row_count if structure else 0,
            structure=structure,
        )

        # --- layer 1: embedded JSON ---------------------------------------
        embedded_records, payload_name = extract_from_embedded(html, page.final_url)
        if structure is None and len(embedded_records) >= 3:
            progress("Extracting records", 0, len(embedded_records))
            records = [build_record(r, fields, "embedded_json") for r in embedded_records]
            result.inspection = _browser_inspection_report(browser_report, 0, fields)
            result.inspection.warnings.append(f"Records came from the embedded payload “{payload_name}”.")
            return _finish(result, records, len(embedded_records), 1, 0, settings, persist,
                           started, progress)

        if structure is None:
            result.inspection = _browser_inspection_report(browser_report, 0, fields)
            result.errors.append(
                "No repeating record structure was detected on this page, and no embedded "
                "payload was found. Open the page in a browser: if the listings appear only "
                "after interacting with the site, this layout needs a dedicated adapter."
            )
            result.health = HealthReport(status="broken", reasons=list(result.errors))
            result.quality = evaluate([], 0, 0, fields, settings)
            result.quality.level = "broken"
            result.finished_at = utc_now()
            result.elapsed_seconds = time.monotonic() - started
            if persist:
                append_run(result.summary())
            return result

        if mapping is None:
            mapping = suggest_mapping(structure)
        if not mapping.columns:
            result.errors.append(
                "No fields are mapped. Choose which detected column contains the business name, "
                "phone or email before extracting."
            )
            result.quality = evaluate([], 0, 0, fields, settings)
            result.quality.level = "broken"
            result.finished_at = utc_now()
            result.elapsed_seconds = time.monotonic() - started
            if persist:
                append_run(result.summary())
            return result

        progress("Detecting records", 0, 1)
        plan = detect_pagination(html, page.final_url, structure.row_selector)
        if strategy and strategy != "auto":
            plan = PaginationPlan(strategy=strategy, selector=plan.selector,
                                  page_urls=plan.page_urls, reason="Selected manually.")
        plan.max_pages = page_limit

        # --- layer 2/3: rendered DOM across pages --------------------------
        def handler(page_html: str, page_url: str) -> list[dict]:
            return extract_page(page_html, page_url, structure, mapping)

        rows, stats = walk_pages(
            session,
            plan,
            handler,
            max_pages=page_limit,
            max_records=record_limit,
            row_selector=structure.row_selector,
            progress=lambda stage, done, total: progress(stage, done, total),
        )

        result.pages_fetched = stats.pages
        result.errors.extend(stats.warnings)
        if not rows:
            result.inspection = _browser_inspection_report(browser_report, structure.row_count, fields)
            result.inspection.warnings.append(stats.stopped_reason)
            result.errors.append(
                "The page structure was detected but no records passed the field mapping. "
                "Re-check which column holds the name."
            )
            result.quality = evaluate([], 0, 0, fields, settings)
            result.quality.level = "broken"
            result.health = HealthReport(status="broken", reasons=list(result.errors))
            result.finished_at = utc_now()
            result.elapsed_seconds = time.monotonic() - started
            if persist:
                append_run(result.summary())
            return result

        # --- layer 4: profile enrichment -----------------------------------
        if enrich_profiles and "profile_url" in mapping.columns:
            rows = _enrich(rows, session, url, max_profiles, settings, progress, result)

        progress("Cleaning data", 0, 1)
        raw = to_raw_records(rows, page.final_url)
        records = [build_record(r, fields, "browser_dom") for r in raw]

        result.inspection = _browser_inspection_report(browser_report, structure.row_count, fields)
        if stats.stopped_reason:
            result.inspection.warnings.append(stats.stopped_reason)
        if plan.strategy == "none":
            result.inspection.warnings.append(
                "No pagination was found; only the records visible on the first page were collected."
            )

        return _finish(result, records, len(rows), stats.pages, stats.clicked, settings, persist,
                       started, progress)
    finally:
        if owns_session:
            session.close()


def _enrich(rows: list[dict], session: BrowserSession, listing_url: str, max_profiles: int,
            settings: Settings, progress: ProgressFn, result: RunResult) -> list[dict]:
    candidates = [
        row for row in rows
        if row.get("profile_url") and same_host(row["profile_url"], listing_url)
    ][:max_profiles]

    for index, row in enumerate(candidates, start=1):
        progress(f"Reading profile {index} of {len(candidates)}", index, len(candidates))
        try:
            page = session.open(row["profile_url"])
        except BrowserBlocked as exc:
            result.errors.append(f"Profile enrichment stopped: {exc}")
            break
        except FetchError as exc:
            result.errors.append(f"Profile could not be opened ({row['profile_url']}): {exc}")
            continue
        except Exception as exc:
            result.errors.append(f"Profile could not be opened ({row['profile_url']}): {exc}")
            continue

        extra = enrich_from_profile(page.html, page.final_url)
        for key, value in extra.items():
            if value and not row.get(key):
                row[key] = value
        if settings.request_delay_seconds:
            session.wait(int(settings.request_delay_seconds * 1000))

    if len(candidates) < len([r for r in rows if r.get("profile_url")]):
        result.errors.append(
            f"Profile enrichment was capped at {len(candidates)} pages; other records keep their "
            "listing-page fields only."
        )
    return rows


def _finish(result: RunResult, records, attempted: int, pages: int, clicks: int,
            settings: Settings, persist: bool, started: float,
            progress: ProgressFn) -> RunResult:
    progress("Preparing export", 0, 1)
    unique, duplicates_removed = dedupe(records)
    result.records = unique
    result.quality = evaluate(unique, attempted, duplicates_removed,
                              ("agent_name", "phone", "email"), settings)

    result.finished_at = utc_now()
    result.elapsed_seconds = time.monotonic() - started
    preliminary = result.summary()
    prior = runs_for(result.url, result.adapter_id)
    result.health = compute_health(preliminary, prior, settings)

    if persist:
        error = append_run(result.summary())
        if error:
            result.errors.append(error)
    return result


def analyze_and_run(
    url: str,
    selected_fields: list[str] | tuple[str, ...] = CORE_FIELD_KEYS,
    **kwargs,
) -> RunResult:
    """Convenience for scripts: inspect, map automatically, extract."""
    inspection = inspect_in_browser(url)
    if not inspection.structure:
        raise FetchError(
            "No repeating records were detected on that page. Try a different listing URL."
        )
    return run_browser_extraction(
        url,
        mapping=suggest_mapping(inspection.structure),
        structure=inspection.structure,
        selected_fields=selected_fields,
        **kwargs,
    )
