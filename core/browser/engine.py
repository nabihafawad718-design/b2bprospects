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
from pathlib import Path
from typing import Callable

from ..engine import _noop
from ..inspect_site import parse_html
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
from ..normalize import clean_text, host_of
from ..quality import build_record, dedupe, evaluate
from ..runs import append_run, compute_health, runs_for
from ..settings import SETTINGS, Settings
from .driver import (
    BrowserBlocked,
    BrowserError,
    BrowserSession,
    PlaywrightSession,
    RenderedPage,
)
from .embedded import blob_keys, extract_from_embedded
from .extract import (
    FieldMapping,
    enrich_from_profile,
    extract_page,
    merge_raw_records,
    same_host,
    suggest_mapping,
    to_raw_records,
)
from .pagination import (
    PaginationPlan,
    detect_pagination,
    walk_pages,
)
from .structure import analyze_html, select_rows

ProgressFn = Callable[[str, int, int], None]

_KIND_TO_FIELD = {
    "name": "agent_name",
    "phone": "phone",
    "email": "email",
    "address": "street_address",
    "city": "city",
    "brokerage": "brokerage_name",
    "website": "website",
    "url": "profile_url",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _page_text_length(html: str) -> int:
    soup = parse_html(html)
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return len(clean_text(soup.get_text(" ")))


def _profile_link_count(html: str, structure) -> int:
    if structure is None:
        return 0
    try:
        rows = select_rows(html, structure)
    except Exception:
        return 0
    count = 0
    for row in rows:
        if row.find("a", href=True):
            count += 1
    return count


def _embedded_page_rows(html: str, page_url: str) -> list[dict]:
    """Adapter for walk_pages: records from an embedded payload on any page."""
    records, payload = extract_from_embedded(html, page_url)
    rows: list[dict] = []
    for record in records:
        fields = dict(record.fields or {})
        fields["_key"] = record.profile_url or "|".join(
            str(fields.get(key, "")) for key in ("agent_name", "phone", "email")
        )
        fields["_payload"] = payload
        fields["_source_url"] = page_url
        rows.append(fields)
    return rows


def _available_fields(structure, embedded_count: int) -> list[str]:
    fields: list[str] = []
    if structure is not None:
        for column in structure.columns:
            target = _KIND_TO_FIELD.get(column.kind_hint)
            if target and target not in fields:
                fields.append(target)
    if embedded_count:
        for key in ("agent_name", "brokerage_name", "phone", "email", "street_address",
                    "city", "state", "postal_code", "website", "profile_url"):
            if key not in fields:
                fields.append(key)
    return fields


def _maybe_auto_scroll(session: BrowserSession, enabled: bool) -> None:
    if not enabled:
        return
    scroll = getattr(session, "auto_scroll", None)
    if callable(scroll):
        try:
            scroll()
        except Exception:
            pass


def _save_screenshot(session: BrowserSession, settings: Settings, url: str) -> str:
    if not settings.browser_screenshot_on_error:
        return ""
    shot = getattr(session, "screenshot", None)
    if not callable(shot):
        return ""
    try:
        folder = Path(getattr(settings, "screenshot_dir", "data/screenshots"))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"browser-{new_run_id(url)}.png"
        if shot(str(path)):
            return str(path)
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------
def inspect_in_browser(
    url: str,
    session: BrowserSession | None = None,
    settings: Settings = SETTINGS,
    progress: ProgressFn | None = None,
    auto_scroll: bool = True,
) -> BrowserInspection:
    """Open the page in a real browser and describe what is extractable."""
    progress = progress or _noop
    owns_session = session is None
    session = session or PlaywrightSession(settings)
    report = BrowserInspection(url=url)

    try:
        progress("Opening website", 0, 1)
        page: RenderedPage = session.open(url)
        _maybe_auto_scroll(session, auto_scroll)
        report.final_url = session.current_url() or page.final_url
        report.title = page.title
        report.status_code = page.status
        report.rendered = True
        report.robots_allowed = getattr(page, "robots_allowed", True)
        report.robots_detail = getattr(page, "robots_detail", "")

        progress("Analyzing page structure", 0, 1)
        html = session.content() or page.html
        report.rendered_html = html
        report.page_text_length = _page_text_length(html)

        candidates = analyze_html(html, report.final_url)
        report.candidates = candidates
        report.structure = candidates[0] if candidates else None
        report.row_count = report.structure.row_count if report.structure else 0
        report.profile_link_count = _profile_link_count(html, report.structure)

        report.embedded_keys = blob_keys(html)
        embedded_records, payload = extract_from_embedded(html, report.final_url)
        report.embedded_record_count = len(embedded_records)
        report.embedded_payloads = [payload] if payload else []
        report.available_fields = _available_fields(report.structure, len(embedded_records))

        plan = detect_pagination(
            html, report.final_url, report.structure.row_selector if report.structure else ""
        )
        report.pagination = plan.to_dict()

        if not candidates and len(embedded_records) < 3:
            report.warnings.append(
                "No repeated records were detected on this page. It may be a single-profile "
                "page, a search form, or a layout this detector does not recognise."
            )
        elif not candidates and embedded_records:
            report.warnings.append(
                f"{len(embedded_records)} records are available in the embedded payload "
                f"“{payload}” but the page has no repeated DOM structure; the browser engine "
                "will extract from that payload."
            )
        if report.structure and report.structure.row_count < 3:
            report.warnings.append(
                f"Only {report.structure.row_count} repeated rows were found. Check the preview "
                "carefully before running a full extraction."
            )
        if not report.robots_allowed:
            report.warnings.append(
                f"robots.txt note: {report.robots_detail}. This is reported, not enforced, "
                "because robots.txt is a crawling convention rather than a statement of permission. "
                "Review the site's terms before commercial use."
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
        robots_allowed=report.robots_allowed,
        robots_detail=report.robots_detail,
        container_count=structure_row_count,
        container_signature=report.structure.signature if report.structure else "",
        container_selector=report.structure.row_selector if report.structure else "",
        available_fields=fields,
        warnings=list(report.warnings),
    )


def _empty_result(result: RunResult, message: str, fields: list[str], settings: Settings,
                  persist: bool, started: float) -> RunResult:
    result.errors.append(message)
    result.quality = evaluate([], 0, 0, fields, settings)
    result.quality.level = "broken"
    result.health = HealthReport(status="broken", reasons=list(result.errors))
    result.finished_at = utc_now()
    result.elapsed_seconds = time.monotonic() - started
    if persist:
        append_run(result.summary())
    return result


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
    mode: str = "auto",
    auto_scroll: bool = True,
    reveal_contacts: bool | None = None,
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
        _maybe_auto_scroll(session, auto_scroll)
        html = session.content() or page.html
        final_url = session.current_url() or page.final_url

        progress("Analyzing page structure", 0, 1)
        if structure is None:
            candidates = analyze_html(html, final_url)
            structure = candidates[0] if candidates else None

        browser_report = BrowserInspection(
            url=url, final_url=final_url, title=page.title, status_code=page.status,
            rendered=True,
            row_count=structure.row_count if structure else 0,
            structure=structure,
            robots_allowed=getattr(page, "robots_allowed", True),
            robots_detail=getattr(page, "robots_detail", ""),
            page_text_length=_page_text_length(html),
            profile_link_count=_profile_link_count(html, structure),
        )

        embedded_records, payload_name = extract_from_embedded(html, final_url)
        browser_report.embedded_record_count = len(embedded_records)
        browser_report.embedded_payloads = [payload_name] if payload_name else []
        browser_report.embedded_keys = blob_keys(html)
        browser_report.available_fields = _available_fields(structure, len(embedded_records))

        use_embedded = False
        if mode == "embedded":
            use_embedded = bool(embedded_records)
        elif mode == "dom":
            use_embedded = False
        else:
            if len(embedded_records) >= 3:
                if structure is None:
                    use_embedded = True
                elif settings.prefer_embedded and len(embedded_records) >= max(3, structure.row_count * 0.6):
                    use_embedded = True

        # --- layer 1: embedded JSON ---------------------------------------
        if use_embedded:
            if len(embedded_records) < 3:
                result.inspection = _browser_inspection_report(browser_report, 0, fields)
                return _empty_result(
                    result,
                    "The embedded payload did not contain enough records to extract.",
                    fields, settings, persist, started,
                )
            result.adapter_id = "embedded_json"
            result.adapter_label = f"Embedded JSON ({payload_name or 'payload'})"
            progress("Detecting records", 0, 1)
            plan = detect_pagination(html, final_url, structure.row_selector if structure else "")
            if strategy and strategy != "auto":
                plan = PaginationPlan(
                    strategy=strategy, selector=plan.selector,
                    selector_candidates=plan.selector_candidates, text=plan.text,
                    page_urls=plan.page_urls, param=plan.param, param_step=plan.param_step, reason="Selected manually.",
                )
            plan.max_pages = page_limit

            rows, stats = walk_pages(
                session, plan, _embedded_page_rows,
                max_pages=page_limit, max_records=record_limit,
                row_selector=structure.row_selector if structure else "",
                progress=lambda stage, done, total: progress(stage, done, total),
                delay_ms=int(settings.request_delay_seconds * 1000),
            )
            result.pages_fetched = stats.pages
            result.errors.extend(stats.warnings)
            if not rows:
                result.inspection = _browser_inspection_report(browser_report, 0, fields)
                return _empty_result(
                    result,
                    "The embedded payload produced no usable records.",
                    fields, settings, persist, started,
                )

            raw_records = to_raw_records(rows, final_url, f"embedded-json ({payload_name})")

            # Merge first-page DOM fields (website, address, extra columns) into
            # the embedded records when a mapping is available.
            if structure is not None and mode != "embedded":
                dom_mapping = mapping or suggest_mapping(structure)
                if dom_mapping.columns:
                    try:
                        dom_rows = extract_page(html, final_url, structure, dom_mapping)
                        raw_records = merge_raw_records(
                            raw_records, to_raw_records(dom_rows, final_url, "browser-dom")
                        )
                    except Exception as exc:
                        result.errors.append(f"DOM enrichment of embedded records failed: {exc}")
        else:
            # --- layer 2/3: rendered DOM across pages ----------------------
            if structure is None:
                result.inspection = _browser_inspection_report(browser_report, 0, fields)
                return _empty_result(
                    result,
                    "No repeating record structure was detected on this page, and no embedded "
                    "payload was found. Open the page in a browser: if the listings appear only "
                    "after interacting with the site, this layout needs a dedicated adapter.",
                    fields, settings, persist, started,
                )

            if mapping is None:
                mapping = suggest_mapping(structure)
            if not mapping.columns:
                result.inspection = _browser_inspection_report(browser_report, structure.row_count, fields)
                return _empty_result(
                    result,
                    "No fields are mapped. Choose which detected column contains the business name, "
                    "phone or email before extracting.",
                    fields, settings, persist, started,
                )

            progress("Detecting records", 0, 1)
            plan = detect_pagination(html, final_url, structure.row_selector)
            if strategy and strategy != "auto":
                plan = PaginationPlan(
                    strategy=strategy, selector=plan.selector,
                    selector_candidates=plan.selector_candidates, text=plan.text,
                    page_urls=plan.page_urls, param=plan.param, param_step=plan.param_step, reason="Selected manually.",
                )
            plan.max_pages = page_limit

            def handler(page_html: str, page_url: str) -> list[dict]:
                return extract_page(page_html, page_url, structure, mapping)

            rows, stats = walk_pages(
                session, plan, handler,
                max_pages=page_limit, max_records=record_limit,
                row_selector=structure.row_selector,
                progress=lambda stage, done, total: progress(stage, done, total),
                delay_ms=int(settings.request_delay_seconds * 1000),
            )

            result.pages_fetched = stats.pages
            result.errors.extend(stats.warnings)
            if not rows:
                result.inspection = _browser_inspection_report(browser_report, structure.row_count, fields)
                result.inspection.warnings.append(stats.stopped_reason)
                return _empty_result(
                    result,
                    "The page structure was detected but no records passed the field mapping. "
                    "Re-check which column holds the name.",
                    fields, settings, persist, started,
                )

            raw_records = to_raw_records(rows, final_url, "browser-dom")

        # --- layer 4: profile enrichment -----------------------------------
        if enrich_profiles and any((record.fields or {}).get("profile_url") for record in raw_records):
            raw_records = _enrich_records(raw_records, session, url, max_profiles, settings,
                                          progress, result, reveal_contacts)

        progress("Cleaning data", 0, 1)
        records = [build_record(raw, fields, result.adapter_id) for raw in raw_records]

        result.inspection = _browser_inspection_report(
            browser_report, structure.row_count if structure else 0, fields
        )
        if not use_embedded and stats.stopped_reason:
            result.inspection.warnings.append(stats.stopped_reason)
        if not use_embedded and plan.strategy == "none":
            result.inspection.warnings.append(
                "No pagination was found; only the records visible on the first page were collected."
            )
        if use_embedded:
            result.inspection.warnings.append(
                f"Records came from the embedded payload “{payload_name or 'payload'}”."
            )

        result.export_context = {
            "Extraction mode": "embedded JSON" if use_embedded else "rendered DOM",
            "Pagination strategy": plan.strategy,
            "Page cap": str(page_limit),
            "Record cap": str(record_limit),
        }
        return _finish(result, records, len(raw_records), stats.pages, stats.clicked,
                       settings, persist, started, progress, fields)
    except BrowserBlocked:
        shot = _save_screenshot(session, settings, url)
        if shot:
            result.screenshots.append(shot)
        raise
    except BrowserError:
        shot = _save_screenshot(session, settings, url)
        if shot:
            result.screenshots.append(shot)
        raise
    finally:
        if owns_session:
            session.close()


def stats_warnings(stats) -> list[str]:
    if stats is None:
        return []
    return list(stats.warnings or [])


def _enrich_records(raw_records, session: BrowserSession, listing_url: str, max_profiles: int,
                    settings: Settings, progress: ProgressFn, result: RunResult,
                    reveal_contacts: bool | None = None) -> list:
    candidates = [
        record for record in raw_records
        if (record.fields or {}).get("profile_url")
        and same_host((record.fields or {})["profile_url"], listing_url)
    ][:max_profiles]

    reveal = settings.browser_reveal_contacts if reveal_contacts is None else reveal_contacts
    result.profiles_attempted = len(candidates)
    list_page_url = session.current_url()
    for index, record in enumerate(candidates, start=1):
        profile_url = (record.fields or {}).get("profile_url", "")
        progress(f"Reading profile {index} of {len(candidates)}", index, len(candidates))
        try:
            page = session.open(profile_url)
        except BrowserBlocked as exc:
            result.errors.append(f"Profile enrichment stopped: {exc}")
            break
        except FetchError as exc:
            result.errors.append(f"Profile could not be opened ({profile_url}): {exc}")
            continue
        except Exception as exc:
            result.errors.append(f"Profile could not be opened ({profile_url}): {exc}")
            continue

        # Ordinary interaction: reveal contact blocks a visitor could open.
        if reveal:
            revealer = getattr(session, "reveal_contacts", None)
            if callable(revealer):
                try:
                    revealer()
                except Exception:
                    pass

        html = session.content() or page.html
        extra = enrich_from_profile(html, page.final_url)
        for key, value in extra.items():
            if value and not (record.fields or {}).get(key):
                record.fields[key] = value
        if settings.request_delay_seconds:
            session.wait(int(settings.request_delay_seconds * 1000))

    # Return to the listing so later pagination (if any) is not lost.
    if list_page_url:
        try:
            session.open(list_page_url)
        except Exception:
            pass

    if len(candidates) < len([r for r in raw_records if (r.fields or {}).get("profile_url")]):
        result.errors.append(
            f"Profile enrichment was capped at {len(candidates)} pages; other records keep their "
            "listing-page fields only."
        )
    return raw_records


def _finish(result: RunResult, records, attempted: int, pages: int, clicks: int,
            settings: Settings, persist: bool, started: float,
            progress: ProgressFn, expected_fields=None) -> RunResult:
    progress("Preparing export", 0, 1)
    unique, duplicates_removed = dedupe(records)
    result.records = unique
    result.quality = evaluate(
        unique, attempted, duplicates_removed,
        tuple(expected_fields or ("agent_name", "phone", "email")),
        settings,
    )

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
    if not inspection.structure and inspection.embedded_record_count < 3:
        raise FetchError(
            "No repeating records were detected on that page. Try a different listing URL."
        )
    return run_browser_extraction(
        url,
        mapping=suggest_mapping(inspection.structure) if inspection.structure else None,
        structure=inspection.structure,
        selected_fields=selected_fields,
        **kwargs,
    )
