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
    looks_like_org_name,
    normalize_email,
    normalize_name,
    normalize_org,
    normalize_phone,
    normalize_website,
    parse_address,
    slugify,
)
from .settings import SETTINGS, Settings

_NAME_KEY = re.compile(r"[^a-z ]")
_RESERVED = set(FIELD_BY_KEY) | set(META_KEYS) | {"phone_digits", "_key", "address"}


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
    country = clean_text(f.get("country"))

    # If the adapter only produced a single address blob, split it here.
    if not city and not street and f.get("address"):
        parsed = parse_address(f.get("address"))
        street, city = parsed["street_address"], parsed["city"]
        state, postal = parsed["state"], parsed["postal_code"]
        country = country or parsed.get("country", "")
    elif street and not city:
        parsed = parse_address(street)
        if parsed.get("city") and parsed.get("street_address"):
            street = parsed["street_address"]
            city = parsed["city"]
            state = state or parsed["state"]
            postal = postal or parsed["postal_code"]
            country = country or parsed.get("country", "")

    agent_name = normalize_name(f.get("agent_name"))
    # A listing page is not a profile. Only use an explicit profile URL; the
    # listing URL lives in source_url and is exported separately.
    profile_url = canonical_url(raw.profile_url or f.get("profile_url") or "")
    source_url = canonical_url(raw.source_url or raw.profile_url or "")

    record = Record(
        agent_name=agent_name,
        brokerage_name=normalize_org(f.get("brokerage_name")),
        phone=phone_display,
        email=email,
        street_address=street[:200],
        city=city[:100],
        state=state[:50],
        postal_code=postal[:20],
        country=country[:60],
        website=normalize_website(f.get("website")),
        profile_url=profile_url,
        source_url=source_url,
        collected_at=collected,
        extraction_method=raw.method,
        adapter_id=adapter_id,
        phone_digits=digits,
    )

    # Arbitrary extra columns (title, licence number, languages, ...).
    extras: dict[str, str] = {}
    for raw_key, raw_value in f.items():
        key = str(raw_key)
        if key in _RESERVED or key.startswith("_") or raw_value is None:
            continue
        value = clean_text(raw_value)[:500]
        if not value:
            continue
        slug = slugify(key)
        if slug in _RESERVED or slug in extras:
            continue
        extras[slug] = value
        if len(extras) >= SETTINGS.max_extra_fields:
            break
    record.extra = extras

    flags: list[str] = []
    if not record.agent_name:
        flags.append("missing name")
    elif looks_like_org_name(record.agent_name):
        flags.append("name looks like a company")
    elif len(record.agent_name.split()) < 2:
        flags.append("short name")
    if not record.phone and not record.email:
        flags.append("no contact details")
    if record.phone and not record.email:
        flags.append("phone only")
    if record.email and not record.phone:
        flags.append("email only")
    if not record.city and not record.state and not record.street_address:
        flags.append("missing location")
    if record.email and record.email.split("@")[0] in {"info", "sales", "office", "admin", "contact", "hello"}:
        flags.append("generic office email")
    for warning in raw.warnings:
        if warning not in flags:
            flags.append(warning)
    record.quality_flags = flags
    return record


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------
def record_issues(record: Record) -> list[str]:
    """Operator-facing issues for one record, without changing its flags."""
    issues: list[str] = []
    if not record.agent_name:
        issues.append("No agent name")
    if not record.phone and not record.email:
        issues.append("No phone or email")
    if record.email and "@" not in record.email:
        issues.append("Invalid email")
    if not record.city and not record.state:
        issues.append("No city/state")
    if record.profile_url and not record.profile_url.startswith(("http://", "https://")):
        issues.append("Profile URL is not absolute")
    for flag in record.quality_flags:
        if flag in {"duplicate"}:
            issues.append("Duplicate removed")
    return list(dict.fromkeys(issues))


def flags_summary(records: list[Record]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for record in records:
        for flag in record.quality_flags:
            counter[flag] += 1
    return dict(counter.most_common())


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
    report.contact_share = report.with_any_contact / len(records)
    report.missing_names = sum(1 for r in records if not r.agent_name)
    report.issue_counts = flags_summary(records)
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
    if report.with_any_contact == 0:
        report.warnings.append(
            "No record has a phone or email. Contact fields are the most likely to break first "
            "when a site changes."
        )
    elif report.contact_share < settings.min_contact_share:
        report.warnings.append(
            f"{1 - report.contact_share:.0%} of records have no phone or email. For lead delivery "
            "that is usually too high; review the contact extraction rule before exporting."
        )

    if report.success_rate < settings.min_success_rate or report.with_any_contact == 0:
        report.level = "watch"
    if report.success_rate < 0.35 or report.missing_names > len(records) * 0.5:
        report.level = "broken"
    if report.level == "ok" and report.warnings:
        report.level = "watch"
    return report


def export_columns(selected_fields: list[str],
                   extra_keys: list[str] | tuple[str, ...] | None = None) -> list[str]:
    """Selected fields in catalogue order, then requested extras, then provenance."""
    ordered = [k for k in FIELD_BY_KEY if k in selected_fields]
    extras: list[str] = []
    if extra_keys is not None:
        extras = [k for k in dict.fromkeys(extra_keys) if k not in FIELD_BY_KEY and k not in META_KEYS]
    else:
        extras = [k for k in dict.fromkeys(selected_fields) if k not in FIELD_BY_KEY and k not in META_KEYS]
    return ordered + extras + list(META_KEYS)
