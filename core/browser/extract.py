"""Turn a detected structure into records, page after page.

The field mapping is the user-facing contract: field key -> column index. Nothing
here needs the operator to write a selector.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import bs4

from ..models import RawRecord, StructureModel
from ..normalize import canonical_url, clean_text, host_of, parse_address
from .structure import href_for, row_values, select_rows, value_for

# Which detected column kind feeds which export field.
_KIND_TO_FIELD = {
    "name": "agent_name",
    "phone": "phone",
    "email": "email",
    "address": "street_address",
    "city": "city",
    "brokerage": "brokerage_name",
    "url": "profile_url",
}

DEFAULT_MAPPING_ORDER = (
    "agent_name", "brokerage_name", "phone", "email",
    "street_address", "city", "state", "postal_code", "website", "profile_url",
)


@dataclass
class FieldMapping:
    """field key -> column index on the detected structure."""

    columns: dict[str, int] = field(default_factory=dict)

    def column_for(self, field_key: str) -> int | None:
        return self.columns.get(field_key)

    def to_dict(self) -> dict[str, int]:
        return dict(self.columns)


def suggest_mapping(structure: StructureModel) -> FieldMapping:
    """Pick the best column for each field from the auto-detected kinds."""
    mapping: dict[str, int] = {}
    used: set[int] = set()

    for column in structure.columns:
        target = _KIND_TO_FIELD.get(column.kind_hint)
        if target and target not in mapping and column.index not in used:
            mapping[target] = column.index
            used.add(column.index)

    return FieldMapping(mapping)


def _row_anchor_href(row: bs4.Tag, base_url: str) -> str:
    anchor = row.find("a", href=True)
    if not anchor:
        return ""
    href = str(anchor["href"]).strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return ""
    return canonical_url(urljoin(base_url, href))


def extract_page(html: str, page_url: str, structure: StructureModel,
                 mapping: FieldMapping) -> list[dict]:
    """Extract mapped records from one rendered page."""
    rows = select_rows(html, structure)
    out: list[dict] = []

    for row in rows:
        cells = row_values(row, page_url)
        record: dict[str, str] = {}

        for field_key, column_index in mapping.columns.items():
            column = structure.column(column_index)
            if column is None:
                continue
            # City/state/postal must never receive the whole address string: the
            # address splitter below fills them from the street column.
            if field_key in ("city", "state", "postal_code") and column_index == mapping.columns.get("street_address"):
                continue
            value = clean_text(value_for(cells, column))
            if field_key in ("profile_url", "website"):
                href = href_for(cells, column)
                if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    value = urljoin(page_url, href)
                elif value and value.startswith(("http", "/", "www.")):
                    value = urljoin(page_url, value)
            if value:
                record[field_key] = value

        # Profile URL fallback: the row's first real link.
        if not record.get("profile_url"):
            href = _row_anchor_href(row, page_url)
            if href:
                record["profile_url"] = href

        if not (record.get("agent_name") or record.get("phone") or record.get("email")):
            continue

        # Split a combined address when the site gives one blob.
        street = record.get("street_address", "")
        if street and not record.get("city"):
            parsed = parse_address(street)
            if parsed.get("city"):
                record["street_address"] = parsed["street_address"]
                record["city"] = parsed["city"]
                record["state"] = record.get("state") or parsed["state"]
                record["postal_code"] = record.get("postal_code") or parsed["postal_code"]

        # A "City, ST" cell should not leave the state glued to the city.
        city = record.get("city", "")
        if city and not record.get("state") and "," in city:
            head, _, tail = city.partition(",")
            tail = tail.strip()
            if re.fullmatch(r"[A-Za-z]{2}", tail):
                record["city"] = head.strip()
                record["state"] = tail.upper()

        record["_key"] = record.get("profile_url") or (
            f"{record.get('agent_name','')}|{record.get('phone','')}"
        )
        out.append(record)

    return out


def to_raw_records(rows: list[dict], source_url: str, method: str = "browser-dom") -> list[RawRecord]:
    records: list[RawRecord] = []
    for row in rows:
        fields = {k: v for k, v in row.items() if not k.startswith("_")}
        records.append(
            RawRecord(
                fields=fields,
                profile_url=fields.get("profile_url", ""),
                source_url=source_url,
                method=method,
            )
        )
    return records


# ---------------------------------------------------------------------------
# Profile detail pages
# ---------------------------------------------------------------------------
def _merge_missing(target: dict[str, str], extra: dict[str, str]) -> None:
    for key, value in extra.items():
        if value and not target.get(key):
            target[key] = value


def enrich_from_profile(html: str, profile_url: str) -> dict[str, str]:
    """Extract contact fields from a rendered profile page.

    Structured data first, then the same heuristic used by the static pipeline —
    which now sees the fully rendered DOM, so JavaScript-built contact blocks work.
    """
    from ..adapters.generic_jsonld import GenericJsonLdAdapter
    from ..adapters.generic_html import heuristic_profile
    from ..inspect_site import parse_html
    from ..net import FetchedPage

    page = FetchedPage(
        url=profile_url, final_url=profile_url, status_code=200, text=html,
        headers={"content-type": "text/html"},
    )
    fields: dict[str, str] = {}

    try:
        raw = GenericJsonLdAdapter().parse_profile(page, ctx=None)  # type: ignore[arg-type]
        if raw is not None and raw.method.startswith("json-ld"):
            _merge_missing(fields, {k: str(v) for k, v in raw.fields.items() if v})
    except Exception:
        pass

    try:
        fallback = heuristic_profile(parse_html(html), profile_url)
        _merge_missing(fields, {k: str(v) for k, v in fallback.fields.items() if v})
    except Exception:
        pass

    return fields


def same_host(profile_url: str, listing_url: str) -> bool:
    a, b = host_of(profile_url), host_of(listing_url)
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def looks_like_profile_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    if not path or path == "/":
        return False
    return bool(re.search(r"/(agent|agents|team|people|profile|staff|realtor|broker|attorney|advisor)s?/", path))
