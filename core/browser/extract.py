"""Turn a detected structure into records, page after page.

The field mapping is the user-facing contract: field key -> column index. Nothing
here needs the operator to write a selector.

Two things make this work on real directories rather than only clean demos:

* a mapped field that the chosen column leaves blank is filled from the row itself
  (tel:/mailto: anchors, text patterns, structured attributes), and
* the profile link is chosen by scoring every anchor in the row, not by taking the
  first one, which is usually the logo or a navigation link.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import bs4

from ..models import RawRecord, StructureModel
from ..normalize import (
    canonical_url,
    clean_text,
    email_from_fragment,
    find_emails,
    find_phones,
    looks_like_org_name,
    looks_like_person_name,
    normalize_email,
    normalize_phone,
    normalize_website,
    parse_address,
    same_site as _same_site,
    tel_from_fragment,
)
from .structure import href_for, row_values, select_rows, value_for

# Which detected column kind feeds which export field.
_KIND_TO_FIELD = {
    "name": "agent_name",
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

DEFAULT_MAPPING_ORDER = (
    "agent_name", "brokerage_name", "phone", "email",
    "street_address", "city", "state", "postal_code", "country", "website", "profile_url",
)

_PROFILE_HINTS = re.compile(
    r"/(agents?|team|people|person|profile|profiles|staff|realtors?|brokers?|associates?|"
    r"attorneys?|lawyers?|advisors?|loan-officers?|members|employees|directory)/", re.I
)
_LINK_PENALTY = re.compile(
    r"(facebook|twitter|x\.com|linkedin|instagram|youtube|tiktok|pinterest|"
    r"mailto:|tel:|javascript:|^#|/privacy|/terms|/login|/search)", re.I
)
_ADDRESS_LINE = re.compile(
    r"\d{1,7}\s+[A-Za-z0-9.'\- ]{2,50}\b(?:st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|"
    r"ln|lane|way|ct|court|pl|place|pkwy|parkway|hwy|highway)\b[^\n]{0,80}",
    re.I,
)
_HEADER_WORDS = {
    "name", "agent", "agent name", "full name", "phone", "phone number", "email", "e-mail",
    "office", "brokerage", "company", "address", "city", "state", "zip", "title", "role",
    "contact", "profile", "website", "sort", "filter",
}


@dataclass
class FieldMapping:
    """field key -> column index on the detected structure.

    Field keys are normally catalogue keys. Any other key is kept as an extra
    column (for example `license_number`), which is how the operator collects
    business information the fixed catalogue does not anticipate.
    """

    columns: dict[str, int] = field(default_factory=dict)

    def column_for(self, field_key: str) -> int | None:
        return self.columns.get(field_key)

    def to_dict(self) -> dict[str, int]:
        return dict(self.columns)


def suggest_mapping(structure: StructureModel) -> FieldMapping:
    """Pick the best column for each field from the auto-detected kinds.

    Beyond the core catalogue this maps genuinely useful extra columns: a job
    title, a second phone/email column, and separate first/last name columns.
    """
    mapping: dict[str, int] = {}
    used: set[int] = set()
    url_candidates: list[int] = []
    first_name: int | None = None
    last_name: int | None = None
    extra_candidates: dict[str, int] = {}

    for column in structure.columns:
        if column.kind_hint == "url":
            url_candidates.append(column.index)
            continue
        if column.kind_hint == "first_name":
            if first_name is None and column.fill_rate >= 0.3:
                first_name = column.index
            continue
        if column.kind_hint == "last_name":
            if last_name is None and column.fill_rate >= 0.3:
                last_name = column.index
            continue
        target = _KIND_TO_FIELD.get(column.kind_hint)
        if target and target not in mapping and column.index not in used:
            mapping[target] = column.index
            used.add(column.index)
        elif column.kind_hint == "title" and column.fill_rate >= 0.3:
            extra_candidates.setdefault("title", column.index)
        elif column.kind_hint == "phone" and column.fill_rate >= 0.4:
            extra_candidates.setdefault("phone_alt", column.index)
        elif column.kind_hint == "email" and column.fill_rate >= 0.4:
            extra_candidates.setdefault("email_alt", column.index)

    # Separate first/last columns: point the name field at the first-name column;
    # extraction joins the corresponding last-name column into the full name.
    if "agent_name" not in mapping and first_name is not None:
        mapping["agent_name"] = first_name
        used.add(first_name)
        if last_name is not None:
            extra_candidates.setdefault("last_name", last_name)

    # The first URL-like column is usually the profile. A second one is only the
    # agent's own website when its values actually look like domains; otherwise
    # "View profile" would be exported as a website.
    for index in url_candidates:
        column = structure.column(index)
        if "profile_url" not in mapping:
            mapping["profile_url"] = index
            used.add(index)
        elif "website" not in mapping and column is not None:
            if any(normalize_website(sample) for sample in column.sample_values):
                mapping["website"] = index
                used.add(index)
            else:
                extra_candidates.setdefault("profile_url_alt", index)

    for key, index in extra_candidates.items():
        if index not in used and key not in mapping:
            mapping[key] = index
    return FieldMapping(mapping)


# ---------------------------------------------------------------------------
# Link scoring
# ---------------------------------------------------------------------------
def _link_score(href: str, text: str, page_url: str, name: str = "") -> int:
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return -99
    absolute = urljoin(page_url, href)
    if not absolute.startswith(("http://", "https://")):
        return -99
    lowered = absolute.lower()
    if _LINK_PENALTY.search(lowered):
        return -99
    path = urlparse(absolute).path
    if path in ("", "/"):
        return -99
    if re.search(r"\.(?:pdf|jpe?g|png|gif|webp|svg|zip|docx?)$", path, re.I):
        return -99
    score = 0
    if _same_site(absolute, page_url):
        score += 3
    else:
        score -= 1
    if _PROFILE_HINTS.search(path):
        score += 8
    if re.fullmatch(r"/(?:agents?|team|people|staff|about|contact|directory)/?", path, re.I):
        score -= 8  # a roster or contact page is not a profile
    if re.search(r"/(contact|about)(/|$)", path, re.I):
        score -= 4  # a contact sub-page is still usable, but the main profile is better
    segments = [s for s in path.split("/") if s]
    if len(segments) >= 2:
        score += 2
    if len(segments) == 1 and re.fullmatch(r"[a-z0-9][a-z0-9\-]{3,60}", segments[0], re.I):
        score += 2
    label = clean_text(text).lower()
    if name and clean_text(text).lower() in {name.lower(), f"view {name}".lower()}:
        score += 3
    if re.search(r"(view|profile|details?|read more|learn more)", label):
        score += 2
    if re.search(r"(logo|home|menu|next|previous|back)", label):
        score -= 6
    return score


def _row_attribute(row: bs4.Tag, keys: tuple[str, ...]) -> str:
    for node in [row, *row.find_all(True)]:
        for key in keys:
            value = node.get(key)
            if value:
                text = clean_text(value)
                if text:
                    return text
    return ""


def _row_profile_href(row: bs4.Tag, page_url: str, name: str = "") -> str:
    best_score = 0
    best = ""
    for anchor in row.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        absolute = urljoin(page_url, href)
        if not _same_site(absolute, page_url):
            continue  # an external agent site is a website, not a directory profile
        score = _link_score(href, anchor.get_text(" "), page_url, name)
        if score > best_score:
            best_score = score
            best = absolute
    return canonical_url(best) if best else ""


def _row_website(row: bs4.Tag, page_url: str) -> str:
    for anchor in row.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if href.startswith("//"):
            href = "https:" + href
        if not href.startswith(("http://", "https://", "www.")):
            continue
        absolute = urljoin(page_url, href)
        if _LINK_PENALTY.search(absolute.lower()):
            continue
        label = clean_text(anchor.get_text(" "))
        if _same_site(absolute, page_url) and not re.search(r"https?://|www\.", label, re.I):
            continue
        website = normalize_website(absolute)
        if website:
            return website
    return ""


# ---------------------------------------------------------------------------
# Row value helpers
# ---------------------------------------------------------------------------
def _first_anchor_value(row: bs4.Tag, scheme: str) -> str:
    for anchor in row.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if href.lower().startswith(scheme):
            if scheme == "tel:":
                value = tel_from_fragment(href)
            else:
                value = email_from_fragment(href)
            if value:
                return value
            text_value = tel_from_fragment(anchor.get_text(" ")) if scheme == "tel:" else email_from_fragment(anchor.get_text(" "))
            if text_value:
                return text_value
    return ""


def _guess_name(row: bs4.Tag, page_url: str) -> str:
    data_name = _row_attribute(row, ("data-full-name", "data-name", "data-agent-name"))
    if data_name and looks_like_person_name(data_name):
        return data_name
    for selector in ("h1", "h2", "h3", "h4", "h5", "[itemprop='name']", ".name", "[class*='name']"):
        for node in row.select(selector):
            text = clean_text(node.get_text(" "))
            if looks_like_person_name(text):
                return text
    for anchor in row.find_all("a", href=True):
        text = clean_text(anchor.get_text(" "))
        if looks_like_person_name(text):
            return text
    for image in row.find_all("img", alt=True):
        text = clean_text(image.get("alt"))
        if looks_like_person_name(text):
            return text
    return ""


def _guess_brokerage(row: bs4.Tag) -> str:
    data_org = _row_attribute(row, ("data-brokerage", "data-company", "data-office"))
    if data_org and 2 <= len(data_org) <= 160:
        return data_org
    for selector in ("[itemprop='worksFor']", "[itemprop='affiliation']", ".brokerage", ".company",
                     ".office-name", "[class*='brokerage']", "[class*='company']"):
        node = row.select_one(selector)
        if node:
            text = clean_text(node.get_text(" "))
            if 2 <= len(text) <= 160 and looks_like_org_name(text):
                return text
    for node in row.find_all(True):
        classes = " ".join(node.get("class") or [])
        if re.search(r"(brokerage|company|office-name|agency|firm)", classes, re.I):
            text = clean_text(node.get_text(" "))
            if 2 <= len(text) <= 160 and looks_like_org_name(text):
                return text
    return ""


def _is_header_row(record: dict, row_text: str, profile_url: str,
                   headers: list[str] | None = None) -> bool:
    if profile_url:
        return False
    normalized = clean_text(row_text).lower()
    if normalized in _HEADER_WORDS:
        return True
    if len(normalized) < 30 and all(part in _HEADER_WORDS for part in re.split(r"\s{2,}|\|", normalized)):
        return True
    if headers:
        header_set = {clean_text(h).lower() for h in headers if h}
        values = {
            clean_text(value).lower() for key, value in record.items()
            if not key.startswith("_") and clean_text(value)
        }
        if values and len(values & header_set) >= max(1, len(values) // 2):
            return True
    return not any(clean_text(value) for key, value in record.items() if not key.startswith("_"))


# ---------------------------------------------------------------------------
# Page extraction
# ---------------------------------------------------------------------------
def extract_page(html: str, page_url: str, structure: StructureModel,
                 mapping: FieldMapping,
                 selected_fields: list[str] | tuple[str, ...] | None = None) -> list[dict]:
    """Extract mapped records from one rendered page.

    `selected_fields` lets the static engine ask for fields that were not mapped
    (for example phone, when no phone *column* exists but every card has a tel:
    link). In Browser mode it stays None so an explicit "do not collect" choice
    in the mapping is respected.
    """
    rows = select_rows(html, structure)
    out: list[dict] = []
    eligible = set(mapping.columns)
    if selected_fields:
        eligible.update(str(field) for field in selected_fields)

    for row in rows:
        cells = row_values(row, page_url)
        row_text = clean_text(row.get_text(" "))
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
            if field_key in ("profile_url", "website", "profile_url_alt"):
                href = href_for(cells, column)
                if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    value = urljoin(page_url, href)
                elif value and value.startswith(("http", "/", "www.")):
                    value = urljoin(page_url, value)
            elif field_key in ("phone", "phone_alt"):
                display, _digits = normalize_phone(value)
                value = display or value
            elif field_key in ("email", "email_alt"):
                value = normalize_email(value) or value
            if value:
                record[field_key] = value

        # --- combine split first/last name columns ---------------------------
        name_column = structure.column(mapping.column_for("agent_name"))
        if name_column is not None and name_column.kind_hint in ("first_name", "name"):
            first = clean_text(record.get("agent_name", ""))
            last_column = structure.column(mapping.column_for("last_name"))
            if last_column is None and name_column.kind_hint == "first_name":
                last_column = next(
                    (c for c in structure.columns if c.kind_hint == "last_name"), None
                )
            last = clean_text(value_for(cells, last_column)) if last_column is not None else ""
            if first and last and first.lower() != last.lower():
                record["agent_name"] = f"{first} {last}".strip()

        # --- profile URL: score every anchor, not just the first -------------
        if not record.get("profile_url"):
            href = _row_profile_href(row, page_url, record.get("agent_name", ""))
            if href:
                record["profile_url"] = href

        # --- name ------------------------------------------------------------
        if "agent_name" in eligible and not record.get("agent_name"):
            guessed = _guess_name(row, page_url)
            if guessed:
                record["agent_name"] = guessed
                record["_name_guessed"] = "1"

        # --- contact fallbacks ----------------------------------------------
        if "phone" in eligible and not record.get("phone"):
            phone = (
                _row_attribute(row, ("data-phone", "data-telephone", "data-tel", "data-mobile", "data-cell"))
                or _first_anchor_value(row, "tel:")
                or (find_phones(row_text, limit=1) or [""])[0]
            )
            if phone:
                record["phone"] = phone
                record["_phone_from_text"] = "1"
        if "email" in eligible and not record.get("email"):
            email = (
                _row_attribute(row, ("data-email",))
                or _first_anchor_value(row, "mailto:")
                or (find_emails(row_text, limit=1) or [""])[0]
            )
            if email:
                record["email"] = email
                record["_email_from_text"] = "1"

        # --- address fallback -------------------------------------------------
        if "street_address" in eligible and not record.get("street_address"):
            attribute_address = _row_attribute(
                row, ("data-address", "data-street", "data-office-address")
            )
            match = _ADDRESS_LINE.search(attribute_address or row_text)
            if match:
                parsed = parse_address(match.group(0))
                if parsed.get("street_address"):
                    record["street_address"] = parsed["street_address"]
                    record["_address_from_text"] = "1"

        # --- brokerage fallback ----------------------------------------------
        if "brokerage_name" in eligible and not record.get("brokerage_name"):
            org = _row_attribute(row, ("data-brokerage", "data-company", "data-office")) \
                or _guess_brokerage(row)
            if org:
                record["brokerage_name"] = org

        # --- website fallback --------------------------------------------------
        if "website" in eligible and not record.get("website"):
            site = _row_website(row, page_url)
            if site:
                record["website"] = site

        if _is_header_row(record, row_text, record.get("profile_url", ""), structure.headers):
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
                record["country"] = record.get("country") or parsed.get("country", "")

        # A "City, ST" cell should not leave the state glued to the city.
        city = record.get("city", "")
        if city and not record.get("state") and "," in city:
            head, _, tail = city.partition(",")
            tail = tail.strip()
            if re.fullmatch(r"[A-Za-z]{2}", tail):
                record["city"] = head.strip()
                record["state"] = tail.upper()

        record["_key"] = record.get("profile_url") or (
            f"{record.get('agent_name','')}|{record.get('phone','')}|{record.get('email','')}"
        )
        record["_source_url"] = page_url
        out.append(record)

    return out


def to_raw_records(rows: list[dict], source_url: str, method: str = "browser-dom") -> list[RawRecord]:
    records: list[RawRecord] = []
    for row in rows:
        fields = {k: v for k, v in row.items() if not k.startswith("_")}
        warnings: list[str] = []
        if row.get("_name_guessed"):
            warnings.append("name inferred from row")
        if row.get("_phone_from_text"):
            warnings.append("phone inferred from row text")
        if row.get("_email_from_text"):
            warnings.append("email inferred from row text")
        records.append(
            RawRecord(
                fields=fields,
                profile_url=fields.get("profile_url", ""),
                source_url=row.get("_source_url") or source_url,
                method=method,
                warnings=warnings,
            )
        )
    return records


# ---------------------------------------------------------------------------
# Merging embedded and DOM records
# ---------------------------------------------------------------------------
def _raw_key(record: RawRecord) -> str:
    fields = record.fields or {}
    parts = [str(fields.get(k, "") or "").strip().lower() for k in ("agent_name", "phone", "email", "profile_url")]
    return "|".join(parts) if any(parts) else ""


def merge_raw_records(primary: list[RawRecord], secondary: list[RawRecord]) -> list[RawRecord]:
    """Fill gaps in embedded records with DOM values, without duplicating people."""
    merged = list(primary)
    index = {_raw_key(record): record for record in merged if _raw_key(record)}

    for record in secondary:
        key = _raw_key(record)
        target = index.get(key)
        if target is None:
            # Match on name + one contact signal, then on name + city.
            name = str((record.fields or {}).get("agent_name", "")).strip().lower()
            if name:
                target = next(
                    (r for r in merged
                     if str((r.fields or {}).get("agent_name", "")).strip().lower() == name),
                    None,
                )
        if target is None:
            # Do not append a DOM row that contributed no identity or contact
            # fields; it would create a blank record beside the embedded ones.
            meaningful = any(
                (record.fields or {}).get(field)
                for field in ("agent_name", "phone", "email", "street_address", "city", "brokerage_name")
            )
            if not meaningful:
                continue
            merged.append(record)
            index[key] = record
            continue
        target.fields = target.fields or {}
        for field_key, value in (record.fields or {}).items():
            if value and not target.fields.get(field_key):
                target.fields[field_key] = value
        if record.profile_url and not target.profile_url:
            target.profile_url = record.profile_url
        target.method = f"{target.method}+{record.method}" if record.method not in target.method else target.method
    return merged


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

    # A configured site adapter can contribute fields the generic parsers do not
    # know about (licence number, title, languages, ...).
    try:
        from ..adapters.registry import resolve_adapter

        configured = resolve_adapter(profile_url, html)
        if configured.id not in {"generic_html", "generic_jsonld", "custom_site"}:
            raw = configured.parse_profile(page, ctx=None)  # type: ignore[arg-type]
            if raw is not None:
                _merge_missing(fields, {k: str(v) for k, v in raw.fields.items() if v})
    except Exception:
        pass

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


def same_host_url(profile_url: str, listing_url: str) -> bool:
    return _same_site(profile_url, listing_url)


# Backwards-compatible name used by the engine and older adapters.
same_host = same_host_url


def looks_like_profile_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    if not path or path == "/":
        return False
    return bool(_PROFILE_HINTS.search(path + "/") or re.search(
        r"/(agent|agents|team|people|profile|staff|realtor|broker|attorney|advisor|member)s?/", path
    ))
