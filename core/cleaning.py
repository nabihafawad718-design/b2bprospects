"""Post-extraction cleaning, filtering and duplicate review.

The engine returns every record it observed. This module lets the operator shape
that dataset before delivery without mutating the original run: filters, a chosen
duplicate strategy, sorting, coverage and issue extraction all work on copies.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Iterable

from .models import FIELD_BY_KEY, Record
from .quality import dedupe as smart_dedupe
from .quality import flags_summary, record_issues

_NAME_KEY = re.compile(r"[^a-z ]")


def clone_record(record: Record) -> Record:
    cloned = copy.copy(record)
    cloned.quality_flags = list(record.quality_flags)
    cloned.extra = dict(record.extra)
    return cloned


def clone_records(records: Iterable[Record]) -> list[Record]:
    return [clone_record(record) for record in records]


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
@dataclass
class FilterSpec:
    """Everything the review screen lets the operator narrow down."""

    query: str = ""
    require_name: bool = False
    require_phone: bool = False
    require_email: bool = False
    require_contact: bool = False
    city: str = ""
    state: str = ""
    missing_fields: list[str] = field(default_factory=list)
    excluded_flags: list[str] = field(default_factory=list)
    require_flags: list[str] = field(default_factory=list)

    def active(self) -> bool:
        return any((
            self.query.strip(), self.require_name, self.require_phone, self.require_email,
            self.require_contact, self.city.strip(), self.state.strip(),
            self.missing_fields, self.excluded_flags, self.require_flags,
        ))

    def describe(self) -> list[str]:
        bits: list[str] = []
        if self.query.strip():
            bits.append(f"search “{self.query.strip()}”")
        if self.require_name:
            bits.append("named records only")
        if self.require_phone:
            bits.append("has phone")
        if self.require_email:
            bits.append("has email")
        if self.require_contact:
            bits.append("has any contact detail")
        if self.city.strip():
            bits.append(f"city contains “{self.city.strip()}”")
        if self.state.strip():
            bits.append(f"state is “{self.state.strip()}”")
        for key in self.missing_fields:
            bits.append(f"missing {FIELD_BY_KEY[key].label if key in FIELD_BY_KEY else key}")
        for flag in self.require_flags:
            bits.append(f"flagged “{flag}”")
        for flag in self.excluded_flags:
            bits.append(f"excluding “{flag}”")
        return bits


def _matches_query(record: Record, needle: str) -> bool:
    if not needle:
        return True
    haystack = " ".join(
        str(value) for value in [*record.to_row().values(), *(record.extra or {}).values()]
        if value is not None
    ).lower()
    return needle in haystack


def filter_records(records: list[Record], spec: FilterSpec) -> list[Record]:
    needle = spec.query.strip().lower()
    city = spec.city.strip().lower()
    state = spec.state.strip().lower()
    out: list[Record] = []
    for record in records:
        if not _matches_query(record, needle):
            continue
        if spec.require_name and not record.agent_name:
            continue
        if spec.require_phone and not record.phone:
            continue
        if spec.require_email and not record.email:
            continue
        if spec.require_contact and not record.has_contact():
            continue
        if city and city not in record.city.lower():
            continue
        if state and state != record.state.lower() and state != record.state.lower().strip():
            continue
        if any(not str(getattr(record, key, "") or "").strip() for key in spec.missing_fields
               if key in FIELD_BY_KEY):
            continue
        flags = set(record.quality_flags)
        if spec.require_flags and not flags.intersection(spec.require_flags):
            continue
        if spec.excluded_flags and flags.intersection(spec.excluded_flags):
            continue
        out.append(record)
    return out


# ---------------------------------------------------------------------------
# Duplicate strategies
# ---------------------------------------------------------------------------
DEDUPE_STRATEGIES: dict[str, str] = {
    "smart": "Smart (URL, then name + phone, then name + firm + city)",
    "profile_url": "Profile URL only",
    "name_phone": "Name + phone number",
    "name_org_city": "Name + brokerage + city",
    "email": "Email address",
    "phone": "Phone number",
    "name": "Name only (aggressive)",
    "none": "Keep every record",
}


def _name_key(value: str) -> str:
    return _NAME_KEY.sub("", (value or "").lower()).strip()


def _strategy_key(record: Record, strategy: str) -> str:
    if strategy == "profile_url":
        return record.profile_url.rstrip("/").lower()
    if strategy == "name_phone":
        return f"{_name_key(record.agent_name)}|{record.phone_digits}" if _name_key(record.agent_name) and record.phone_digits else ""
    if strategy == "name_org_city":
        name, org, city = _name_key(record.agent_name), _name_key(record.brokerage_name), record.city.lower().strip()
        return f"{name}|{org}|{city}" if name and org and city else ""
    if strategy == "email":
        return record.email.lower()
    if strategy == "phone":
        return record.phone_digits
    if strategy == "name":
        return _name_key(record.agent_name)
    return ""


def dedupe_records(records: list[Record], strategy: str = "smart") -> tuple[list[Record], int]:
    """Return (unique copies, removed count) for the chosen strategy."""
    copies = clone_records(records)
    if strategy == "none":
        return copies, 0
    if strategy == "smart":
        return smart_dedupe(copies)

    seen: dict[str, Record] = {}
    unique: list[Record] = []
    removed = 0
    for record in copies:
        key = _strategy_key(record, strategy)
        if not key:
            unique.append(record)
            continue
        if key in seen:
            removed += 1
            record.duplicate_of = seen[key].profile_url or seen[key].agent_name
            if "duplicate" not in record.quality_flags:
                record.quality_flags.append("duplicate")
            continue
        seen[key] = record
        unique.append(record)
    return unique, removed


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------
def available_extra_keys(records: list[Record]) -> list[str]:
    keys: list[str] = []
    for record in records:
        for key in (record.extra or {}):
            if key not in keys:
                keys.append(key)
    return keys


def sort_records(records: list[Record], key: str = "agent_name", reverse: bool = False) -> list[Record]:
    if key in ("agent_name", "brokerage_name", "city", "state", "email", "phone"):
        return sorted(records, key=lambda r: (str(getattr(r, key, "") or "").lower(), r.agent_name.lower()), reverse=reverse)
    if key in available_extra_keys(records):
        return sorted(records, key=lambda r: (r.extra.get(key, "").lower(), r.agent_name.lower()), reverse=reverse)
    return list(records)


def coverage(records: list[Record], keys: list[str]) -> dict[str, float]:
    if not records:
        return {key: 0.0 for key in keys}
    out: dict[str, float] = {}
    for key in keys:
        if key in FIELD_BY_KEY:
            filled = sum(1 for record in records if str(getattr(record, key, "") or "").strip())
        else:
            filled = sum(1 for record in records if str((record.extra or {}).get(key, "") or "").strip())
        out[key] = filled / len(records)
    return out


def issue_rows(records: list[Record]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, record in enumerate(records, start=1):
        issues = record_issues(record)
        if not issues:
            continue
        rows.append({
            "Row": str(index),
            "Name": record.agent_name,
            "Brokerage": record.brokerage_name,
            "Phone": record.phone,
            "Email": record.email,
            "Profile URL": record.profile_url,
            "Issues": "; ".join(issues),
            "Quality flags": "; ".join(record.quality_flags),
        })
    return rows


def issue_counts(records: list[Record]) -> dict[str, int]:
    return flags_summary(records)


def filter_summary(before: int, after: int, spec: FilterSpec) -> dict[str, str]:
    summary = {
        "Records before filtering": str(before),
        "Records after filtering": str(after),
    }
    described = spec.describe()
    if described:
        summary["Filters applied"] = "; ".join(described)
    return summary
