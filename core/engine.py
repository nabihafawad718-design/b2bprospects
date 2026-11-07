"""Extraction orchestrator.

Flow: fetch listing → inspect → resolve adapter → walk pages → collect profile
links and any listing-level records → fetch profiles politely → normalize →
deduplicate → score quality → compare to baseline → persist. The UI only calls
`run_extraction`.
"""
from __future__ import annotations

import re
import time
from typing import Callable

from . import diagnostics
from .adapters.base import AdapterContext, DirectoryAdapter
from .adapters.generic_html import GenericHtmlAdapter
from .adapters.generic_jsonld import GenericJsonLdAdapter
from .adapters.registry import resolve_adapter
from .inspect_site import discover_sitemap_profile_urls, inspect_page
from .models import (
    CORE_FIELD_KEYS,
    ExtractionDiagnosis,
    HealthReport,
    InspectionReport,
    RawRecord,
    RunResult,
    new_run_id,
    utc_now,
)
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
             for key in ("agent_name", "phone", "email")]
    parts.append(str(record.profile_url or fields.get("profile_url") or "").strip().lower())
    return "|".join(parts) if any(parts) else ""


def _contact_identity(record: RawRecord) -> tuple[str, str]:
    fields = record.fields or {}
    email = str(fields.get("email", "") or "").strip().lower()
    phone = re.sub(r"\D", "", str(fields.get("phone", "") or ""))
    if len(phone) == 11 and phone.startswith("1"):
        phone = phone[1:]
    return email, phone[-10:] if len(phone) >= 10 else phone


def _merge_seed_records(target: list[RawRecord], extra: list[RawRecord]) -> None:
    """Merge extra listing records into the seed list, filling gaps by identity."""
    index = {_seed_key(record): record for record in target if _seed_key(record)}
    for record in extra:
        key = _seed_key(record)
        if not key:
            continue
        existing = index.get(key)
        if existing is None:
            name = str((record.fields or {}).get("agent_name", "")).strip().lower()
            if name:
                existing = next(
                    (item for item in target
                     if str((item.fields or {}).get("agent_name", "")).strip().lower() == name),
                    None,
                )
        if existing is None:
            # A partial adapter (or a different layer) may know the same person
            # only by email or phone. A shared office phone is not enough on its
            # own: only use it when one side has no name, or the names agree.
            email, phone = _contact_identity(record)
            record_name = str((record.fields or {}).get("agent_name", "")).strip().lower()
            for item in target:
                item_email, item_phone = _contact_identity(item)
                item_name = str((item.fields or {}).get("agent_name", "")).strip().lower()
                if email and email == item_email:
                    existing = item
                    break
                if phone and phone == item_phone and (
                    not record_name or not item_name or record_name == item_name
                ):
                    existing = item
                    break
        if existing is None:
            target.append(record)
            index[key] = record
            continue
        existing.fields = existing.fields or {}
        for field_key, value in (record.fields or {}).items():
            if value and not existing.fields.get(field_key):
                existing.fields[field_key] = value
        if record.profile_url and not existing.profile_url:
            existing.profile_url = record.profile_url


def _seed_samples(records: list[RawRecord], limit: int = 5) -> list[dict]:
    samples: list[dict] = []
    for record in records[:limit]:
        fields = {
            key: str(value)[:200] for key, value in (record.fields or {}).items()
            if value and not str(key).startswith("_")
        }
        if record.profile_url and not fields.get("profile_url"):
            fields["profile_url"] = record.profile_url
        if fields:
            samples.append(fields)
    return samples


def _collect_seeds(adapter: DirectoryAdapter, page, ctx: AdapterContext,
                   target: list[RawRecord], result: RunResult) -> tuple[int, int]:
    """Collect listing-level records from the adapter and the generic detector.

    The generic pass runs even when a site adapter claims the domain: it can fill
    a missing name or a column the adapter's selectors no longer match. Records
    are merged by contact identity, so it does not duplicate people.
    """
    adapter_records: list[RawRecord] = []
    try:
        adapter_records = adapter.listing_records(page, ctx) or []
    except Exception as exc:
        result.errors.append(
            f"{adapter.label}: listing-record extraction failed "
            f"({exc.__class__.__name__}: {exc})"
        )
    generic_records: list[RawRecord] = []
    try:
        generic_records = GenericHtmlAdapter().listing_records(page, ctx) or []
    except Exception as exc:
        result.errors.append(
            f"Generic listing-record extraction failed ({exc.__class__.__name__}: {exc})"
        )
    _merge_seed_records(target, adapter_records)
    _merge_seed_records(target, generic_records)
    return len(adapter_records), len(generic_records)


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
    result.diagnosis = ExtractionDiagnosis()

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
    layer_started = time.monotonic()
    adapter_seed_count, generic_seed_count = _collect_seeds(
        adapter, first_page, ctx, seed_records, result
    )
    result.diagnosis.add(
        "adapter_listing", f"{adapter.label} listing rules",
        "ok" if adapter_seed_count else "empty", adapter_seed_count,
        f"{adapter_seed_count} records from the resolved adapter's listing rules.",
        time.monotonic() - layer_started,
    )
    layer_started = time.monotonic()
    result.diagnosis.add(
        "generic_listing", "Generic listing cards",
        "ok" if generic_seed_count else "empty", generic_seed_count,
        f"{generic_seed_count} records from repeated listing cards.",
        time.monotonic() - layer_started,
    )
    listing_seed_count = len(seed_records)

    # Layer 2: schema.org listing records. A server-rendered roster often carries
    # complete Person nodes even when its cards are not machine-readable.
    layer_started = time.monotonic()
    try:
        jsonld_seeds = GenericJsonLdAdapter().listing_records(first_page, ctx) or []
    except Exception as exc:
        jsonld_seeds = []
        result.errors.append(f"Structured-data listing extraction failed: {exc.__class__.__name__}: {exc}")
    if jsonld_seeds:
        _merge_seed_records(seed_records, jsonld_seeds)
        result.diagnosis.add(
            "jsonld_listing", "schema.org listing data", "ok", len(jsonld_seeds),
            f"{len(jsonld_seeds)} Person/RealEstateAgent records on the listing page.",
            time.monotonic() - layer_started,
        )
    else:
        result.diagnosis.add(
            "jsonld_listing", "schema.org listing data", "empty", 0,
            "No complete Person records in the listing page's JSON-LD.",
            time.monotonic() - layer_started,
        )

    # Layer 3: embedded JSON (__NEXT_DATA__, __INITIAL_STATE__, flight chunks).
    layer_started = time.monotonic()
    try:
        from .browser.embedded import extract_from_embedded

        embedded_seeds, embedded_payload = extract_from_embedded(
            first_page.text, first_page.final_url
        )
    except Exception as exc:
        embedded_seeds, embedded_payload = [], ""
        result.errors.append(f"Embedded JSON extraction failed: {exc.__class__.__name__}: {exc}")
    if embedded_seeds:
        _merge_seed_records(seed_records, embedded_seeds)
        report.embedded_record_count = len(embedded_seeds)
        report.embedded_payloads = [embedded_payload] if embedded_payload else []
        result.diagnosis.add(
            "embedded_listing", f"embedded JSON ({embedded_payload or 'payload'})", "ok",
            len(embedded_seeds),
            f"{len(embedded_seeds)} records in the {embedded_payload or 'embedded'} payload.",
            time.monotonic() - layer_started,
        )
    else:
        result.diagnosis.add(
            "embedded_listing", "embedded JSON", "empty", 0,
            "No usable record array was found in the page's JSON payloads.",
            time.monotonic() - layer_started,
        )

    report.sample_records = _seed_samples(seed_records)
    report.detected_layers = [
        layer for layer in (
            "listing cards" if listing_seed_count else "",
            "schema.org JSON-LD" if jsonld_seeds else "",
            f"embedded JSON ({embedded_payload})" if embedded_seeds else "",
            "profile links" if report.profile_link_count else "",
        ) if layer
    ]

    if report.requires_javascript and not report.profile_link_count and len(seed_records) < 2:
        result.finished_at = utc_now()
        result.elapsed_seconds = time.monotonic() - started_clock
        message = (
            "This directory renders its listings with JavaScript. The HTML we received contains "
            "no profile links and no embedded records, so a static extraction cannot work. "
            "Use the browser-automation path or add a dedicated adapter."
        )
        result.errors.append(message)
        result.quality = evaluate([], 0, 0, fields, settings)
        result.quality.level = "broken"
        result.health = HealthReport(status="broken", reasons=[message])
        result.diagnosis.failure_code = diagnostics.JAVASCRIPT_ONLY
        result.diagnosis.summary = message
        result.diagnosis.recommendations = [
            "Switch to Browser mode so Chromium can render the page.",
            "If Browser mode also finds nothing, the site loads records from an authenticated API.",
            "For a recurring target, add a site adapter with the profile URL pattern.",
        ]
        if persist:
            append_run(result.summary())
        return result

    # --- walk listing pages ------------------------------------------------
    page = first_page
    seen_pages: set[str] = set()
    seen_profiles: set[str] = set()
    profile_urls: list[str] = []
    pagination_blocked = False

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
            pagination_blocked = True
            break
        except FetchError as exc:
            result.errors.append(f"Pagination stopped on {target}: {exc}")
            break

    result.diagnosis.add(
        "listing_pages", "Listing pages", "ok" if result.pages_fetched else "empty",
        len(profile_urls),
        f"{result.pages_fetched} listing page(s) walked; {len(profile_urls)} profile link(s).",
        0.0,
    )

    # Layer 5: sitemap-discovered profile URLs. This rescues a JavaScript-only
    # roster whose profiles are still listed in the XML sitemap.
    sitemap_used = False
    if settings.sitemap_fallback and not profile_urls and len(seed_records) < 2:
        sitemap_started = time.monotonic()
        try:
            sitemap_urls = discover_sitemap_profile_urls(
                report.final_url or url, fetcher, settings, max_urls=profile_limit
            )
        except Exception as exc:
            sitemap_urls = []
            result.errors.append(f"Sitemap discovery failed: {exc.__class__.__name__}: {exc}")
        if sitemap_urls:
            profile_urls = sitemap_urls
            sitemap_used = True
            result.diagnosis.add(
                "sitemap", "Sitemap-listed profiles", "ok", len(sitemap_urls),
                f"{len(sitemap_urls)} profile URL(s) found in the site sitemap.",
                time.monotonic() - sitemap_started,
            )
        else:
            result.diagnosis.add(
                "sitemap", "Sitemap-listed profiles", "empty", 0,
                "No profile-shaped URLs were found in the site sitemap.",
                time.monotonic() - sitemap_started,
            )

    profile_urls = profile_urls[:profile_limit]

    # A listing page carrying complete records is a legitimate high-fidelity
    # source. Use it directly when contact coverage is good; otherwise profiles
    # remain the better source and the seeds are a fallback.
    contact_seeds = sum(
        1 for record in seed_records
        if (record.fields or {}).get("phone") or (record.fields or {}).get("email")
    )
    seed_share = contact_seeds / len(seed_records) if seed_records else 0.0
    # "Complete enough" means coverage across the fields the operator asked for,
    # not merely one phone number per card. Otherwise a thin listing card would
    # suppress profile pages that hold the email and office address.
    seed_fields = [
        key for key in ("agent_name", "phone", "email", "street_address", "city",
                        "state", "website", "profile_url")
        if key in fields
    ] or ["agent_name", "phone", "email"]
    if seed_records:
        total_fill = sum(
            1 for record in seed_records for key in seed_fields
            if (record.fields or {}).get(key)
        )
        seed_field_share = total_fill / (len(seed_records) * len(seed_fields))
        email_share = sum(1 for record in seed_records if (record.fields or {}).get("email")) / len(seed_records)
        phone_share = sum(1 for record in seed_records if (record.fields or {}).get("phone")) / len(seed_records)
    else:
        seed_field_share = 0.0
        email_share = phone_share = 0.0
    # Both contact types must be present before listing cards outrank profiles.
    # A phone-only card usually means the profile holds the email and address.
    rich_contact = email_share >= 0.5 and phone_share >= 0.5
    prefer_seeds = settings.prefer_listing_records if prefer_listing_records is None else prefer_listing_records
    use_seeds = len(seed_records) >= 2 and (
        not profile_urls
        or (prefer_seeds and seed_share >= 0.60 and rich_contact and seed_field_share >= 0.50)
    )

    if not profile_urls and len(seed_records) < 2:
        result.finished_at = utc_now()
        result.elapsed_seconds = time.monotonic() - started_clock
        code, message, recommendations = diagnostics.classify(
            page_text_length=len(first_page.text or ""),
            profile_link_count=report.profile_link_count,
            embedded_count=report.embedded_record_count,
            jsonld_count=len(jsonld_seeds),
            structure_present=bool(report.container_count),
            mapping_present=True,
            records=0,
            pagination_strategy=report.pagination_type,
        )
        result.errors.append(message)
        result.quality = evaluate([], 0, 0, fields, settings)
        result.quality.level = "broken"
        result.health = HealthReport(status="broken", reasons=[message])
        result.diagnosis.failure_code = code
        result.diagnosis.summary = message
        result.diagnosis.recommendations = recommendations
        if persist:
            append_run(result.summary())
        return result

    # --- records -----------------------------------------------------------
    raw_records: list[RawRecord] = []
    if use_seeds:
        progress("Using records published on the listing page", 0, 1)
        # A site adapter that publishes complete cards is not limited by the
        # profile cap (the demo relies on this). Generic/embedded seeds are the
        # delivered dataset, so the operator's cap applies to them.
        selected_seeds = seed_records if adapter_seed_count else seed_records[:profile_limit]
        raw_records = [build_record(record, fields, adapter.id) for record in selected_seeds]
        result.profiles_attempted = 0
        if embedded_seeds:
            result.diagnosis.selected_method = "embedded_listing"
            result.diagnosis.selected_label = f"Embedded JSON ({embedded_payload or 'payload'})"
        elif jsonld_seeds:
            result.diagnosis.selected_method = "jsonld_listing"
            result.diagnosis.selected_label = "schema.org listing data"
        elif adapter_seed_count:
            result.diagnosis.selected_method = "adapter_listing"
            result.diagnosis.selected_label = f"{adapter.label} listing records"
        else:
            result.diagnosis.selected_method = "generic_listing"
            result.diagnosis.selected_label = "Generic listing cards"
        result.diagnosis.add(
            "listing_records", "Listing-page records", "ok", len(seed_records),
            "Complete/contact-rich records were available without opening profiles.", 0.0,
        )
        result.export_context = {
            "Extraction source": f"listing records ({result.diagnosis.selected_label})",
            "Listing pages walked": str(result.pages_fetched),
        }
    else:
        result.profiles_attempted = len(profile_urls)
        blocked = False
        profile_started = time.monotonic()
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
                if not (raw.fields or {}).get("agent_name"):
                    try:
                        from .browser.engine import name_from_profile_url

                        inferred = name_from_profile_url(profile_url)
                    except Exception:
                        inferred = ""
                    if inferred:
                        raw.fields["agent_name"] = inferred
                        raw.warnings.append("name inferred from profile URL")
                raw.source_url = raw.source_url or profile_url
                raw_records.append(build_record(raw, fields, adapter.id))

        result.diagnosis.add(
            "profiles", "Profile pages", "ok" if raw_records else "empty", len(raw_records),
            f"{len(raw_records)} profile record(s) from {len(profile_urls)} URL(s).",
            time.monotonic() - profile_started,
        )
        if raw_records:
            result.diagnosis.selected_method = "sitemap_profiles" if sitemap_used else "profiles"
            result.diagnosis.selected_label = (
                "Sitemap-listed profile pages" if sitemap_used else "Profile pages"
            )

        if blocked:
            result.quality = evaluate([], 0, 0, fields, settings)
            result.quality.level = "broken"

        if not raw_records and seed_records:
            progress("Falling back to listing-page records", 0, 1)
            raw_records = [build_record(record, fields, adapter.id) for record in seed_records]
            result.diagnosis.selected_method = "listing_records_fallback"
            result.diagnosis.selected_label = "Listing-page records (profile fallback)"
            result.diagnosis.add(
                "listing_fallback", "Listing records fallback", "ok", len(seed_records),
                "Profiles produced no records; listing-page records were used.", 0.0,
            )
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
            result.diagnosis.failure_code = diagnostics.BLOCKED
            result.diagnosis.summary = (
                "The site refused access partway through this run; the dataset is incomplete."
            )
            result.diagnosis.blocked = True
            result.diagnosis.recommendations = [
                "Do not deliver this run as complete.",
                "Stop this target and ask the site owner for permission.",
            ]
            if persist:
                append_run(result.summary())
            return result

    # --- quality -----------------------------------------------------------
    unique, duplicates_removed = dedupe(raw_records)
    result.records = unique
    result.quality = evaluate(unique, result.profiles_attempted, duplicates_removed,
                              adapter.expected_fields, settings)
    if unique and pagination_blocked:
        result.quality.level = "broken"
        result.quality.warnings.insert(
            0,
            "The site refused access while paginating. This dataset is incomplete; "
            "do not deliver it as complete.",
        )
        result.diagnosis.failure_code = diagnostics.BLOCKED
        result.diagnosis.blocked = True
        result.diagnosis.summary = (
            "Access was refused during pagination; only the pages read before the refusal are present."
        )
        result.diagnosis.recommendations = [
            "Do not deliver this run as complete.",
            "Stop this target and ask the site owner for permission.",
        ]
    elif unique:
        result.diagnosis.failure_code = ""
        result.diagnosis.summary = (
            f"{len(unique)} records delivered from {result.diagnosis.selected_label or result.adapter_label}."
        )
    else:
        code, summary, recommendations = diagnostics.classify(
            page_text_length=len(first_page.text or ""),
            profile_link_count=report.profile_link_count,
            embedded_count=report.embedded_record_count,
            jsonld_count=len(jsonld_seeds),
            structure_present=bool(report.container_count),
            mapping_present=True,
            records=0,
            pages=result.pages_fetched,
            pagination_strategy=report.pagination_type,
        )
        result.diagnosis.failure_code = code
        result.diagnosis.summary = summary
        result.diagnosis.recommendations = recommendations
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
