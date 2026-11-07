"""Embedded JSON layer.

Many JavaScript directories ship the records in the HTML as a JSON payload
(`__NEXT_DATA__`, `__INITIAL_STATE__`, `application/json` blocks, Next.js App
Router flight chunks) and render them client-side. When that payload is present it
is the cleanest possible source and needs no DOM guessing.

This layer is deliberately conservative: it only accepts an array of objects that
share a shape and contain at least one recognisable field name.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from ..inspect_site import parse_html
from ..models import RawRecord
from ..normalize import (
    canonical_url,
    clean_text,
    normalize_email,
    normalize_phone,
    parse_address,
    slugify,
)

_FULL_NAME_KEYS = ("name", "fullname", "full_name", "displayname", "display_name",
                   "agentname", "agent_name")
_FIRST_KEYS = ("firstname", "first_name", "givenname", "given_name")
_NAME_KEYS = _FULL_NAME_KEYS + _FIRST_KEYS
_LAST_KEYS = ("lastname", "last_name", "surname", "familyname", "family_name")
_PHONE_KEYS = ("phone", "phonenumber", "phone_number", "telephone", "tel", "mobile",
               "mobilephone", "directphone", "direct_phone", "officephone", "phone_number_1",
               "primaryphone", "contactphone")
_EMAIL_KEYS = ("email", "emailaddress", "email_address", "contactemail", "contact_email",
               "primaryemail", "workemail")
_STREET_KEYS = ("streetaddress", "street_address", "address1", "addressline1", "address",
                "street", "officeaddress", "line1")
_CITY_KEYS = ("city", "town", "locality", "addresslocality", "officecity")
_STATE_KEYS = ("state", "region", "province", "addressregion", "officestate", "statecode")
_POSTAL_KEYS = ("postalcode", "postal_code", "zip", "zipcode", "zip_code", "postcode")
_COUNTRY_KEYS = ("country", "countryname", "addresscountry", "country_code")
_ORG_KEYS = ("brokerage", "brokeragename", "officename", "office_name", "company",
             "companyname", "organization", "organisation", "firmname", "firm",
             "worksfor", "affiliation")
_URL_KEYS = ("profileurl", "profile_url", "detailurl", "detail_url", "permalink", "url", "link", "href")
_TITLE_KEYS = ("title", "jobtitle", "job_title", "position", "role", "headline")
_EXTRA_KEYS = {
    "license": ("license", "licensenumber", "license_number", "license_no"),
    "mls_id": ("mls", "mlsid", "mls_id", "mlsnumber"),
    "languages": ("languages", "language"),
    "specialties": ("specialties", "specialty", "specialisations", "specializations"),
    "title": _TITLE_KEYS,
}

_ASSIGNMENT = re.compile(
    r"window\.(__[A-Z_]+__|[A-Za-z_$][\w$]*)\s*=\s*(\{.*?\}|\[.*?\])\s*;?\s*(?:</script>|$)",
    re.S,
)
_NEXT_FLIGHT = re.compile(
    r"self\.__next_f\.push\(\[\s*1\s*,\s*(\"(?:[^\"\\]|\\.)*\")\s*\]\)",
    re.S,
)


def _iter_scripts(html: str):
    soup = parse_html(html)
    for tag in soup.find_all("script"):
        script_type = (tag.get("type") or "").lower()
        script_id = tag.get("id") or ""
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue

        if script_type in ("application/json", "application/ld+json") or script_id in (
            "__NEXT_DATA__", "__NUXT_DATA__", "__APOLLO_STATE__"
        ):
            try:
                yield (script_id or script_type or "json-script"), json.loads(raw)
            except Exception:
                continue
            continue

        if script_type in ("", "text/javascript", "module"):
            # Next.js App Router streams records inside self.__next_f.push(...)
            if "__next_f" in raw:
                flight = _parse_flight(raw)
                if flight:
                    yield ("next-flight", flight)
                    continue
            # A script that is simply a JSON document.
            if raw.startswith(("{", "[")) and len(raw) > 40:
                try:
                    yield ("inline-json", json.loads(raw))
                    continue
                except Exception:
                    pass
            for match in _ASSIGNMENT.finditer(raw):
                name, payload = match.group(1), match.group(2)
                if len(payload) < 40:
                    continue
                try:
                    yield (name, json.loads(payload))
                except Exception:
                    continue


def _parse_flight(raw: str) -> list[Any]:
    """Decode JSON objects embedded in Next.js flight chunks.

    The chunks are not a single JSON document: they are concatenated fragments.
    We unescape the string literals, then scan for balanced JSON objects.
    """
    pieces: list[str] = []
    for match in _NEXT_FLIGHT.finditer(raw):
        try:
            pieces.append(json.loads(match.group(1)))
        except Exception:
            continue
    if not pieces:
        return []
    text = "".join(pieces)

    documents: list[Any] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidate = text[start:index + 1]
                    if len(candidate) > 40:
                        try:
                            documents.append(json.loads(candidate))
                        except Exception:
                            pass
                    start = -1
        if len(documents) >= 60:
            break
    return documents


def _normalise_key(key: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(key).lower())


def _scalar(value: Any) -> str:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("number", "value", "email", "address", "name", "text", "label", "url"):
            if key in value:
                text = _scalar(value[key])
                if text:
                    return text
        return ""
    if isinstance(value, list):
        for item in value:
            text = _scalar(item)
            if text:
                return text
    return ""


def _lookup(item: dict, keys: tuple[str, ...]) -> str:
    lowered = {_normalise_key(k): v for k, v in item.items()}
    for key in keys:
        value = lowered.get(key)
        text = _scalar(value)
        if text:
            return text
    return ""


def _unwrapped(item: dict) -> dict:
    """Turn a ListItem wrapper into the Person/Organization it contains."""
    inner = item.get("item") if isinstance(item.get("item"), dict) else None
    if inner and (_has_identity_fields(inner) or inner.get("name")):
        merged = dict(item)
        merged.update(inner)
        return merged
    if isinstance(item.get("attributes"), dict):
        merged = dict(item)
        merged.update(item["attributes"])
        return merged
    return item


def _record_lists(node: Any, path: str = "", depth: int = 0):
    """Yield (path, list-of-dicts) for arrays that look like records."""
    if depth > 10:
        return
    if isinstance(node, list):
        dicts = [_unwrapped(item) for item in node if isinstance(item, dict)]
        if len(dicts) >= 3 and len(dicts) >= len(node) * 0.7:
            keysets = [set(_normalise_key(k) for k in item) for item in dicts[:6]]
            shared = set.intersection(*keysets) if keysets else set()
            if len(shared) >= 2 and any(_has_identity_fields(item) for item in dicts):
                yield (path or "/", dicts)
        for index, item in enumerate(node[:80]):
            yield from _record_lists(item, f"{path}[{index}]", depth + 1)
    elif isinstance(node, dict):
        for key, value in list(node.items())[:100]:
            yield from _record_lists(value, f"{path}.{key}" if path else str(key), depth + 1)


def _has_identity_fields(item: dict) -> bool:
    """Guard against menus, products and blog lists.

    A JSON array of `{name, url}` objects is far more often navigation than people.
    We accept a record only when it carries a contact detail, or a name plus an
    address/company/location signal. Profile URLs alone are handled by the DOM and
    JSON-LD link layers instead.
    """
    keys = {_normalise_key(k) for k in item}
    has_name = any(k in keys for k in _NAME_KEYS + _LAST_KEYS)
    has_contact = any(k in keys for k in _PHONE_KEYS + _EMAIL_KEYS)
    has_detail = any(k in keys for k in _STREET_KEYS + _CITY_KEYS + _ORG_KEYS)
    if has_name and not has_detail:
        # A payload that only has names and profile links is still a roster,
        # provided the link is shaped like a profile rather than a menu item.
        url = _lookup(item, _URL_KEYS)
        if url and re.search(r"/(agents?|team|people|person|profiles?|staff|realtors?|brokers?)/", url, re.I):
            has_detail = True
    return has_contact or (has_name and has_detail)


def _name_from(item: dict) -> str:
    direct = _lookup(item, _FULL_NAME_KEYS)
    if direct:
        return direct
    for key in ("name", "fullName", "displayName"):
        value = item.get(key)
        if isinstance(value, dict):
            first = _scalar(value.get("givenName") or value.get("first") or value.get("firstName"))
            last = _scalar(value.get("familyName") or value.get("last") or value.get("lastName"))
            if first or last:
                return " ".join(part for part in (first, last) if part)
    first = _lookup(item, _FIRST_KEYS)
    last = _lookup(item, _LAST_KEYS)
    return " ".join(part for part in (first, last) if part).strip()


def _address_from(item: dict) -> dict[str, str]:
    # Flat fields are common in API payloads: address1/city/state/zip at the top
    # level rather than a nested PostalAddress object.
    flat = {
        "street_address": _lookup(item, _STREET_KEYS),
        "city": _lookup(item, _CITY_KEYS),
        "state": _lookup(item, _STATE_KEYS),
        "postal_code": _lookup(item, _POSTAL_KEYS),
        "country": _lookup(item, _COUNTRY_KEYS),
    }
    state = flat["state"]
    flat["state"] = state.upper() if len(state) == 2 else state

    value = item.get("address") or item.get("officeAddress") or item.get("location")
    if isinstance(value, dict) and isinstance(value.get("address"), dict):
        value = value["address"]
    nested: dict[str, str] = {}
    if isinstance(value, str):
        nested = {k: v for k, v in parse_address(value).items() if v}
    elif isinstance(value, dict):
        street = _scalar(value.get("streetAddress") or value.get("address1") or value.get("line1") or "")
        city = _scalar(value.get("addressLocality") or value.get("city") or "")
        state_value = _scalar(value.get("addressRegion") or value.get("state") or "")
        postal = _scalar(value.get("postalCode") or value.get("zip") or "")
        country = _scalar(value.get("addressCountry") or value.get("country") or "")
        nested = {
            "street_address": street,
            "city": city,
            "state": state_value.upper() if len(state_value) == 2 else state_value,
            "postal_code": postal,
            "country": country,
        }
    result = {key: val for key, val in nested.items() if val}
    for key, val in flat.items():
        if val:
            result.setdefault(key, val)
    return result


def embedded_blobs(html: str) -> dict[str, Any]:
    blobs: dict[str, Any] = {}
    for name, payload in _iter_scripts(html):
        blobs.setdefault(name, payload)
    return blobs


def blob_keys(html: str) -> list[str]:
    return list(embedded_blobs(html).keys())


def extract_from_embedded(html: str, base_url: str = "") -> tuple[list[RawRecord], str]:
    """Return records found in embedded JSON and the name of the payload used."""
    best_records: list[RawRecord] = []
    best_name = ""

    for name, payload in _iter_scripts(html):
        documents = payload if isinstance(payload, list) else [payload]
        best: list[dict] = []
        for document in documents:
            for _path, items in _record_lists(document):
                good = [item for item in items if _has_identity_fields(item)]
                if len(good) > len(best):
                    best = good
        # A plain list of record dicts is itself a candidate.
        if isinstance(payload, list):
            good = [item for item in payload if isinstance(item, dict) and _has_identity_fields(_unwrapped(item))]
            if len(good) > len(best):
                best = [_unwrapped(item) for item in good]
        if len(best) < 3:
            continue

        records: list[RawRecord] = []
        seen: set[str] = set()
        for item in best[:5000]:
            item = _unwrapped(item)
            fields: dict[str, str] = {}
            name_text = _name_from(item)
            if name_text:
                fields["agent_name"] = clean_text(name_text)
            phone = _lookup(item, _PHONE_KEYS)
            if phone:
                display, _digits = normalize_phone(phone)
                if display:
                    fields["phone"] = display
            email = _lookup(item, _EMAIL_KEYS)
            if email:
                normalized = normalize_email(email)
                if normalized:
                    fields["email"] = normalized
            address = _address_from(item)
            fields.update({key: value for key, value in address.items() if value})

            org = _lookup(item, _ORG_KEYS)
            if org:
                fields["brokerage_name"] = org

            for extra_key, keys in _EXTRA_KEYS.items():
                value = _lookup(item, keys)
                if value:
                    fields[slugify(extra_key)] = value

            url = _lookup(item, _URL_KEYS)
            if url and not url.lower().startswith("javascript"):
                absolute = urljoin(base_url, url) if base_url else url
                fields["profile_url"] = canonical_url(absolute)

            key = fields.get("profile_url") or "|".join(
                fields.get(k, "") for k in ("agent_name", "phone", "email")
            )
            if key and key in seen:
                continue
            seen.add(key)

            if fields.get("agent_name") or fields.get("phone") or fields.get("email") or fields.get("brokerage_name"):
                records.append(RawRecord(
                    fields=fields,
                    profile_url=fields.get("profile_url", ""),
                    source_url=base_url,
                    method=f"embedded-json ({name})",
                ))
        if len(records) > len(best_records):
            best_records = records
            best_name = name

    return best_records, best_name
