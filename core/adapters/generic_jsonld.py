"""schema.org JSON-LD adapter.

Many brokerage sites publish structured data for SEO. When they do, it is the
highest-quality source on the page: field names are unambiguous and values are
clean. This adapter reads it before falling back to heuristics.
"""
from __future__ import annotations

import json
from urllib.parse import urljoin

from ..inspect_site import discover_profile_links, parse_html
from ..models import RawRecord
from ..net import FetchedPage
from ..normalize import (
    canonical_url,
    clean_text,
    find_emails,
    host_of,
    normalize_email,
    normalize_name,
    normalize_org,
    normalize_phone,
    parse_address,
    same_site,
    slugify,
)
from .base import AdapterContext, DirectoryAdapter
from .generic_html import heuristic_profile, next_page_urls_from_soup

_PERSON_TYPES = {"person", "realestateagent", "employee", "agent"}


def _iter_jsonld_blocks(text: str):
    soup = parse_html(text)
    for tag in soup.find_all("script", attrs={"type": lambda v: v and "ld+json" in v.lower()}):
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except Exception:
            continue


def _flatten(node, out: list[dict]) -> None:
    if isinstance(node, list):
        for item in node:
            _flatten(item, out)
    elif isinstance(node, dict):
        out.append(node)
        if "@graph" in node:
            _flatten(node["@graph"], out)


def _types_of(node: dict) -> set[str]:
    raw = node.get("@type") or node.get("type") or ""
    if isinstance(raw, str):
        return {raw.lower()}
    if isinstance(raw, list):
        return {str(t).lower() for t in raw}
    return set()


def _as_text(value) -> str:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return clean_text(value.get("name") or value.get("@id") or "")
    if isinstance(value, list):
        for item in value:
            text = _as_text(item)
            if text:
                return text
    return ""


def _address_fields(value) -> dict[str, str]:
    if isinstance(value, str):
        return {k: v for k, v in parse_address(value).items() if v}
    if isinstance(value, dict):
        street = clean_text(value.get("streetAddress") or "")
        city = clean_text(value.get("addressLocality") or "")
        state = clean_text(value.get("addressRegion") or "")
        postal = clean_text(value.get("postalCode") or "")
        country = clean_text(value.get("addressCountry") or "")
        if isinstance(country, dict):
            country = clean_text(country.get("name") or country.get("@id") or "")
        if not street and not city:
            return {}
        return {
            "street_address": street,
            "city": city,
            "state": state.upper() if len(state) == 2 else state,
            "postal_code": postal,
            "country": country,
        }
    if isinstance(value, list):
        for item in value:
            fields = _address_fields(item)
            if fields:
                return fields
    return {}


def _person_nodes(blocks: list[dict]) -> list[dict]:
    people = [n for n in blocks if _types_of(n) & _PERSON_TYPES]
    # Prefer the richest node when several describe the same person.
    people.sort(key=lambda n: len(n), reverse=True)
    return people


def _person_fields(person: dict) -> dict[str, str]:
    """Map one schema.org Person/RealEstateAgent node onto record fields."""
    fields: dict[str, str] = {}

    name = normalize_name(_as_text(person.get("name")))
    if not name:
        first = _as_text(person.get("givenName") or person.get("firstName"))
        last = _as_text(person.get("familyName") or person.get("lastName"))
        name = normalize_name(" ".join(part for part in (first, last) if part))
    if name:
        fields["agent_name"] = name

    org = _as_text(person.get("worksFor")) or _as_text(person.get("memberOf")) \
        or _as_text(person.get("affiliation")) or _as_text(person.get("parentOrganization"))
    if org:
        fields["brokerage_name"] = normalize_org(org)

    telephone = person.get("telephone")
    if isinstance(telephone, list):
        telephone = telephone[0] if telephone else ""
    display, _digits = normalize_phone(telephone or "")
    if display:
        fields["phone"] = display

    email = normalize_email(person.get("email"))
    if not email:
        found = find_emails(_as_text(person.get("email")) or "", limit=1)
        email = found[0] if found else ""
    if email:
        fields["email"] = email

    fields.update({k: v for k, v in _address_fields(person.get("address")).items() if v})

    for key, label in (("jobTitle", "title"), ("worksFor", "")):
        if label:
            value = _as_text(person.get(key))
            if value:
                fields[slugify(label)] = value
    languages = person.get("knowsLanguage") or person.get("languages")
    language_text = _as_text(languages)
    if language_text:
        fields["languages"] = language_text
    return fields


class GenericJsonLdAdapter(DirectoryAdapter):
    id = "generic_jsonld"
    label = "schema.org structured data"
    version = "1.0"
    expected_fields = ("agent_name", "phone", "email", "street_address", "city", "state")
    description = (
        "Reads schema.org Person / RealEstateAgent markup. Highest fidelity when present; "
        "falls back to heuristics for fields the markup omits."
    )

    def matches(self, url: str, html: str = "") -> bool:
        return False  # selected by inspection, not by domain

    def profile_links(self, page: FetchedPage, ctx: AdapterContext) -> list[str]:
        blocks: list[dict] = []
        for block in _iter_jsonld_blocks(page.text):
            _flatten(block, blocks)

        links: list[str] = []
        for node in blocks:
            items = node.get("itemListElement")
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    target = item.get("url")
                    if not isinstance(target, str):
                        inner = item.get("item")
                        if isinstance(inner, dict) and isinstance(inner.get("url"), str):
                            target = inner["url"]
                        elif isinstance(inner, dict) and isinstance(inner.get("@id"), str):
                            target = inner["@id"]
                    if isinstance(target, str):
                        links.append(urljoin(page.final_url, target))
            if _types_of(node) & _PERSON_TYPES and isinstance(node.get("url"), str):
                links.append(urljoin(page.final_url, node["url"]))

        if not links:
            soup = parse_html(page.text)
            links, _sig, _count, _selector = discover_profile_links(
                soup, page.final_url, host_of(page.final_url)
            )
        return [canonical_url(u) for u in dict.fromkeys(links) if u]

    def listing_records(self, page: FetchedPage, ctx: AdapterContext) -> list[RawRecord]:
        """Use complete Person nodes on the listing page without opening profiles."""
        blocks: list[dict] = []
        for block in _iter_jsonld_blocks(page.text):
            _flatten(block, blocks)

        people: list[dict] = []
        for node in blocks:
            if _types_of(node) & _PERSON_TYPES:
                people.append(node)
            items = node.get("itemListElement")
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and isinstance(item.get("item"), dict):
                        inner = item["item"]
                        if _types_of(inner) & _PERSON_TYPES:
                            people.append(inner)

        records: list[RawRecord] = []
        seen: set[str] = set()
        for person in people:
            fields = _person_fields(person)
            if not fields.get("agent_name"):
                continue

            declared = person.get("url") or person.get("@id")
            profile_url = (
                canonical_url(urljoin(page.final_url, declared))
                if isinstance(declared, str) and declared.startswith(("/", "http"))
                else ""
            )
            if profile_url:
                fields.setdefault("profile_url", profile_url)
            same_as = person.get("sameAs")
            if isinstance(same_as, list):
                same_as = next((item for item in same_as if isinstance(item, str)), "")
            if isinstance(same_as, str) and same_as.startswith("http"):
                fields.setdefault("website", canonical_url(same_as))

            if not any(fields.get(key) for key in
                       ("phone", "email", "street_address", "city", "website", "profile_url")):
                continue
            key = fields.get("email") or fields.get("phone") or fields["agent_name"].lower()
            if key in seen:
                continue
            seen.add(key)

            records.append(RawRecord(
                fields=fields,
                profile_url=profile_url,
                source_url=canonical_url(page.final_url),
                method="json-ld (listing)",
            ))
        return records

    def next_page_urls(self, page: FetchedPage, ctx: AdapterContext) -> list[str]:
        return next_page_urls_from_soup(parse_html(page.text), page.final_url, ctx.max_list_pages)

    def parse_profile(self, page: FetchedPage, ctx: AdapterContext) -> RawRecord | None:
        blocks: list[dict] = []
        for block in _iter_jsonld_blocks(page.text):
            _flatten(block, blocks)

        people = _person_nodes(blocks)
        if not people:
            record = heuristic_profile(parse_html(page.text), page.final_url)
            if record:
                record.method = "heuristic (no structured data on profile)"
            return record

        person = people[0]
        fields = _person_fields(person)

        # Structured data is often sparse on profiles; fill gaps from the page.
        if len(fields) < 4:
            fallback = heuristic_profile(parse_html(page.text), page.final_url)
            for key, value in (fallback.fields or {}).items():
                fields.setdefault(key, value)

        warnings: list[str] = []
        if not fields.get("agent_name"):
            warnings.append("no name in structured data or page")

        profile_url = canonical_url(page.final_url)
        declared = person.get("url")
        if isinstance(declared, str):
            resolved = urljoin(page.final_url, declared)
            if same_site(resolved, page.final_url):
                profile_url = canonical_url(resolved)

        return RawRecord(
            fields=fields,
            profile_url=profile_url,
            source_url=profile_url,
            method="json-ld",
            warnings=warnings,
        )
