"""Extraction orchestrator.

Flow: fetch listing → inspect → resolve adapter → walk pages → collect profile
links and any listing-level records → fetch profiles politely → normalize →
deduplicate → score quality → compare to baseline → persist. The UI only calls
`run_extraction`.
"""
from __future__ import annotations

import time
from typing import Callable

from .adapters.base import AdapterContext, DirectoryAdapter
from .adapters.registry import resolve_adapter
from .inspect_site import inspect_page
from .models import CORE_FIELD_KEYS, HealthReport, InspectionReport, RawRecord, RunResult, new_run_id, utc_now
from .net import BlockedError, FetchError, PoliteFetcher, RobotsDisallowed
from .normalize import canonical_url, host_of, same_site
from .quality import build_record, dedupe, evaluate
from .runs import append_run, compute_health, runs_for
from .settings import SETTINGS, Settings

ProgressFn = Callable[[str, int, int], None]


def _noop(stage: str, done: int, total: int) -> None:  # pragma: no cover
    return None


def _seed_key(record: RawRecord) -> str:
    fields = record.fields or {}
    parts = [str(fields.get(key, "") or "").strip().lower()
             for key in ("agent_name", "phone", "email", "profile_url")]
    return "|".join(parts) if any(parts) else ""


def _collect_seeds(adapter: DirectoryAdapter, page, ctx: AdapterContext,
                   target: list[RawRecord], result: RunResult) -> None:
    """Ask an adapter for records available directly on a listing page."""
    try:
        records = adapter.listing_records(page, ctx) or []
    except Exception as exc:
        result.errors.append(
            f"{adapter.label}: listing-record extraction failed "
            f"({exc.__class__.__name__}: {exc})"
        )
        return
    seen = {_seed_key(record) for record in target if _seed_key(record)}
    for record in records:
        key = _seed_key(record)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        target.append(record)


def run_extraction(
    url: str,
    selected_fields: list[str] | tuple[str, ...] = CORE_FIELD_KEYS,
    fetcher: PoliteFetcher | None = None,
    settings: Settings = SETTINGS,
    max_list_pages: int | None = None,
    max_profiles: int | None = None,
    progress: ProgressFn | None = None,
    persist: bool = True,
    prefer_listing_records: bool | None = None,
) -> RunResult:
    """Run one extraction. Never raises for per-profile failures; raises only if the
    entry page itself cannot be fetched (the UI catches and explains those)."""
    progress = progress or _noop
    fetcher = fetcher or PoliteFetcher(settings)
    settings = fetcher.settings
    fields = list(dict.fromkeys(selected_fields)) or list(CORE_FIELD_KEYS)
    page_limit = max(1, min(max_list_pages or settings.max_list_pages, 100))
    profile_limit = max(1, min(max_profiles or settings.max_profiles,
                               settings.max_profiles_per_run_hard_cap))

    started_clock = time.monotonic()
    result = RunResult(
        run_id=new_run_id(url),
        started_at=utc_now(),
        url=url,
        selected_fields=fields,
    )

    progress("Opening the directory page", 0, 1)
    first_page = fetcher.get(url)
    if not first_page.is_html:
        raise FetchError(
            f"That URL returned {first_page.content_type or 'a non-HTML response'}, not a directory page.",
            url,
            first_page.status_code,
        )

    progress("Inspecting page structure", 0, 1)
    report: InspectionReport = inspect_page(first_page, fetcher, check_sitemap=True)
    result.inspection = report

    adapter = resolve_adapter(report.final_url or url, first_page.text, report.recommended_adapter)
    result.adapter_id = adapter.id
    result.adapter_label = adapter.label

    ctx = AdapterContext(
        start_url=report.final_url or url,
        fetcher=fetcher,
        settings=settings,
        selected_fields=tuple(fields),
        max_list_pages=page_limit,
        max_profiles=profile_limit,
    )

    seed_records: list[RawRecord] = []
    _collect_seeds(adapter, first_page, ctx, seed_records, result)

    if report.requires_javascript and not report.profile_link_count and len(seed_records) < 2:
        result.finished_at = utc_now()
        result.elapsed_seconds = time.monotonic() - started_clock
        message = (
            "This directory renders its listings with JavaScript. The HTML we received contains "
            "no profile links, so a static extraction cannot work. Use the browser-automation "
            "path or add a dedicated adapter."
        )
        result.errors.append(message)
        result.quality = evaluate([], 0, 0, fields, settings)
        result.quality.level = "broken"
        result.health = HealthReport(status="broken", reasons=[message])
        if persist:
            append_run(result.summary())
        return result

    # --- walk listing pages ------------------------------------------------
    page = first_page
    seen_pages: set[str] = set()
    seen_profiles: set[str] = set()
    profile_urls: list[str] = []

    for page_number in range(1, page_limit + 1):
        page_key = canonical_url(page.final_url)
        if page_key in seen_pages:
            break
        seen_pages.add(page_key)

        progress(f"Reading listing page {page_number}", 0, 1)
        try:
            links = adapter.profile_links(page, ctx)
        except Exception as exc:  # an adapter bug must not kill the whole run
            result.errors.append(f"{adapter.label}: profile discovery failed ({exc.__class__.__name__}: {exc})")
            links = []

        if page_number > 1:
            _collect_seeds(adapter, page, ctx, seed_records, result)

        new_links = 0
        for link in links:
            canonical = canonical_url(link)
            if not canonical or canonical in seen_profiles:
                continue
            if not same_site(canonical, report.host):
                continue  # never wander off-site
            seen_profiles.add(canonical)
            profile_urls.append(canonical)
            new_links += 1

        result.pages_fetched += 1
        if new_links == 0 and page_number > 1:
            ctx.note(f"Listing page {page_number} produced no new profile links; stopped paging.")
            break
        # When the listing itself publishes records, a profile cap must not stop
        # pagination: the cap applies to profile fetches, not to listing pages.
        if not seed_records and len(profile_urls) >= profile_limit:
            break

        next_urls = []
        try:
            next_urls = adapter.next_page_urls(page, ctx)
        except Exception as exc:
            result.errors.append(f"{adapter.label}: pagination failed ({exc.__class__.__name__}: {exc})")

        target = next((u for u in next_urls if canonical_url(u) not in seen_pages), None)
        if not target:
            break
        try:
            page = fetcher.get(target)
        except RobotsDisallowed as exc:
            result.errors.append(f"Pagination blocked by robots.txt: {exc}")
            break
        except BlockedError as exc:
            result.errors.append(f"Pagination stopped: {exc}")
            break
        except FetchError as exc:
            result.errors.append(f"Pagination stopped on {target}: {exc}")
            break

    profile_urls = profile_urls[:profile_limit]

    # A listing page carrying complete records is a legitimate high-fidelity
    # source. Use it directly when contact coverage is good; otherwise profiles
    # remain the better source and the seeds are a fallback.
    contact_seeds = sum(
        1 for record in seed_records
        if (record.fields or {}).get("phone") or (record.fields or {}).get("email")
    )
    seed_share = contact_seeds / len(seed_records) if seed_records else 0.0
    prefer_seeds = settings.prefer_listing_records if prefer_listing_records is None else prefer_listing_records
    use_seeds = len(seed_records) >= 2 and (
        not profile_urls or (prefer_seeds and seed_share >= 0.60)
    )

    if not profile_urls and len(seed_records) < 2:
        result.finished_at = utc_now()
        result.elapsed_seconds = time.monotonic() - started_clock
        message = (
            "No profile pages were discovered. The listing markup may have changed, or this URL "
            "is not a directory page."
        )
        result.errors.append(message)
        result.quality = evaluate([], 0, 0, fields, settings)
        result.quality.level = "broken"
        result.health = HealthReport(status="broken", reasons=[message])
        if persist:
            append_run(result.summary())
        return result

    # --- records -----------------------------------------------------------
    raw_records: list[RawRecord] = []
    if use_seeds:
        progress("Using records published on the listing page", 0, 1)
        raw_records = [build_record(record, fields, adapter.id) for record in seed_records]
        result.profiles_attempted = 0
        result.export_context = {
            "Extraction source": "listing page records",
            "Listing pages walked": str(result.pages_fetched),
        }
    else:
        result.profiles_attempted = len(profile_urls)
        blocked = False
        for index, profile_url in enumerate(profile_urls, start=1):
            progress(f"Extracting profile {index} of {len(profile_urls)}", index, len(profile_urls))
            try:
                profile_page = fetcher.get(profile_url)
            except RobotsDisallowed as exc:
                result.errors.append(f"Skipped {profile_url}: {exc}")
                continue
            except BlockedError as exc:
                result.errors.append(f"Site refused access at profile {index}: {exc}")
                blocked = True
                break
            except FetchError as exc:
                result.errors.append(f"Could not fetch {profile_url}: {exc}")
                continue

            if not profile_page.is_html:
                continue
            try:
                raw = adapter.parse_profile(profile_page, ctx)
            except Exception as exc:
                result.errors.append(
                    f"{adapter.label} failed to parse {profile_url} ({exc.__class__.__name__}: {exc})"
                )
                continue
            if raw is not None:
                raw.source_url = raw.source_url or profile_url
                raw_records.append(build_record(raw, fields, adapter.id))

        if blocked:
            result.quality = evaluate([], 0, 0, fields, settings)
            result.quality.level = "broken"

        if not raw_records and seed_records:
            progress("Falling back to listing-page records", 0, 1)
            raw_records = [build_record(record, fields, adapter.id) for record in seed_records]
            result.export_context = {
                "Extraction source": "listing records (profile fallback)",
                "Listing pages walked": str(result.pages_fetched),
            }
            result.errors.append(
                "Profile pages produced no usable records; the listing page's published records "
                "were used instead."
            )

        if not result.export_context:
            result.export_context = {
                "Extraction source": "profile pages",
                "Listing pages walked": str(result.pages_fetched),
            }

        if blocked:
            result.finished_at = utc_now()
            result.elapsed_seconds = time.monotonic() - started_clock
            result.quality = evaluate(raw_records, result.profiles_attempted, 0,
                                      adapter.expected_fields, settings)
            result.quality.level = "broken"
            result.quality.warnings.insert(
                0, "The site refused access partway through. This run is incomplete; do not deliver it."
            )
            result.records = raw_records
            result.health = HealthReport(status="broken", reasons=list(result.errors))
            if persist:
                append_run(result.summary())
            return result

    # --- quality -----------------------------------------------------------
    unique, duplicates_removed = dedupe(raw_records)
    result.records = unique
    result.quality = evaluate(unique, result.profiles_attempted, duplicates_removed,
                              adapter.expected_fields, settings)
    if ctx.notes:
        result.inspection.warnings.extend(n for n in ctx.notes if n not in result.inspection.warnings)
    if not report.robots_allowed and report.robots_detail:
        note = (
            f"robots.txt note: {report.robots_detail}. This is reported, not enforced, "
            "because robots.txt is a crawling convention rather than a statement of permission."
        )
        if note not in result.inspection.warnings:
            result.inspection.warnings.append(note)

    # --- health ------------------------------------------------------------
    result.finished_at = utc_now()
    result.elapsed_seconds = time.monotonic() - started_clock

    preliminary = result.summary()
    prior = runs_for(url, adapter.id)
    result.health = compute_health(preliminary, prior, settings)

    if persist:
        error = append_run(result.summary())
        if error:
            result.errors.append(error)
    return result


# ---------------------------------------------------------------------------
# Convenience used by the inspection step in the UI
# ---------------------------------------------------------------------------
def inspect_only(url: str, fetcher: PoliteFetcher | None = None) -> InspectionReport:
    fetcher = fetcher or PoliteFetcher()
    from .inspect_site import inspect_url

    report = inspect_url(url, fetcher)
    report.recommended_adapter = report.recommended_adapter or ""
    return report
