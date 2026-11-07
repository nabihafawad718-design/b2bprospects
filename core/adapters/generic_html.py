"""Generic HTML adapter.

Works on directory sites that render their roster server-side but are not kind
enough to publish schema.org markup. It is deliberately conservative: a field is
only filled when a specific signal is found, and the record is flagged when the
name had to be guessed.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import bs4

from ..inspect_site import discover_profile_links, parse_html
from ..models import RawRecord
from ..net import FetchedPage
from ..normalize import (
    canonical_url,
    clean_text,
    find_emails,
    find_phones,
    host_of,
    normalize_name,
    normalize_org,
    normalize_phone,
    normalize_website,
    parse_address,
    tel_from_fragment,
)
from ..inspect_site import _detect_pagination
from .base import AdapterContext, DirectoryAdapter

_CONTACT_CONTAINERS = re.compile(
    r"(contact|profile|agent|agent-info|bio|details|card|vcard|team-member|person)", re.I
)
_ADDRESS_TAG = re.compile(r"(address|addr|location|office)", re.I)


def _strip_noise(soup: bs4.BeautifulSoup) -> bs4.BeautifulSoup:
    """Remove content that produces false positives.

    Header, nav and footer are stripped deliberately: a brokerage's switchboard
    number and info@ address live there, and attributing them to every agent is
    the single most common way a directory export turns out to be wrong.
    """
    for tag in soup(["script", "style", "noscript", "template", "svg", "header", "nav", "footer"]):
        tag.decompose()
    return soup


def _scope_candidates(soup: bs4.BeautifulSoup) -> list[bs4.BeautifulSoup]:
    """Most specific containers first, whole page last."""
    scopes: list[bs4.BeautifulSoup] = []
    for selector in ("main", "article"):
        for el in soup.find_all(selector):
            scopes.append(el)
    for el in soup.find_all(True, class_=_CONTACT_CONTAINERS):
        scopes.append(el)
    if soup.body:
        scopes.append(soup.body)
    scopes.append(soup)
    return scopes


def _first_tel_anchor(scope: bs4.BeautifulSoup) -> str:
    for anchor in scope.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if href.lower().startswith("tel:"):
            display, _digits = normalize_phone(href[4:])
            if display:
                return display
    return ""


def _first_mailto(scope: bs4.BeautifulSoup) -> str:
    for anchor in scope.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if href.lower().startswith("mailto:"):
            emails = find_emails(href[7:], limit=1)
            if emails:
                return emails[0]
    return ""


def _heading_name(soup: bs4.BeautifulSoup) -> str:
    for el in soup.find_all(attrs={"itemprop": "name"}):
        name = normalize_name(el.get_text(" ") or el.get("content") or "")
        if 2 <= len(name.split()) <= 6:
            return name
    for tag in ("h1",):
        for el in soup.find_all(tag):
            name = normalize_name(el.get_text(" "))
            if 2 <= len(name.split()) <= 6:
                return name
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        candidate = re.split(r"[|\-–—:]", clean_text(og_title["content"]))[0]
        name = normalize_name(candidate)
        if 2 <= len(name.split()) <= 6:
            return name
    if soup.title:
        candidate = re.split(r"[|\-–—:]", clean_text(soup.title.get_text()))[0]
        return normalize_name(candidate)
    return ""


def heuristic_profile(soup: bs4.BeautifulSoup, page_url: str) -> RawRecord:
    soup = _strip_noise(soup)
    fields: dict[str, str] = {}
    method_notes: list[str] = []

    scopes = _scope_candidates(soup)

    name = _heading_name(soup)
    if not name:
        for attribute in ("data-name", "data-full-name", "data-agent-name"):
            node = soup.find(attrs={attribute: True})
            if node:
                candidate = normalize_name(node.get(attribute))
                if candidate:
                    name = candidate
                    break
    if name:
        fields["agent_name"] = name

    phone = ""
    email = ""
    address_text = ""
    for attribute in ("data-phone", "data-telephone", "data-tel", "data-mobile"):
        node = soup.find(attrs={attribute: True})
        if node:
            display, _digits = normalize_phone(node.get(attribute))
            if display:
                phone = display
                break
    for attribute in ("data-email",):
        node = soup.find(attrs={attribute: True})
        if node:
            found = find_emails(str(node.get(attribute)), limit=1)
            if found:
                email = found[0]
                break
    for attribute in ("data-address", "data-street"):
        node = soup.find(attrs={attribute: True})
        if node:
            address_text = clean_text(node.get(attribute))
            break
    for scope in scopes:
        if not phone:
            phone = _first_tel_anchor(scope)
        if not phone:
            phone_el = scope.find(attrs={"itemprop": "telephone"})
            if phone_el:
                phone = tel_from_fragment(phone_el.get("href") or phone_el.get_text(" ") or phone_el.get("content"))
        if not email:
            email = _first_mailto(scope)
        if not email:
            email_el = scope.find(attrs={"itemprop": "email"})
            if email_el:
                found = find_emails(email_el.get("href", "").replace("mailto:", "") or email_el.get_text(" "), limit=1)
                email = found[0] if found else ""
        if not address_text:
            address_el = scope.find("address") or scope.find(attrs={"itemprop": "address"})
            if address_el:
                address_text = clean_text(address_el.get_text(" "))
        if phone and email and address_text:
            break

    # Regex fallback, scoped to the most specific container that yields a hit.
    if not phone:
        for scope in scopes[:4]:
            phones = find_phones(clean_text(scope.get_text(" ")), limit=1)
            if phones:
                phone = phones[0]
                method_notes.append("phone from page text")
                break
    if not email:
        for scope in scopes[:4]:
            emails = find_emails(scope.get_text(" "), limit=1)
            if emails:
                email = emails[0]
                method_notes.append("email from page text")
                break
    if not address_text:
        for scope in scopes[:3]:
            text = clean_text(scope.get_text(" "))
            parsed = parse_address(text)
            if parsed["city"] and parsed["state"]:
                address_text = text
                break

    if phone:
        fields["phone"] = phone
    if email:
        fields["email"] = email
    if address_text:
        parsed = parse_address(address_text)
        fields.update({k: v for k, v in parsed.items() if v})

    # schema.org microdata and ordinary labelled links.
    url_el = soup.find(attrs={"itemprop": "url"})
    if url_el:
        website = normalize_website(url_el.get("href") or url_el.get_text(" "))
        if website:
            fields["website"] = website
    title_el = soup.find(attrs={"itemprop": "jobTitle"})
    if title_el:
        title = clean_text(title_el.get_text(" ") or title_el.get("content") or "")
        if title:
            fields["title"] = title[:120]

    # Brokerage: explicit markup, then og:site_name, then page title tail.
    org = ""
    for scope in scopes[:3]:
        el = scope.find(attrs={"itemprop": "worksFor"}) or scope.find(attrs={"itemprop": "affiliation"})
        if el:
            org = clean_text(el.get_text(" "))
            break
    if not org:
        for el in soup.find_all(True, class_=re.compile(r"(brokerage|company|office-name|agency)", re.I)):
            text = clean_text(el.get_text(" "))
            if 2 <= len(text) <= 80:
                org = text
                break
    if not org:
        og_site = soup.find("meta", attrs={"property": "og:site_name"})
        if og_site and og_site.get("content"):
            org = clean_text(og_site["content"])
    if org:
        fields["brokerage_name"] = normalize_org(org)

    warnings: list[str] = []
    if not fields.get("agent_name"):
        warnings.append("no name found on profile page")

    return RawRecord(
        fields=fields,
        profile_url=canonical_url(page_url),
        source_url=canonical_url(page_url),
        method="heuristic" + (f" ({'; '.join(method_notes)})" if method_notes else ""),
        warnings=warnings,
    )


def next_page_urls_from_soup(soup: bs4.BeautifulSoup, base_url: str,
                             max_list_pages: int = 20) -> list[str]:
    """Shared pagination discovery.

    Order matters: an explicit rel=next beats numbered links, which beat URLs we
    construct ourselves from a page parameter.
    """
    urls: list[str] = []

    for link in soup.find_all("a", href=True):
        rel = link.get("rel") or []
        if isinstance(rel, str):
            rel = [rel]
        if any(str(r).lower() == "next" for r in rel):
            urls.append(urljoin(base_url, link["href"]))
            break
        text = clean_text(link.get_text()).lower()
        if text in ("next", "next page", "next ›", "next »", "›", "»"):
            urls.append(urljoin(base_url, link["href"]))
            break

    # Path pagination (/agents/page/2) and attribute-only next controls.
    if not urls:
        try:
            from ..browser.pagination import detect_pagination

            row_hint = "a" if len(soup.find_all("a", href=True)) >= 3 else ""
            plan = detect_pagination(str(soup), base_url, row_hint)
            if plan.strategy == "path_pages":
                urls.extend(plan.page_urls)
            elif plan.strategy == "next_button" and plan.selector:
                node = soup.select_one(plan.selector)
                if node is not None and node.get("href"):
                    urls.append(urljoin(base_url, str(node["href"])))
        except Exception:
            pass

    # Numbered pagination must look like a pager, not a stray year or price in a
    # footer. Require two or more numbered links that share a path and stay small.
    by_path: dict[str, list[tuple[int, str]]] = {}
    for link in soup.find_all("a", href=True):
        text = clean_text(link.get_text())
        if not re.fullmatch(r"\d{1,4}", text):
            continue
        number = int(text)
        if not (1 <= number <= 500):
            continue
        target = urljoin(base_url, link["href"])
        by_path.setdefault(urlparse(target).path, []).append((number, target))

    pager = max(by_path.values(), key=len, default=[])
    if len(pager) >= 2:
        for number, href in sorted(pager):
            if href not in urls and number > 1:
                urls.append(href)

    if not urls:
        kind, _pages, param = _detect_pagination(soup, base_url)
        if kind == "query_param" and param:
            limit = min(max_list_pages, 50)
            parsed = urlparse(base_url)
            base_values = dict(parse_qsl(parsed.query, keep_blank_values=True))
            raw_current = base_values.get(param, "0" if param in ("start", "offset") else "1")
            current = (int(raw_current) if str(raw_current).isdigit()
                       else (0 if param in ("start", "offset") else 1))
            values: set[int] = set()
            if str(base_values.get(param, "")).isdigit():
                values.add(int(base_values[param]))
            for link in soup.find_all("a", href=True):
                for key, value in parse_qsl(urlparse(urljoin(base_url, link["href"])).query):
                    if key.lower() == param.lower() and value.isdigit():
                        values.add(int(value))
            step = 1
            if param in ("start", "offset"):
                ordered = sorted(v for v in values if v >= 0)
                gaps = [b - a for a, b in zip(ordered, ordered[1:]) if b > a]
                if gaps:
                    step = min(gaps)
            for number in range(current + step, current + step * limit, step):
                query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                         if k.lower() != param.lower()]
                query.append((param, str(number)))
                urls.append(urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", urlencode(query), "")))

    here = canonical_url(base_url)
    return [u for u in dict.fromkeys(urls) if canonical_url(u) != here]


class GenericHtmlAdapter(DirectoryAdapter):
    id = "generic_html"
    label = "Generic HTML directory"
    version = "1.0"
    expected_fields = ("agent_name", "phone", "email", "profile_url")
    description = (
        "Heuristic extraction for ordinary server-rendered directories. Detects repeated "
        "profile blocks and pagination, then parses each profile page."
    )

    def matches(self, url: str, html: str = "") -> bool:
        return False  # never auto-claims a domain; used as an explicit fallback

    def profile_links(self, page: FetchedPage, ctx: AdapterContext) -> list[str]:
        soup = parse_html(page.text)
        links, _sig, _count, _selector = discover_profile_links(soup, page.final_url, host_of(page.final_url))
        return links

    def next_page_urls(self, page: FetchedPage, ctx: AdapterContext) -> list[str]:
        return next_page_urls_from_soup(parse_html(page.text), page.final_url, ctx.max_list_pages)

    def listing_records(self, page: FetchedPage, ctx: AdapterContext) -> list[RawRecord]:
        """Use rich listing cards directly when they already carry contact details.

        This is the same detector the browser uses, applied to the server-rendered
        HTML. It avoids fetching hundreds of profile pages when the roster already
        publishes the fields the operator wants.
        """
        from ..browser.extract import extract_page, suggest_mapping
        from ..browser.structure import analyze_html

        models = analyze_html(page.text, page.final_url)
        if not models:
            return []
        structure = models[0]
        mapping = suggest_mapping(structure)
        if "agent_name" not in mapping.columns:
            return []
        rows = extract_page(page.text, page.final_url, structure, mapping)
        records: list[RawRecord] = []
        for row in rows:
            fields = {key: value for key, value in row.items() if not key.startswith("_")}
            if not fields.get("agent_name"):
                continue
            records.append(RawRecord(
                fields=fields,
                profile_url=fields.get("profile_url", ""),
                source_url=canonical_url(page.final_url),
                method="generic-listing",
            ))
        # A blog index also repeats title-shaped cards. Only treat the page as a
        # record source when the cards carry a business/contact signal; otherwise
        # fall back to profile pages, which the roster path handles correctly.
        meaningful = [
            record for record in records
            if any((record.fields or {}).get(key) for key in
                   ("phone", "email", "brokerage_name", "street_address", "city", "website"))
        ]
        return meaningful if len(meaningful) >= 2 else []

    def parse_profile(self, page: FetchedPage, ctx: AdapterContext) -> RawRecord | None:
        # Structured data is higher fidelity than heuristics, even when the listing
        # did not contain enough JSON-LD to select the JSON-LD adapter.
        try:
            from .generic_jsonld import GenericJsonLdAdapter

            structured = GenericJsonLdAdapter().parse_profile(page, ctx)
            if structured is not None and len([
                key for key, value in (structured.fields or {}).items()
                if value and key != "agent_name"
            ]) >= 2:
                return structured
        except Exception:
            pass
        return heuristic_profile(parse_html(page.text), page.final_url)
