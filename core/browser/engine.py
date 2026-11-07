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

import re
import time
from pathlib import Path
from typing import Callable

from .. import diagnostics
from ..adapters.base import AdapterContext
from ..adapters.generic_jsonld import GenericJsonLdAdapter
from ..adapters.registry import resolve_adapter
from ..engine import _noop
from ..inspect_site import (
    _looks_like_profile,
    discover_profile_links,
    discover_sitemap_profile_urls,
    parse_html,
)
from ..models import (
    BrowserInspection,
    CORE_FIELD_KEYS,
    ExtractionDiagnosis,
    HealthReport,
    InspectionReport,
    RawRecord,
    RunResult,
    new_run_id,
    utc_now,
)
from ..net import FetchedPage, FetchError, PoliteFetcher
from ..normalize import canonical_url, clean_text, host_of, same_site
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
    WalkStats,
    detect_pagination,
    walk_pages,
)
from .structure import analyze_html, select_rows

ProgressFn = Callable[[str, int, int], None]

_KIND_TO_FIELD = {
    "name": "agent_name",
    "first_name": "agent_name",
    "last_name": "agent_name",
    "phone": "phone",
    "email": "email",
    "address": "street_address",
    "city": "city",
    "state": "state",
    "postal_code": "postal_code",
    "country": "country",
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


def _available_fields(structure, embedded_count: int, jsonld_count: int = 0) -> list[str]:
    fields: list[str] = []
    if structure is not None:
        for column in structure.columns:
            target = _KIND_TO_FIELD.get(column.kind_hint)
            if target and target not in fields:
                fields.append(target)
        for column in structure.columns:
            if column.kind_hint == "title" and "title" not in fields:
                fields.append("title")
    if embedded_count or jsonld_count:
        for key in ("agent_name", "brokerage_name", "phone", "email", "street_address",
                    "city", "state", "postal_code", "website", "profile_url", "title"):
            if key not in fields:
                fields.append(key)
    return fields


def _fetched_page(html: str, url: str, status: int = 200) -> FetchedPage:
    return FetchedPage(
        url=url, final_url=url, status_code=status or 200, text=html,
        headers={"content-type": "text/html; charset=utf-8"},
    )


def _jsonld_records(html: str, url: str) -> list[RawRecord]:
    try:
        adapter = GenericJsonLdAdapter()
        return adapter.listing_records(_fetched_page(html, url), ctx=None) or []  # type: ignore[arg-type]
    except Exception:
        return []


def _jsonld_page_rows(html: str, page_url: str) -> list[dict]:
    rows: list[dict] = []
    for record in _jsonld_records(html, page_url):
        fields = dict(record.fields or {})
        fields["_key"] = record.profile_url or "|".join(
            str(fields.get(key, "")) for key in ("agent_name", "phone", "email")
        )
        fields["_source_url"] = page_url
        rows.append(fields)
    return rows


def _records_from_row_dicts(rows: list[dict], source_url: str, method: str) -> list[RawRecord]:
    return to_raw_records(rows, source_url, method)


def _sample_records_from_raw(records: list[RawRecord], limit: int = 5) -> list[dict]:
    samples: list[dict] = []
    for record in records[:limit]:
        fields = {
            key: value for key, value in (record.fields or {}).items()
            if value and not str(key).startswith("_")
        }
        if record.profile_url and not fields.get("profile_url"):
            fields["profile_url"] = record.profile_url
        if fields:
            samples.append({key: str(value)[:200] for key, value in list(fields.items())[:12]})
    return samples


def _sample_records_from_page(html: str, url: str, structure,
                              mapping: FieldMapping | None, limit: int = 5) -> list[dict]:
    if structure is None:
        return []
    try:
        mapping = mapping or suggest_mapping(structure)
        if not mapping.columns:
            return []
        rows = extract_page(html, url, structure, mapping)
        samples = []
        for row in rows[:limit]:
            clean = {key: value for key, value in row.items() if not str(key).startswith("_")}
            if clean:
                samples.append({key: str(value)[:200] for key, value in list(clean.items())[:12]})
        return samples
    except Exception:
        return []


_URL_NAME_STOP = {"agent", "agents", "profile", "profiles", "team", "people", "staff",
                  "realtor", "realtors", "broker", "brokers", "member", "members"}


def name_from_profile_url(url: str) -> str:
    """Last-resort name from /agents/jane-smith when the page has no heading."""
    try:
        from urllib.parse import urlparse

        segment = [part for part in urlparse(url).path.split("/") if part][-1]
    except Exception:
        return ""
    segment = re.sub(r"\.(?:html?|php|aspx?)$", "", segment.lower())
    parts = [part for part in re.split(r"[-_]+", segment) if part.isalpha()]
    if len(parts) < 2 or any(part in _URL_NAME_STOP for part in parts):
        return ""
    return " ".join(part.capitalize() for part in parts[:4])


def _identity_count(records: list[RawRecord]) -> int:
    """Records with an actual identity, not just a link.

    A row that produced only a profile URL is not a usable record; counting it
    would stop the engine from falling back to an embedded payload that has names.
    """
    return sum(
        1 for record in records
        if any((record.fields or {}).get(key)
               for key in ("agent_name", "phone", "email", "brokerage_name"))
    )


def _maybe_auto_scroll(session: BrowserSession, enabled: bool) -> None:
    if not enabled:
        return
    scroll = getattr(session, "auto_scroll", None)
    if callable(scroll):
        try:
            scroll()
        except Exception:
            pass


def _maybe_reveal_listing_contacts(session: BrowserSession, settings: Settings) -> None:
    if not settings.browser_reveal_contacts:
        return
    revealer = getattr(session, "reveal_listing_contacts", None)
    if callable(revealer):
        try:
            revealer()
        except Exception:
            pass


def _profile_scroll(session: BrowserSession) -> None:
    """Scroll a profile briefly so lazy contact blocks render."""
    scroll = getattr(session, "auto_scroll", None)
    if callable(scroll):
        try:
            scroll(steps=3, pause_ms=120)
        except TypeError:
            try:
                scroll()
            except Exception:
                pass
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
        _maybe_reveal_listing_contacts(session, settings)
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
        jsonld_records = _jsonld_records(html, report.final_url)
        report.jsonld_record_count = len(jsonld_records)
        report.link_cluster_count = sum(
            1 for candidate in candidates if getattr(candidate, "method", "") == "link-cluster"
        )
        report.available_fields = _available_fields(
            report.structure, len(embedded_records), len(jsonld_records)
        )
        if report.structure is not None:
            suggested = suggest_mapping(report.structure)
            report.suggested_mapping = dict(suggested.columns)
        if jsonld_records:
            report.sample_records = _sample_records_from_raw(jsonld_records)
        elif embedded_records:
            report.sample_records = _sample_records_from_raw(embedded_records)
        else:
            report.sample_records = _sample_records_from_page(
                html, report.final_url, report.structure, None
            )

        layers: list[str] = []
        if jsonld_records:
            layers.append(f"schema.org JSON-LD ({len(jsonld_records)} records)")
        if embedded_records:
            layers.append(f"embedded JSON “{payload or 'payload'}” ({len(embedded_records)} records)")
        if report.structure is not None:
            method = getattr(report.structure, "method", "browser-dom")
            layers.append(
                f"{'profile-link clusters' if method == 'link-cluster' else 'repeated cards'}"
                f" ({report.structure.row_count} rows)"
            )
        if report.profile_link_count:
            layers.append(f"profile links ({report.profile_link_count})")
        report.detected_layers = layers

        plan = detect_pagination(
            html, report.final_url, report.structure.row_selector if report.structure else ""
        )
        report.pagination = plan.to_dict()

        if not candidates and len(embedded_records) < 3 and not jsonld_records:
            report.warnings.append(
                "No repeated records were detected on this page. It may be a single-profile "
                "page, a search form, or a layout this detector does not recognise."
            )
        elif not candidates and (embedded_records or jsonld_records):
            report.warnings.append(
                f"{max(len(embedded_records), len(jsonld_records))} records are available in "
                "structured/embedded data but the page has no repeated DOM structure; the "
                "browser engine will extract from that data."
            )
        if report.structure and report.structure.row_count < 3:
            report.warnings.append(
                f"Only {report.structure.row_count} repeated rows were found. Check the preview "
                "carefully before running a full extraction."
            )
        if report.structure is not None and getattr(report.structure, "method", "") == "link-cluster":
            report.warnings.append(
                "The record rows were inferred from clusters of profile links because no clean "
                "card group was found. Check the preview; the visual picker can confirm the fields."
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
        embedded_record_count=report.embedded_record_count,
        embedded_payloads=list(report.embedded_payloads),
        available_fields=fields,
        suggested_fields={key: f"Column {index + 1}" for key, index in report.suggested_mapping.items()},
        sample_records=list(report.sample_records),
        detected_layers=list(report.detected_layers),
        warnings=list(report.warnings),
    )


def _empty_result(result: RunResult, message: str, fields: list[str], settings: Settings,
                  persist: bool, started: float, *, code: str = diagnostics.NO_RECORDS,
                  inspection: InspectionReport | None = None,
                  recommendations: list[str] | None = None) -> RunResult:
    result.errors.append(message)
    result.quality = evaluate([], 0, 0, fields, settings)
    result.quality.level = "broken"
    result.health = HealthReport(status="broken", reasons=list(result.errors))
    if inspection is not None:
        result.inspection = inspection
    result.diagnosis.failure_code = code
    result.diagnosis.summary = message
    result.diagnosis.recommendations = list(recommendations or [])
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
    """Run a full browser extraction with layered fallback.

    Each supported layer is tried in reliability order. A layer that produces no
    usable records is recorded and the next one is attempted. Explicit access
    refusals (HTTP 401/403/429, anti-bot challenges) stop the run immediately.
    """
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
    result.diagnosis = ExtractionDiagnosis()

    first_html = ""
    first_url = url
    browser_report = BrowserInspection(url=url)
    plan = PaginationPlan()
    rows: list[dict] = []
    stats = WalkStats(pages=1)
    plan_strategy = "none"
    chosen_method = ""
    chosen_label = ""
    raw_records: list[RawRecord] = []

    try:
        progress("Opening website", 0, 1)
        page = session.open(url)
        _maybe_auto_scroll(session, auto_scroll)
        _maybe_reveal_listing_contacts(session, settings)
        first_html = session.content() or page.html
        final_url = session.current_url() or page.final_url
        first_url = final_url

        progress("Analyzing page structure", 0, 1)
        candidates = analyze_html(first_html, final_url)
        if structure is None:
            structure = candidates[0] if candidates else None

        embedded_records, payload_name = extract_from_embedded(first_html, final_url)
        jsonld_records = _jsonld_records(first_html, final_url)
        soup = parse_html(first_html)
        profile_links, _profile_sig, profile_count, _profile_selector = discover_profile_links(
            soup, final_url, host_of(final_url)
        )
        profile_links = [canonical_url(link) for link in profile_links if link]

        auto_mapping = mapping if (mapping and mapping.columns) else (
            suggest_mapping(structure) if structure is not None else None
        )
        explicit_mapping = bool(mapping and mapping.columns)
        is_config_adapter = False
        configured = None
        try:
            configured = resolve_adapter(final_url, first_html)
            is_config_adapter = bool(getattr(configured, "domains", ()))
        except Exception:
            configured = None

        browser_report = BrowserInspection(
            url=url, final_url=final_url, title=page.title, status_code=page.status,
            rendered=True,
            row_count=structure.row_count if structure else 0,
            structure=structure,
            candidates=candidates,
            robots_allowed=getattr(page, "robots_allowed", True),
            robots_detail=getattr(page, "robots_detail", ""),
            page_text_length=_page_text_length(first_html),
            profile_link_count=profile_count or _profile_link_count(first_html, structure),
            embedded_record_count=len(embedded_records),
            embedded_payloads=[payload_name] if payload_name else [],
            embedded_keys=blob_keys(first_html),
            jsonld_record_count=len(jsonld_records),
            link_cluster_count=sum(
                1 for candidate in candidates if getattr(candidate, "method", "") == "link-cluster"
            ),
            available_fields=_available_fields(structure, len(embedded_records), len(jsonld_records)),
        )
        browser_report.pagination = detect_pagination(
            first_html, final_url, structure.row_selector if structure else ""
        ).to_dict()
        if structure is not None:
            browser_report.suggested_mapping = dict(suggest_mapping(structure).columns)
        if jsonld_records:
            browser_report.sample_records = _sample_records_from_raw(jsonld_records)
        elif embedded_records:
            browser_report.sample_records = _sample_records_from_raw(embedded_records)
        else:
            browser_report.sample_records = _sample_records_from_page(
                first_html, final_url, structure, auto_mapping
            )
        detected_layers: list[str] = []
        if jsonld_records:
            detected_layers.append(
                f"schema.org JSON-LD ({len(jsonld_records)} records)"
            )
        if embedded_records:
            detected_layers.append(
                f"embedded JSON “{payload_name or 'payload'}” ({len(embedded_records)} records)"
            )
        if structure is not None:
            detected_layers.append(
                f"{'profile-link clusters' if getattr(structure, 'method', '') == 'link-cluster' else 'repeated cards'}"
                f" ({structure.row_count} rows)"
            )
        if profile_links:
            detected_layers.append(f"profile links ({len(profile_links)})")
        browser_report.detected_layers = detected_layers

        ctx = AdapterContext(
            start_url=final_url,
            fetcher=PoliteFetcher(settings),
            settings=settings,
            selected_fields=tuple(fields),
            max_list_pages=page_limit,
            max_profiles=max_profiles,
        )

        def _plan_for(row_selector: str) -> PaginationPlan:
            detected = detect_pagination(first_html, final_url, row_selector)
            if strategy and strategy != "auto":
                detected = PaginationPlan(
                    strategy=strategy, selector=detected.selector,
                    selector_candidates=detected.selector_candidates, text=detected.text,
                    page_urls=detected.page_urls, param=detected.param,
                    param_step=detected.param_step, reason="Selected manually.",
                )
            detected.max_pages = page_limit
            return detected

        # -- layer runners -------------------------------------------------
        def run_dom():
            if structure is None:
                return [], WalkStats(pages=0), "No repeated DOM structure was detected.", "none"
            if auto_mapping is None or not auto_mapping.columns:
                return [], WalkStats(pages=0), "No field was mapped to a column.", "none"
            local_plan = _plan_for(structure.row_selector)
            def handler(page_html: str, page_url: str) -> list[dict]:
                return extract_page(page_html, page_url, structure, auto_mapping)
            collected, local_stats = walk_pages(
                session, local_plan, handler,
                max_pages=page_limit, max_records=record_limit,
                row_selector=structure.row_selector,
                progress=lambda stage, done, total: progress(stage, done, total),
                delay_ms=int(settings.request_delay_seconds * 1000),
            )
            return (
                _records_from_row_dicts(collected, final_url, "browser-dom"),
                local_stats,
                f"{len(collected)} records from the rendered cards.",
                local_plan.strategy,
            )

        def run_embedded():
            if mode == "dom":
                return [], WalkStats(pages=0), "Embedded data was disabled for this run.", "none"
            if len(embedded_records) < 3:
                return [], WalkStats(pages=0), "Fewer than three embedded records.", "none"
            local_plan = _plan_for(structure.row_selector if structure else "")
            collected, local_stats = walk_pages(
                session, local_plan, _embedded_page_rows,
                max_pages=page_limit, max_records=record_limit,
                row_selector=structure.row_selector if structure else "",
                progress=lambda stage, done, total: progress(stage, done, total),
                delay_ms=int(settings.request_delay_seconds * 1000),
            )
            return (
                to_raw_records(collected, final_url, f"embedded-json ({payload_name})"),
                local_stats,
                f"{len(collected)} records from “{payload_name or 'embedded payload'}”.",
                local_plan.strategy,
            )

        def run_jsonld():
            if len(jsonld_records) < 3:
                return [], WalkStats(pages=0), "Fewer than three schema.org listing records.", "none"
            local_plan = _plan_for(structure.row_selector if structure else "")
            collected, local_stats = walk_pages(
                session, local_plan, _jsonld_page_rows,
                max_pages=page_limit, max_records=record_limit,
                row_selector=structure.row_selector if structure else "",
                progress=lambda stage, done, total: progress(stage, done, total),
                delay_ms=int(settings.request_delay_seconds * 1000),
            )
            return (
                _records_from_row_dicts(collected, final_url, "json-ld (listing)"),
                local_stats,
                f"{len(collected)} records from schema.org listing markup.",
                local_plan.strategy,
            )

        def run_adapter():
            if not is_config_adapter or configured is None:
                return [], WalkStats(pages=0), "No config adapter is configured for this domain.", "none"
            # Listing-level records first.
            local_plan = _plan_for("")
            def handler(page_html: str, page_url: str) -> list[dict]:
                found = configured.listing_records(_fetched_page(page_html, page_url), ctx) or []
                rows_out: list[dict] = []
                for record in found:
                    payload = dict(record.fields or {})
                    payload["_key"] = record.profile_url or "|".join(
                        str(payload.get(key, "")) for key in ("agent_name", "phone", "email")
                    )
                    payload["_source_url"] = page_url
                    rows_out.append(payload)
                return rows_out
            collected, local_stats = walk_pages(
                session, local_plan, handler,
                max_pages=page_limit, max_records=record_limit,
                progress=lambda stage, done, total: progress(stage, done, total),
                delay_ms=int(settings.request_delay_seconds * 1000),
            )
            if collected:
                return (
                    _records_from_row_dicts(collected, final_url, f"{configured.id} (listing)"),
                    local_stats,
                    f"{len(collected)} records from the site adapter's listing rules.",
                    local_plan.strategy,
                )
            # Then profile pages selected by the adapter.
            links = configured.profile_links(_fetched_page(first_html, final_url), ctx) or profile_links
            links = [canonical_url(link) for link in dict.fromkeys(links) if link]
            links = [link for link in links if same_site(link, final_url)][:max_profiles]
            if not links:
                return [], local_stats, "The site adapter matched no profile links.", local_plan.strategy
            raw: list[RawRecord] = []
            listing_back = session.current_url()
            for index, link in enumerate(links, start=1):
                progress(f"Adapter profile {index} of {len(links)}", index, len(links))
                try:
                    session.open(link)
                except BrowserBlocked:
                    raise
                except Exception as exc:
                    result.errors.append(f"Adapter could not open {link}: {exc}")
                    continue
                _profile_scroll(session)
                if settings.browser_reveal_contacts:
                    revealer = getattr(session, "reveal_contacts", None)
                    if callable(revealer):
                        try:
                            revealer()
                        except Exception:
                            pass
                parsed = configured.parse_profile(
                    _fetched_page(session.content() or "", session.current_url() or link), ctx
                )
                if parsed is not None:
                    parsed.profile_url = parsed.profile_url or link
                    if not (parsed.fields or {}).get("agent_name"):
                        inferred = name_from_profile_url(link)
                        if inferred:
                            parsed.fields["agent_name"] = inferred
                            parsed.warnings.append("name inferred from profile URL")
                    raw.append(parsed)
            if listing_back:
                try:
                    session.open(listing_back)
                except Exception:
                    pass
            return (
                raw, WalkStats(pages=1, clicked=len(links)),
                f"{len(raw)} records from adapter profile rules.",
                "none",
            )

        def run_profiles():
            links = [link for link in profile_links if same_site(link, final_url)][:max_profiles]
            if not links:
                return [], WalkStats(pages=0), "No profile links were found.", "none"
            raw: list[RawRecord] = []
            listing_back = session.current_url()
            for index, link in enumerate(links, start=1):
                progress(f"Reading profile {index} of {len(links)}", index, len(links))
                try:
                    session.open(link)
                except BrowserBlocked:
                    raise
                except Exception as exc:
                    result.errors.append(f"Profile could not be opened ({link}): {exc}")
                    continue
                _profile_scroll(session)
                if settings.browser_reveal_contacts:
                    revealer = getattr(session, "reveal_contacts", None)
                    if callable(revealer):
                        try:
                            revealer()
                        except Exception:
                            pass
                page_html = session.content() or ""
                fields_found = enrich_from_profile(page_html, session.current_url() or link)
                if not fields_found:
                    continue
                if not fields_found.get("agent_name"):
                    inferred = name_from_profile_url(link)
                    if inferred:
                        fields_found["agent_name"] = inferred
                        fields_found["_name_from_url"] = "1"
                fields_found.setdefault("profile_url", link)
                raw.append(RawRecord(
                    fields=fields_found,
                    profile_url=link,
                    source_url=link,
                    method="profile-page",
                ))
            if listing_back:
                try:
                    session.open(listing_back)
                except Exception:
                    pass
            return (
                raw, WalkStats(pages=1, clicked=len(links)),
                f"{len(raw)} records from {len(links)} profile pages.",
                "none",
            )

        def run_sitemap():
            try:
                sitemap_links = discover_sitemap_profile_urls(
                    final_url, PoliteFetcher(settings), settings, max_urls=max_profiles
                )
            except Exception as exc:
                return [], WalkStats(pages=0), f"Sitemap discovery failed: {exc}", "none"
            if not sitemap_links:
                return [], WalkStats(pages=0), "No sitemap profile URLs were found.", "none"
            raw: list[RawRecord] = []
            listing_back = session.current_url()
            for index, link in enumerate(sitemap_links[:max_profiles], start=1):
                progress(f"Sitemap profile {index} of {len(sitemap_links[:max_profiles])}", index,
                         len(sitemap_links[:max_profiles]))
                try:
                    session.open(link)
                except BrowserBlocked:
                    raise
                except Exception as exc:
                    result.errors.append(f"Sitemap profile could not be opened ({link}): {exc}")
                    continue
                _profile_scroll(session)
                page_html = session.content() or ""
                fields_found = enrich_from_profile(page_html, session.current_url() or link)
                if not fields_found:
                    continue
                if not fields_found.get("agent_name"):
                    inferred = name_from_profile_url(link)
                    if inferred:
                        fields_found["agent_name"] = inferred
                        fields_found["_name_from_url"] = "1"
                fields_found.setdefault("profile_url", link)
                raw.append(RawRecord(
                    fields=fields_found, profile_url=link, source_url=link,
                    method="sitemap-profile",
                ))
            if listing_back:
                try:
                    session.open(listing_back)
                except Exception:
                    pass
            return (
                raw, WalkStats(pages=1, clicked=len(sitemap_links)),
                f"{len(raw)} records from sitemap-listed profile pages.",
                "none",
            )

        # -- candidate order ------------------------------------------------
        jsonld_rich = len(jsonld_records) >= 3 and (
            structure is None
            or (settings.prefer_embedded and len(jsonld_records) >= max(3, int(structure.row_count * 0.6)))
        )
        embedded_rich = len(embedded_records) >= 3 and (
            structure is None
            or (settings.prefer_embedded and len(embedded_records) >= max(3, int(structure.row_count * 0.6)))
        )
        layer_order: list[tuple[str, str, Callable]] = []
        if explicit_mapping or mode == "dom":
            layer_order.append(("dom", "Rendered DOM cards", run_dom))
        if jsonld_rich:
            layer_order.append(("jsonld", "schema.org JSON-LD", run_jsonld))
        if embedded_rich:
            layer_order.append(("embedded", f"Embedded JSON ({payload_name or 'payload'})", run_embedded))
        if not explicit_mapping and mode != "dom":
            # Record the DOM attempt even when no structure was found, so the
            # diagnosis says so instead of silently omitting the layer.
            layer_order.append(("dom", "Rendered DOM cards", run_dom))
        if is_config_adapter:
            layer_order.append(("adapter", f"Site adapter: {getattr(configured, 'label', 'configured')}", run_adapter))
        if mode == "embedded":
            embedded_item = next(
                (item for item in layer_order if item[0] == "embedded"),
                ("embedded", f"Embedded JSON ({payload_name or 'payload'})", run_embedded),
            )
            layer_order = [embedded_item] + [
                item for item in layer_order if item[0] not in ("embedded", "dom")
            ]
        if profile_links:
            layer_order.append(("profiles", "Profile pages", run_profiles))
        if settings.sitemap_fallback:
            layer_order.append(("sitemap", "Sitemap-listed profiles", run_sitemap))

        # -- run layers -----------------------------------------------------
        selected = None
        for index, (method, label, runner) in enumerate(layer_order):
            if index > 0:
                try:
                    session.open(first_url)
                    _maybe_auto_scroll(session, auto_scroll)
                    _maybe_reveal_listing_contacts(session, settings)
                except BrowserBlocked:
                    raise
                except Exception:
                    pass
            progress(f"Trying {label}", 0, 1)
            layer_started = time.monotonic()
            try:
                raw, local_stats, detail, local_strategy = runner()
            except BrowserBlocked as exc:
                result.diagnosis.add(
                    method, label, "failed", 0, str(exc), time.monotonic() - layer_started
                )
                result.diagnosis.blocked = True
                result.diagnosis.blocked_reason = str(exc)
                raise
            except Exception as exc:
                result.diagnosis.add(
                    method, label, "failed", 0,
                    f"{exc.__class__.__name__}: {exc}", time.monotonic() - layer_started,
                )
                result.errors.append(f"{label} failed: {exc.__class__.__name__}: {exc}")
                continue

            usable = _identity_count(raw)
            result.diagnosis.add(
                method, label, "ok" if usable else "empty", usable, detail,
                time.monotonic() - layer_started,
            )
            if usable:
                selected = (method, label, raw, local_stats, detail, local_strategy)
                break

        if selected is None:
            code, summary, recommendations = diagnostics.classify(
                page_text_length=browser_report.page_text_length,
                script_count=len(re.findall(r"<script\b", first_html, flags=re.I)),
                row_count=browser_report.row_count,
                profile_link_count=len(profile_links),
                embedded_count=len(embedded_records),
                jsonld_count=len(jsonld_records),
                link_cluster_count=browser_report.link_cluster_count,
                structure_present=structure is not None,
                mapping_present=bool(auto_mapping and auto_mapping.columns),
                records=0,
                pages=0,
                pagination_strategy=browser_report.pagination.get("strategy", "")
                if isinstance(browser_report.pagination, dict) else "",
            )
            result.inspection = _browser_inspection_report(
                browser_report, structure.row_count if structure else 0, fields
            )
            result.inspection.warnings.append(summary)
            return _empty_result(
                result, summary, fields, settings, persist, started,
                code=code, inspection=result.inspection, recommendations=recommendations,
            )

        chosen_method, chosen_label, raw_records, stats, detail, plan_strategy = selected
        result.pages_fetched = stats.pages
        result.errors.extend(stats.warnings)

        method_ids = {
            "dom": ("browser_dom", "Browser DOM extraction"),
            "embedded": ("embedded_json", f"Embedded JSON ({payload_name or 'payload'})"),
            "jsonld": ("jsonld_listing", "schema.org listing data"),
            "adapter": (getattr(configured, "id", "site_adapter"),
                        getattr(configured, "label", "Site adapter")),
            "profiles": ("browser_profiles", "Profile pages"),
            "sitemap": ("sitemap_profiles", "Sitemap-listed profiles"),
        }
        result.adapter_id, result.adapter_label = method_ids.get(
            chosen_method, ("browser_dom", "Browser DOM extraction")
        )
        result.diagnosis.selected_method = chosen_method
        result.diagnosis.selected_label = chosen_label
        result.diagnosis.summary = detail

        # Merge first-page DOM fields into structured/embedded records when a
        # mapping exists. This adds columns the JSON payload omitted.
        if chosen_method in ("embedded", "jsonld") and structure is not None and auto_mapping is not None:
            if explicit_mapping or mode != "embedded":
                try:
                    dom_rows = extract_page(first_html, final_url, structure, auto_mapping)
                    raw_records = merge_raw_records(
                        raw_records, to_raw_records(dom_rows, final_url, "browser-dom")
                    )
                except Exception as exc:
                    result.errors.append(f"DOM enrichment of structured records failed: {exc}")

        # -- optional profile enrichment ------------------------------------
        if enrich_profiles and any((record.fields or {}).get("profile_url") for record in raw_records):
            raw_records = _enrich_records(raw_records, session, url, max_profiles, settings,
                                          progress, result, reveal_contacts)

        progress("Cleaning data", 0, 1)
        records = [build_record(raw, fields, result.adapter_id) for raw in raw_records]

        result.inspection = _browser_inspection_report(
            browser_report, structure.row_count if structure else 0, fields
        )
        if stats.stopped_reason:
            result.inspection.warnings.append(stats.stopped_reason)
        if plan_strategy == "none" and chosen_method in ("dom", "embedded", "jsonld", "adapter"):
            result.inspection.warnings.append(
                "No pagination was found; only the records visible on the first page were collected."
            )
        if chosen_method in ("embedded", "jsonld"):
            result.inspection.warnings.append(
                f"Records came from {chosen_label}. DOM values were merged where available."
            )
        if chosen_method == "profiles":
            result.inspection.warnings.append(
                "The listing had no usable card data, so records were read from profile pages."
            )
        if chosen_method == "sitemap":
            result.inspection.warnings.append(
                "The listing had no usable records or links, so profile URLs were discovered from "
                "the site's sitemap. Check the coverage against the expected roster size."
            )
        contact_raw = sum(
            1 for record in raw_records
            if (record.fields or {}).get("phone") or (record.fields or {}).get("email")
        )
        if (not enrich_profiles and chosen_method in ("dom", "embedded", "jsonld", "adapter")
                and raw_records and contact_raw / len(raw_records) < 0.5
                and profile_links):
            note = (
                "The structured/listing records carry few phone or email values, but the page "
                "has profile links. Enable “Read profile pages” on a re-run to enrich them."
            )
            result.inspection.warnings.append(note)
            result.diagnosis.recommendations.append(note)

        result.export_context = {
            "Extraction method": chosen_label,
            "Layers attempted": str(len(result.diagnosis.attempts)),
            "Pagination strategy": plan_strategy,
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
            result.diagnosis.blocked = True
            result.diagnosis.blocked_reason = str(exc)
            result.diagnosis.failure_code = diagnostics.BLOCKED
            result.diagnosis.summary = (
                "The site refused access while profiles were being read; this dataset is incomplete."
            )
            result.diagnosis.recommendations = [
                "Do not deliver this run as complete.",
                "Stop this target and ask the site owner for permission.",
            ]
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
    if result.diagnosis.failure_code == diagnostics.BLOCKED:
        result.quality.level = "broken"
        warning = (
            "Access was refused during this run. The dataset is incomplete; do not "
            "deliver it as complete."
        )
        if warning not in result.quality.warnings:
            result.quality.warnings.insert(0, warning)

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
