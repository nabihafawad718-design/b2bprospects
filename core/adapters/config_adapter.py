"""Selector-driven adapters loaded from JSON.

This is the maintenance path that does not require a Python file. Drop a JSON
document into `core/adapters/sites/`, restart the app, and the domain is served by
a dedicated adapter. The engine, validation, quality scoring and export are shared
with every other adapter.

Example
-------
{
  "id": "acme",
  "label": "Acme Realty",
  "domains": ["acme.example"],
  "listing": {
    "row": "div.agent-card",
    "profile_link": "a.agent-card__link",
    "next": "a.pagination__next",
    "page_param": "page"
  },
  "profile": {
    "fields": {
      "agent_name": "h1.agent-name",
      "brokerage_name": ".office-name",
      "phone": "a[href^='tel:']",
      "email": "a[href^='mailto:']",
      "street_address": "address",
      "website": ".website a@href",
      "title": ".job-title"
    },
    "regex": {"phone": "\\\\(?\\\\d{3}\\\\)\\\\s*\\\\d{3}-\\\\d{4}"}
  }
}

Selector syntax:
  `css`            text content
  `css@href`       attribute value (any attribute)
  `a || b`         first selector that yields a value
  `fields` can map to any name; names outside the catalogue become extra columns.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import bs4

from ..inspect_site import discover_profile_links, parse_html
from ..models import RawRecord
from ..net import FetchedPage
from ..normalize import (
    canonical_url,
    clean_text,
    email_from_fragment,
    find_emails,
    find_phones,
    host_of,
    normalize_phone,
    parse_address,
    tel_from_fragment,
)
from ..settings import ADAPTER_CONFIG_DIR
from .base import AdapterContext, DirectoryAdapter
from .generic_html import heuristic_profile, next_page_urls_from_soup


class ConfigError(ValueError):
    pass


def _split_selector(spec: str) -> list[str]:
    return [part.strip() for part in str(spec or "").split("||") if part.strip()]


def _selector_and_attr(spec: str) -> tuple[str, str]:
    text = str(spec or "").strip()
    if "@" in text:
        selector, _, attr = text.rpartition("@")
        return selector.strip(), attr.strip()
    return text, ""


def _extract(soup: bs4.BeautifulSoup, spec: str, field: str = "") -> str:
    for alternative in _split_selector(spec):
        selector, attr = _selector_and_attr(alternative)
        try:
            nodes = soup.select(selector)
        except Exception:
            nodes = []
        for node in nodes:
            value = ""
            if attr:
                value = node.get(attr) or ""
                if attr == "content":
                    value = node.get("content") or ""
            if not value:
                value = node.get_text(" ", strip=True)
            if not value:
                href = node.get("href") or ""
                if field == "phone":
                    value = tel_from_fragment(href)
                elif field == "email":
                    value = email_from_fragment(href)
            value = clean_text(value)
            if not value:
                continue
            if field == "phone":
                display, _digits = normalize_phone(value)
                if display:
                    return display
                continue
            if field == "email":
                found = find_emails(value, limit=1)
                if found:
                    return found[0]
                continue
            return value
    return ""


class ConfigSiteAdapter(DirectoryAdapter):
    """A site-specific adapter described entirely by JSON."""

    def __init__(self, config: dict[str, Any], path: str = "") -> None:
        self.config = config
        self.path = path
        self.id = str(config.get("id") or "config_site")
        self.label = str(config.get("label") or self.id)
        self.version = str(config.get("version") or "1.0")
        self.domains = tuple(str(d).lower() for d in (config.get("domains") or []) if str(d).strip())
        self.description = str(config.get("description") or "Selector-driven site adapter.")
        self.listing = config.get("listing") or {}
        self.profile = config.get("profile") or {}
        self.expected_fields = tuple(self.profile.get("expected_fields") or
                                     ("agent_name", "phone", "email", "profile_url"))

    # -- listing -----------------------------------------------------------
    def profile_links(self, page: FetchedPage, ctx: AdapterContext) -> list[str]:
        soup = parse_html(page.text)
        row_selector = str(self.listing.get("row") or "")
        link_selector = str(self.listing.get("profile_link") or "")
        links: list[str] = []

        if row_selector:
            try:
                rows = soup.select(row_selector)
            except Exception:
                rows = []
            for row in rows:
                target = ""
                if link_selector:
                    try:
                        anchor = row.select_one(link_selector)
                    except Exception:
                        anchor = None
                    if anchor and anchor.get("href"):
                        target = str(anchor["href"])
                if not target:
                    anchor = row.find("a", href=True)
                    if anchor:
                        target = str(anchor["href"])
                if target:
                    links.append(canonical_url(urljoin(page.final_url, target)))

        if not links:
            links, _sig, _count, _selector = discover_profile_links(
                soup, page.final_url, host_of(page.final_url)
            )
            if row_selector:
                ctx.note(
                    f"{self.label}: row selector {row_selector!r} matched no profile links; "
                    "used generic discovery instead."
                )
        return [canonical_url(url) for url in dict.fromkeys(links) if url]

    def next_page_urls(self, page: FetchedPage, ctx: AdapterContext) -> list[str]:
        soup = parse_html(page.text)
        next_selector = str(self.listing.get("next") or "")
        if next_selector:
            try:
                anchor = soup.select_one(next_selector)
            except Exception:
                anchor = None
            if anchor and anchor.get("href"):
                target = canonical_url(urljoin(page.final_url, str(anchor["href"])))
                if target and target != canonical_url(page.final_url):
                    return [target]
            # A configured next control that is absent means we are on the last
            # page. Do not invent further page URLs in that case.
            return []

        param = str(self.listing.get("page_param") or "")
        if param:
            limit = min(int(self.listing.get("max_pages") or ctx.max_list_pages), 100)
            parts = urlparse(page.final_url)
            query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() != param.lower()]
            current = 1
            for key, value in parse_qsl(parts.query, keep_blank_values=True):
                if key.lower() == param.lower() and value.isdigit():
                    current = int(value)
            urls = []
            for number in range(current + 1, current + limit):
                urls.append(urlunparse((parts.scheme, parts.netloc, parts.path, "", urlencode(query + [(param, str(number))]), "")))
            return urls

        return next_page_urls_from_soup(soup, page.final_url, ctx.max_list_pages)

    # -- listing records ---------------------------------------------------
    def listing_records(self, page: FetchedPage, ctx: AdapterContext) -> list[RawRecord]:
        spec = self.listing.get("record_fields") or {}
        row_selector = str(self.listing.get("row") or "")
        if not spec or not row_selector:
            return []
        soup = parse_html(page.text)
        try:
            rows = soup.select(row_selector)
        except Exception:
            return []

        records: list[RawRecord] = []
        for row in rows:
            fields: dict[str, str] = {}
            for field, selector in spec.items():
                value = _extract(row, str(selector), field)
                if value:
                    fields[field] = value
            if not any(fields.get(key) for key in ("agent_name", "phone", "email", "brokerage_name")):
                continue
            profile_url = ""
            link_selector = str(self.listing.get("profile_link") or "")
            if link_selector:
                try:
                    anchor = row.select_one(link_selector)
                except Exception:
                    anchor = None
                if anchor and anchor.get("href"):
                    profile_url = canonical_url(urljoin(page.final_url, str(anchor["href"])))
            if not profile_url and fields.get("profile_url"):
                profile_url = canonical_url(urljoin(page.final_url, fields["profile_url"]))
            if fields.get("street_address") and not fields.get("city"):
                parsed = parse_address(fields["street_address"])
                if parsed.get("city"):
                    fields.update({key: value for key, value in parsed.items() if value})
            records.append(RawRecord(
                fields=fields,
                profile_url=profile_url,
                source_url=canonical_url(page.final_url),
                method="config-selectors (listing)",
            ))
        return records

    # -- profile -----------------------------------------------------------
    def parse_profile(self, page: FetchedPage, ctx: AdapterContext) -> RawRecord | None:
        soup = parse_html(page.text)
        fields: dict[str, str] = {}
        selectors: dict[str, Any] = self.profile.get("fields") or {}

        for field, spec in selectors.items():
            if isinstance(spec, list):
                value = ""
                for alternative in spec:
                    value = _extract(soup, str(alternative), field)
                    if value:
                        break
            else:
                value = _extract(soup, str(spec), field)
            if value:
                fields[field] = value

        regexes: dict[str, str] = self.profile.get("regex") or {}
        page_text = clean_text(soup.get_text(" "))
        for field, pattern in regexes.items():
            if fields.get(field):
                continue
            try:
                match = re.search(pattern, page_text, re.I)
            except re.error:
                continue
            if match:
                fields[field] = clean_text(match.group(1) if match.groups() else match.group(0))

        # A selector that returns a whole address should still be split.
        if fields.get("street_address") and not fields.get("city") and "," in fields["street_address"]:
            parsed = parse_address(fields["street_address"])
            if parsed.get("city"):
                fields.update({key: value for key, value in parsed.items() if value})

        if not fields.get("agent_name"):
            fallback = heuristic_profile(soup, page.final_url)
            for key, value in (fallback.fields or {}).items():
                fields.setdefault(key, value)

        warnings: list[str] = []
        if not fields.get("agent_name"):
            warnings.append("no name matched the configured selectors")

        return RawRecord(
            fields=fields,
            profile_url=canonical_url(page.final_url),
            source_url=canonical_url(page.final_url),
            method="config-selectors",
            warnings=warnings,
        )


def load_config_adapters(directory: Path | str = ADAPTER_CONFIG_DIR) -> tuple[list[DirectoryAdapter], list[str]]:
    adapters: list[DirectoryAdapter] = []
    errors: list[str] = []
    folder = Path(directory)
    if not folder.exists():
        return adapters, errors

    for path in sorted(folder.glob("*.json")):
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path.name}: could not read JSON ({exc})")
            continue
        if not isinstance(config, dict):
            errors.append(f"{path.name}: top level must be an object")
            continue
        if not config.get("domains"):
            continue  # template / disabled
        if not config.get("id"):
            errors.append(f"{path.name}: missing 'id'")
            continue
        try:
            adapters.append(ConfigSiteAdapter(config, str(path)))
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    return adapters, errors


def adapter_config_errors(directory: Path | str = ADAPTER_CONFIG_DIR) -> list[str]:
    return load_config_adapters(directory)[1]
