"""Embedded JSON layer.

Many JavaScript directories ship the records in the HTML as a JSON payload
(`__NEXT_DATA__`, `__INITIAL_STATE__`, `application/json` blocks) and render them
client-side. When that payload is present it is the cleanest possible source and
needs no DOM guessing.

This layer is deliberately conservative: it only accepts an array of objects that
share a shape and contain at least one recognisable field name.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..inspect_site import parse_html
from ..models import RawRecord
from ..normalize import clean_text, canonical_url

_NAME_KEYS = ("name", "fullname", "full_name", "displayname", "display_name", "agentname",
              "agent_name", "firstname", "first_name")
_LAST_KEYS = ("lastname", "last_name", "surname")
_PHONE_KEYS = ("phone", "phonenumber", "phone_number", "telephone", "tel", "mobile",
               "mobilephone", "directphone", "direct_phone", "officephone")
_EMAIL_KEYS = ("email", "emailaddress", "email_address", "contactemail", "contact_email")
_STREET_KEYS = ("streetaddress", "street_address", "address1", "addressline1", "address",
                "street", "officeaddress")
_CITY_KEYS = ("city", "town", "locality", "addresslocality", "officecity")
_STATE_KEYS = ("state", "region", "province", "addressregion", "officestate", "statecode")
_POSTAL_KEYS = ("postalcode", "postal_code", "zip", "zipcode", "zip_code")
_ORG_KEYS = ("brokerage", "brokeragename", "officename", "office_name", "company",
             "companyname", "organization", "organisation", "firmname", "firm")
_URL_KEYS = ("profileurl", "profile_url", "detailurl", "detail_url", "permalink", "url", "link", "href")

_ASSIGNMENT = re.compile(
    r"window\.(__[A-Z_]+__|[A-Za-z_$][\w$]*)\s*=\s*(\{.*?\}|\[.*?\])\s*;?\s*(?:</script>|$)",
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
            for match in _ASSIGNMENT.finditer(raw):
                name, payload = match.group(1), match.group(2)
                if len(payload) < 40:
                    continue
                try:
                    yield (name, json.loads(payload))
                except Exception:
                    continue


def _normalise_key(key: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(key).lower())


def _lookup(item: dict, keys: tuple[str, ...]) -> str:
    lowered = {_normalise_key(k): v for k, v in item.items()}
    for key in keys:
        value = lowered.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def _record_lists(node: Any, path: str = "", depth: int = 0):
    """Yield (path, list-of-dicts) for arrays that look like records."""
    if depth > 8:
        return
    if isinstance(node, list):
        dicts = [item for item in node if isinstance(item, dict)]
        if len(dicts) >= 3 and len(dicts) >= len(node) * 0.8:
            keysets = [set(_normalise_key(k) for k in item) for item in dicts[:5]]
            shared = set.intersection(*keysets) if keysets else set()
            if len(shared) >= 2:
                yield (path or "/", dicts)
        for index, item in enumerate(node[:50]):
            yield from _record_lists(item, f"{path}[{index}]", depth + 1)
    elif isinstance(node, dict):
        for key, value in list(node.items())[:80]:
            yield from _record_lists(value, f"{path}.{key}" if path else str(key), depth + 1)


def _has_identity_fields(item: dict) -> bool:
    keys = {_normalise_key(k) for k in item}
    has_name = any(k in keys for k in _NAME_KEYS + _LAST_KEYS)
    has_contact = any(k in keys for k in _PHONE_KEYS + _EMAIL_KEYS)
    return has_name or has_contact


def embedded_blobs(html: str) -> dict[str, Any]:
    blobs: dict[str, Any] = {}
    for name, payload in _iter_scripts(html):
        blobs.setdefault(name, payload)
    return blobs


def blob_keys(html: str) -> list[str]:
    return list(embedded_blobs(html).keys())


def extract_from_embedded(html: str, base_url: str = "") -> tuple[list[RawRecord], str]:
    """Return records found in embedded JSON and the name of the payload used."""
    for name, payload in _iter_scripts(html):
        best: list[dict] = []
        for _path, items in _record_lists(payload):
            good = [item for item in items if _has_identity_fields(item)]
            if len(good) > len(best):
                best = good
        if len(best) < 3:
            continue

        records: list[RawRecord] = []
        for item in best[:5000]:
            fields: dict[str, str] = {}
            first = _lookup(item, _NAME_KEYS)
            last = _lookup(item, _LAST_KEYS)
            name_text = " ".join(p for p in (first, last) if p).strip()
            if name_text:
                fields["agent_name"] = clean_text(name_text)
            phone = _lookup(item, _PHONE_KEYS)
            if phone:
                fields["phone"] = phone
            email = _lookup(item, _EMAIL_KEYS)
            if email:
                fields["email"] = email
            street = _lookup(item, _STREET_KEYS)
            if street:
                fields["street_address"] = street
            for field, keys in (("city", _CITY_KEYS), ("state", _STATE_KEYS),
                                ("postal_code", _POSTAL_KEYS), ("brokerage_name", _ORG_KEYS)):
                value = _lookup(item, keys)
                if value:
                    fields[field] = value
            url = _lookup(item, _URL_KEYS)
            if url and not url.lower().startswith("javascript"):
                if url.startswith("/"):
                    url = base_url.rstrip("/") + url
                fields["profile_url"] = canonical_url(url)

            if fields.get("agent_name") or fields.get("phone") or fields.get("email"):
                records.append(RawRecord(
                    fields=fields,
                    profile_url=fields.get("profile_url", ""),
                    source_url=base_url,
                    method=f"embedded-json ({name})",
                ))
        if records:
            return records, name
    return [], ""
