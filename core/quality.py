"""Record construction, duplicate detection and quality scoring.

The rule this module enforces: never hand a customer a table that looks complete
when it is not. Every weakness becomes a flag on the record and a warning on the run.
"""
from __future__ import annotations

import re
from collections import Counter

from .models import FIELD_BY_KEY, META_KEYS, QualityReport, RawRecord, Record, utc_now
from .normalize import (
    canonical_url,
    clean_text,
    normalize_email,
    normalize_name,
    normalize_org,
    normalize_phone,
    parse_address,
)
from .settings import SETTINGS, Settings

_NAME_KEY = re.compile(r"[^a-z ]")


def _name_key(name: str) -> str:
    return _NAME_KEY.sub("", name.lower()).strip()


def build_record(raw: RawRecord, selected_fields: list[str], adapter_id: str,
                 collected_at: str | None = None) -> Record:
    """Normalize one raw observation into an exportable record."""
    f = raw.fields or {}
    collected = collected_at or utc_now()

    phone_display, digits = normalize_phone(f.get("phone"))
    email = normalize_email(f.get("email"))

    street = clean_text(f.get("street_address"))
    city = clean_text(f.get("city"))
    state = clean_text(f.get("state"))
    postal = clean_text(f.get("postal_code"))

    # If the adapter only produced a single address blob, split it here.
    if not city and not street and f.get("address"):
        parsed = parse_address(f.get("address"))
        street, city = parsed["street_address"], parsed["city"]
        state, postal = parsed["state"], parsed["postal_code"]

    record = Record(
        agent_name=normalize_name(f.get("agent_name")),
        brokerage_name=normalize_org(f.get("brokerage_name")),
        phone=phone_display,
        email=email,
        street_address=street[:200],
        city=city[:100],
        state=state[:50],
        postal_code=postal[:20],
        profile_url=canonical_url(raw.profile_url or f.get("profile_url") or raw.source_url),
        source_url=canonical_url(raw.source_url or raw.profile_url or ""),
        collected_at=collected,
        extraction_method=raw.method,
        adapter_id=adapter_id,
        phone_digits=digits,
    )

    flags: list[str] = []
    if not record.agent_name:
        flags.append("missing name")
    if not record.phone and not record.email:
        flags.append("no contact details")
    if record.phone and not record.email:
        flags.append("phone only")
    if record.email and not record.phone:
        flags.append("email only")
    if not record.city and not record.state:
        flags.append("missing location")
    for warning in raw.warnings:
        if warning not in flags:
            flags.append(warning)
    record.quality_flags = flags
    return record


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------
def dedupe(records: list[Record]) -> tuple[list[Record], int]:
    """Drop exact duplicates. Returns (unique records, count removed).

    Matching is layered from strict to loose so that a shared office phone number
    never merges two different agents.
    """
    seen_urls: dict[str, Record] = {}
    seen_name_phone: dict[tuple[str, str], Record] = {}
    seen_name_org_city: dict[tuple[str, str, str], Record] = {}
    unique: list[Record] = []
    removed = 0

    for record in records:
        name_key = _name_key(record.agent_name)
        org_key = _name_key(record.brokerage_name)
        city_key = record.city.lower().strip()
        url_key = record.profile_url.rstrip("/").lower()
        phone_key = record.phone_digits

        duplicate = None
        if url_key and url_key in seen_urls:
            duplicate = seen_urls[url_key]
        elif name_key and phone_key and (name_key, phone_key) in seen_name_phone:
            duplicate = seen_name_phone[(name_key, phone_key)]
        elif name_key and org_key and city_key:
            key = (name_key, org_key, city_key)
            if key in seen_name_org_city:
                duplicate = seen_name_org_city[key]

        if duplicate is not None:
            removed += 1
            record.duplicate_of = duplicate.profile_url or duplicate.agent_name
            if "duplicate" not in record.quality_flags:
                record.quality_flags.append("duplicate")
            continue

        unique.append(record)
        if url_key:
            seen_urls[url_key] = record
        if name_key and phone_key:
            seen_name_phone[(name_key, phone_key)] = record
        if name_key and org_key and city_key:
            seen_name_org_city[(name_key, org_key, city_key)] = record

    return unique, removed


def _fill_rate(records: list[Record], key: str) -> float:
    if not records:
        return 0.0
    filled = sum(1 for r in records if str(getattr(r, key, "") or "").strip())
    return filled / len(records)


def evaluate(records: list[Record], attempted: int, duplicates_removed: int,
             expected_fields: tuple[str, ...] | list[str],
             settings: Settings = SETTINGS) -> QualityReport:
    report = QualityReport(attempted=attempted, extracted=len(records) + duplicates_removed,
                           unique=len(records), duplicates_removed=duplicates_removed)
    if not records:
        report.level = "broken"
        report.warnings.append(
            "No usable records were produced. The page structure may have changed, or the "
            "site may require JavaScript rendering."
        )
        return report

    report.with_any_contact = sum(1 for r in records if r.has_contact())
    report.missing_names = sum(1 for r in records if not r.agent_name)
    denominator = attempted or len(records)
    named = len(records) - report.missing_names
    report.success_rate = named / denominator if denominator else 0.0

    for key in expected_fields:
        if key in FIELD_BY_KEY:
            report.field_fill[key] = round(_fill_rate(records, key), 3)

    if report.success_rate < settings.min_success_rate:
        report.warnings.append(
            f"Only {report.success_rate:.0%} of attempted profiles produced a named record "
            f"(target {settings.min_success_rate:.0%})."
        )
    if report.missing_names:
        report.warnings.append(
            f"{report.missing_names} record(s) have no agent name — the name selector or heading "
            "structure may have changed."
        )
    for key, rate in report.field_fill.items():
        if rate < settings.min_field_fill_rate:
            label = FIELD_BY_KEY[key].label
            report.warnings.append(
                f"{label} is present in only {rate:.0%} of records. Either the site does not "
                "publish it or the extraction rule needs review."
            )
    if duplicates_removed:
        report.warnings.append(
            f"{duplicates_removed} duplicate record(s) were removed before export."
        )
    contact_share = report.with_any_contact / len(records)
    if report.with_any_contact == 0:
        report.warnings.append(
            "No record has a phone or email. Contact fields are the most likely to break first "
            "when a site changes."
        )
    elif contact_share < settings.min_contact_share:
        report.warnings.append(
            f"{1 - contact_share:.0%} of records have no phone or email. For lead delivery that "
            "is usually too high; review the contact extraction rule before exporting."
        )

    if report.success_rate < settings.min_success_rate or report.with_any_contact == 0:
        report.level = "watch"
    if report.success_rate < 0.35 or report.missing_names > len(records) * 0.5:
        report.level = "broken"
    if report.level == "ok" and report.warnings:
        report.level = "watch"
    return report


def flags_summary(records: list[Record]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for record in records:
        for flag in record.quality_flags:
            counter[flag] += 1
    return dict(counter.most_common())


def export_columns(selected_fields: list[str]) -> list[str]:
    """Selected fields in catalogue order, then provenance columns."""
    ordered = [k for k in FIELD_BY_KEY if k in selected_fields]
    return ordered + list(META_KEYS)
