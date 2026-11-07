"""Website inspection.

Answers the question the UI asks in Step 2: what is on this page, what can be
extracted, and what will need a dedicated adapter or a browser?

Nothing here extracts records. It only measures structure, so a user can make an
informed decision before committing to a run.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from urllib.parse import urljoin, urlparse

import bs4

from .models import InspectionReport, signature
from .net import FetchedPage, FetchError, PoliteFetcher
from .normalize import canonical_url, clean_text, find_emails, find_phones, host_of, same_site

PROFILE_KEYWORDS = (
    "agent", "agents", "agent-profile", "team", "our-team", "people", "person", "profile",
    "profiles", "staff", "realtor", "realtors", "associate", "associates", "broker", "brokers",
    "directory", "members", "specialist", "advisor", "advisors", "attorney", "lawyer",
)

CONTACT_KEYWORDS = ("contact", "tel:", "mailto:", "phone", "email")

_JSONLD_TYPES_OF_INTEREST = {
    "realestateagent", "person", "organization", "localbusiness", "realestatelisting",
    "itemlist", "professionalService",
}

_ADDRESS_HINT = re.compile(r"\b\d{1,6}\s+[A-Za-z0-9.'\- ]{2,40}\b(?:st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|lane|way|ct|court|pl|place|pkwy|parkway|hwy|highway)\b", re.I)


def parse_html(text: str) -> bs4.BeautifulSoup:
    for parser in ("lxml", "html.parser"):
        try:
            return bs4.BeautifulSoup(text, parser)
        except Exception:
            continue
    return bs4.BeautifulSoup(text, "html.parser")


def _iter_jsonld(soup: bs4.BeautifulSoup):
    for tag in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        raw = re.sub(r"^\s*<!--", "", raw)
        raw = re.sub(r"-->\s*$", "", raw)
        try:
            yield json.loads(raw)
        except Exception:
            # Tolerate trailing commas / stray control characters, common in the wild.
            try:
                yield json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
            except Exception:
                continue


def _flatten_jsonld(node, out: list[dict]) -> None:
    if isinstance(node, list):
        for item in node:
            _flatten_jsonld(item, out)
        return
    if not isinstance(node, dict):
        return
    out.append(node)
    graph = node.get("@graph")
    if isinstance(graph, (list, dict)):
        _flatten_jsonld(graph, out)


def _type_names(nodes: list[dict]) -> list[str]:
    found: Counter[str] = Counter()
    for node in nodes:
        raw = node.get("@type") or node.get("type") or ""
        if isinstance(raw, str):
            found[raw] += 1
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    found[item] += 1
    return [name for name, _count in found.most_common()]


def _jsonld_fields(nodes: list[dict]) -> list[str]:
    fields = set()
    for node in nodes:
        for key in node:
            fields.add(str(key).lstrip("@").lower())
        address = node.get("address")
        if isinstance(address, dict):
            fields.update({"street_address", "city", "state", "postal_code"})
    return sorted(fields)


# ---------------------------------------------------------------------------
# Link classification
# ---------------------------------------------------------------------------
# Sections that look like a list of people but never are.
_EXCLUDED_PREFIXES = {
    "blog", "news", "articles", "press", "events", "careers", "jobs", "faq",
    "support", "help", "legal", "privacy", "terms", "sitemap", "search",
}
# Root pages that contain the roster but are not themselves a profile.
_SECTION_ROOTS = {
    "agents", "agent", "team", "our-team", "people", "staff", "directory", "realtors",
    "brokers", "associates", "members", "advisors", "attorneys", "professionals",
    "contact", "about", "about-us", "home", "index",
}


def _looks_like_profile(href: str, base_host: str) -> bool:
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return False
    parsed = urlparse(href)
    if parsed.hostname and not same_site(parsed.hostname, base_host):
        return False
    path = parsed.path.lower()
    if not path or path in ("/", ""):
        return False
    segments = [s for s in path.split("/") if s]
    if not segments:
        return False
    if segments[0] in _EXCLUDED_PREFIXES:
        return False
    # /agents or /team is the roster itself, not one person in it.
    if len(segments) == 1 and segments[0] in _SECTION_ROOTS:
        return False
    joined = "/".join(segments)
    if any(keyword in joined for keyword in PROFILE_KEYWORDS):
        return True
    # /jane-smith style single-segment slugs are common on brokerage sites
    if len(segments) == 1 and re.fullmatch(r"[a-z0-9][a-z0-9\-]{3,60}", segments[0]):
        if segments[0] not in _SECTION_ROOTS:
            return True
    if len(segments) >= 2 and re.fullmatch(r"[a-z0-9][a-z0-9\-]{2,60}", segments[-1]):
        return True
    return False


def _container_candidates(soup: bs4.BeautifulSoup):
    """Yield repeated child groups: containers whose direct children share a shape."""
    results = []
    for parent in soup.find_all(["div", "ul", "ol", "section", "article", "table", "tbody", "main"]):
        children = parent.find_all(recursive=False)
        if len(children) < 2:
            continue
        groups: dict[tuple, list] = {}
        for child in children:
            classes = tuple(sorted(c for c in (child.get("class") or []) if isinstance(c, str)))
            key = (child.name, classes)
            groups.setdefault(key, []).append(child)
        for key, group in groups.items():
            # Two repeated rows is enough for a small brokerage; a single row is
            # indistinguishable from ordinary page furniture.
            if len(group) < 2:
                continue
            anchors = [a.get("href") for el in group for a in el.find_all("a", href=True)]
            if not anchors:
                continue
            results.append((parent, key, group, anchors))
    return results


def discover_profile_links(soup: bs4.BeautifulSoup, base_url: str, host: str
                           ) -> tuple[list[str], str, int, str]:
    """Shared by the inspector and the generic HTML adapter.

    Returns (canonical profile links, container signature, container count, selector).
    """
    best = None
    for parent, key, group, anchors in _container_candidates(soup):
        profile_links = [a for a in anchors if _looks_like_profile(urljoin(base_url, a), host)]
        # A lone profile-looking link in a two-item nav is not a roster.
        if len(profile_links) < 2:
            continue
        score = len(profile_links) * 3 + len(group)
        # Page furniture can repeat too; never let a nav/footer outscore cards.
        if parent.name in ("nav", "header", "footer") or parent.find_parent(["nav", "header", "footer"]):
            score -= 12
        if len(profile_links) >= 3:
            score += 4
        if best is None or score > best[0]:
            best = (score, parent, key, group, profile_links)
    if not best:
        # Cards with unique per-record classes defeat the class grouping above.
        # Reuse the browser structure detector, which has a tag-level fallback.
        try:
            from .browser.structure import analyze_html, select_rows

            html = str(soup)
            models = analyze_html(html, base_url)
            if models:
                model = models[0]
                rows = select_rows(html, model)
                links = []
                for row in rows:
                    for anchor in row.find_all("a", href=True):
                        candidate = urljoin(base_url, str(anchor["href"]))
                        if _looks_like_profile(candidate, host):
                            links.append(candidate)
                unique = list(dict.fromkeys(
                    canonical_url(url) for url in links if url
                ))
                if len(unique) >= 2:
                    return unique, model.signature, len(rows), model.row_selector
        except Exception:
            pass
        return [], "", 0, ""

    _score, parent, key, group, profile_links = best
    classes = ".".join(c for c in key[1] if c)
    selector = f"{key[0]}{'.' + classes if classes else ''}"
    # Deliberately excludes row/link counts: those change legitimately when a
    # brokerage hires. Only the markup shape is compared between runs.
    sig = signature(parent.name or "", *sorted(parent.get("class") or []), key[0], *key[1])
    unique_links = list(dict.fromkeys(
        canonical_url(urljoin(base_url, a)) for a in profile_links if urljoin(base_url, a)
    ))
    return unique_links, sig, len(group), selector


def _derive_pattern(urls: list[str]) -> str:
    if not urls:
        return ""
    shapes = []
    for url in urls[:40]:
        segments = [s for s in urlparse(url).path.split("/") if s]
        normalised = ["*" if re.fullmatch(r"[a-z0-9\-_.]+", s.lower()) and s.lower() not in
                      ("agents", "agent", "team", "people", "staff", "profile", "profiles") else s.lower()
                      for s in segments]
        shapes.append("/".join(normalised))
    if not shapes:
        return ""
    return Counter(shapes).most_common(1)[0][0][:120]


_PAGE_PARAMS = ("page", "p", "pg", "paged", "start", "offset", "page_num", "pagenumber", "pageindex")
_PATH_PAGE = re.compile(r"/(?:page|pages|p|pg)/\d{1,4}/?$", re.I)


def _detect_pagination(soup: bs4.BeautifulSoup, page_url: str) -> tuple[str, int, str]:
    for link in soup.find_all("a", href=True):
        rel = link.get("rel") or []
        if isinstance(rel, str):
            rel = [rel]
        if any(str(r).lower() == "next" for r in rel):
            return "next_link", 1, ""
        text = clean_text(link.get_text()).lower()
        aria = str(link.get("aria-label") or "").lower()
        if text in ("next", "next page", "›", "»", "next »", "››") or aria in ("next", "next page"):
            return "next_link", 1, ""

    # Path pagination: /agents/page/2, /team/p/3.
    for link in soup.find_all("a", href=True):
        target = urljoin(page_url, str(link["href"]))
        if _PATH_PAGE.search(urlparse(target).path):
            return "path_pages", 0, ""
    if _PATH_PAGE.search(urlparse(page_url).path):
        return "path_pages", 0, ""

    # Attribute-only next control (icon button in a React pager).
    for control in soup.find_all(["a", "button"]):
        blob = " ".join([
            " ".join(control.get("class") or []),
            str(control.get("id") or ""),
            str(control.get("data-testid") or ""),
            str(control.get("aria-label") or ""),
            str(control.get("title") or ""),
        ]).lower()
        if re.search(r"(next[-_ ]?(page|btn|link|button)?|pagination[-_ ]?next|pager[-_ ]?next)", blob):
            if str(control.get("aria-disabled") or "").lower() != "true" and not control.has_attr("disabled"):
                return "next_link", 1, ""

    # Numbered links only count when they sit together in one pager, so a footer
    # copyright year is never mistaken for a page number.
    by_parent: dict[int, set[int]] = {}
    for link in soup.find_all("a", href=True):
        text = clean_text(link.get_text())
        if not re.fullmatch(r"\d{1,3}", text):
            continue
        number = int(text)
        if not (1 <= number <= 500):
            continue
        parent = link.parent
        by_parent.setdefault(id(parent), set()).add(number)
    pager = max(by_parent.values(), key=len, default=set())
    if len(pager) >= 2:
        return "numbered", max(pager), ""

    params = Counter()
    for link in soup.find_all("a", href=True):
        query = urlparse(urljoin(page_url, link["href"])).query
        for part in query.split("&"):
            key = part.split("=")[0].lower()
            if key in _PAGE_PARAMS:
                params[key] += 1
    # A page-2 link in the current URL is the strongest possible signal.
    for part in urlparse(page_url).query.split("&"):
        key = part.split("=")[0].lower()
        if key in _PAGE_PARAMS:
            params[key] += 3
    if params:
        key, _count = params.most_common(1)[0]
        return "query_param", 0, key
    return "none", 0, ""


def _detect_load_more(soup: bs4.BeautifulSoup) -> bool:
    pattern = re.compile(r"\b(load|show|view|see|display)\s+(?:\d+\s+)?(more|additional|further)\b", re.I)
    for el in soup.find_all(["button", "a"]):
        text = clean_text(el.get_text())
        if pattern.search(text):
            return True
        attrs = " ".join(f"{k}={v}" for k, v in el.attrs.items() if isinstance(v, str)).lower()
        if any(token in attrs for token in ("load-more", "loadmore", "load_more", "show-more", "showmore")):
            return True
    return False


# ---------------------------------------------------------------------------
# Main inspection
# ---------------------------------------------------------------------------
def inspect_page(page: FetchedPage, fetcher: PoliteFetcher | None = None,
                 check_sitemap: bool = False) -> InspectionReport:
    soup = parse_html(page.text)
    report = InspectionReport(
        url=page.url,
        final_url=page.final_url,
        host=host_of(page.final_url),
        status_code=page.status_code,
        page_title=clean_text(soup.title.get_text()) if soup.title else "",
        robots_allowed=page.robots_allowed,
        robots_detail=page.robots_detail,
    )

    # --- structured data ---------------------------------------------------
    nodes: list[dict] = []
    for block in _iter_jsonld(soup):
        _flatten_jsonld(block, nodes)
    report.jsonld_blocks = len(nodes)
    report.jsonld_types = _type_names(nodes)
    report.jsonld_fields = _jsonld_fields(nodes)

    # --- repeated blocks ---------------------------------------------------
    links, sig, count, selector = discover_profile_links(soup, page.final_url, report.host)
    if links:
        report.container_selector = selector
        report.container_count = count
        report.container_signature = sig
        report.profile_link_count = len(links)
        report.profile_link_samples = links[:5]
        report.profile_link_pattern = _derive_pattern(links)

    # A roster can publish records in cards or a table without linking to separate
    # profiles. Detect that structure too, so inspection does not report "nothing"
    # when the static engine can in fact extract the page.
    structure_fields: list[str] = []
    if not links:
        try:
            from .browser.structure import analyze_html

            models = analyze_html(page.text, page.final_url)
        except Exception:
            models = []
        if models:
            best = models[0]
            report.container_selector = best.row_selector
            report.container_signature = best.signature
            report.container_count = best.row_count
            report.estimated_records = best.row_count
            kind_map = {
                "name": "agent_name", "first_name": "agent_name", "last_name": "agent_name",
                "phone": "phone", "email": "email",
                "address": "street_address", "city": "city", "state": "state",
                "postal_code": "postal_code", "country": "country",
                "brokerage": "brokerage_name", "website": "website",
                "url": "profile_url", "title": "title",
            }
            for column in best.columns:
                field = kind_map.get(column.kind_hint)
                if field and field not in structure_fields:
                    structure_fields.append(field)
            report.sample_records = [
                {
                    kind_map.get(column.kind_hint, column.kind_hint):
                        (column.sample_values[0] if column.sample_values else "")
                    for column in best.columns[:12]
                }
            ]

    # --- embedded records --------------------------------------------------
    embedded_records: list = []
    embedded_payload = ""
    try:
        from .browser.embedded import extract_from_embedded

        embedded_records, embedded_payload = extract_from_embedded(page.text, page.final_url)
        report.embedded_record_count = len(embedded_records)
        report.embedded_payloads = [embedded_payload] if embedded_payload else []
    except Exception:
        pass

    # --- navigation --------------------------------------------------------
    report.pagination_type, report.pagination_pages, report.pagination_param = _detect_pagination(
        soup, page.final_url
    )
    report.load_more_detected = _detect_load_more(soup)

    # --- body signals ------------------------------------------------------
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    body_text = clean_text(soup.get_text(" "))[:20000]
    anchors = soup.find_all("a", href=True)
    has_tel = any(str(a["href"]).lower().startswith("tel:") for a in anchors)
    has_mailto = any(str(a["href"]).lower().startswith("mailto:") for a in anchors)
    has_address = bool(_ADDRESS_HINT.search(body_text))
    inline_phones = find_phones(body_text, limit=1)
    inline_emails = find_emails(body_text, limit=1)

    available: list[str] = []
    if report.jsonld_types or report.profile_link_count:
        if "person" in {t.lower() for t in report.jsonld_types} or "realestateagent" in {
            t.lower() for t in report.jsonld_types
        }:
            available.append("agent_name")
        if has_tel or inline_phones:
            available.append("phone")
        if has_mailto or inline_emails:
            available.append("email")
        if has_address:
            available.extend(["street_address", "city", "state", "postal_code", "country"])
        if has_mailto or inline_emails:
            available.append("email")
        if any("website" in str(a.get("class") or "").lower() or "website" in str(a.get("href") or "").lower()
               for a in anchors):
            available.append("website")
        if report.profile_link_count:
            available.extend(["profile_url", "source_url", "brokerage_name"])
    available.extend(structure_fields)
    report.available_fields = list(dict.fromkeys(available))
    if embedded_records:
        for key in ("agent_name", "brokerage_name", "phone", "email", "street_address",
                    "city", "state", "postal_code", "website", "profile_url", "title"):
            if key not in report.available_fields:
                report.available_fields.append(key)
        report.sample_records = [
            {
                key: str(value)[:200]
                for key, value in (record.fields or {}).items()
                if value and not str(key).startswith("_")
            }
            for record in embedded_records[:5]
        ]
    report.suggested_fields = {key: "detected" for key in report.available_fields}
    report.detected_layers = [
        layer_name for layer_name, present in (
            ("schema.org structured data", bool(report.jsonld_blocks)),
            (f"embedded JSON ({embedded_payload})", bool(embedded_records)),
            ("profile links", bool(report.profile_link_count)),
            ("listing blocks", bool(report.container_count)),
        ) if present
    ]

    # --- capability --------------------------------------------------------
    script_count = len(re.findall(r"<script\b", page.text, flags=re.I))
    if report.profile_link_count == 0 and report.embedded_record_count >= 3:
        report.requires_javascript = False
        report.warnings.append(
            f"{report.embedded_record_count} records are available in embedded JSON "
            f"“{embedded_payload or 'payload'}”, so this page can be extracted without a browser."
        )
    elif report.profile_link_count == 0 and report.container_count > 0:
        report.requires_javascript = False
        report.warnings.append(
            f"No separate profile links were found, but {report.container_count} records are "
            "visible in repeated cards or a table. Static extraction can use those records "
            "directly; profile enrichment is not available for this layout."
        )
    elif report.profile_link_count == 0:
        if report.jsonld_blocks > 0:
            report.requires_javascript = False
            report.warnings.append(
                "No repeated profile links were found, but structured data is present. "
                "This may be a single profile page rather than a directory listing."
            )
        elif script_count >= 5 or len(body_text) < 400:
            report.requires_javascript = True
            report.warnings.append(
                "The listing appears to be rendered by JavaScript: almost no links or text in the "
                "returned HTML. This site needs the browser-automation path or a dedicated adapter."
            )
        else:
            report.warnings.append(
                "No agent profile links were detected. The page may not be a directory listing, "
                "or the links use an unfamiliar URL pattern."
            )

    if report.load_more_detected:
        report.warnings.append(
            "A 'load more' control was found. Results beyond the first batch require the "
            "browser-automation path; the static extractor will only see the first batch."
        )
    if not report.robots_allowed:
        report.warnings.append(
            f"robots.txt note: {report.robots_detail}. This run is not blocked by default because "
            "robots.txt is a crawling convention, not a statement of permission. Review the site's "
            "terms before commercial use; set B2B_ROBOTS_POLICY=enforce to block automatically."
        )

    # --- sitemap (only when it would add information) -----------------------
    if check_sitemap and fetcher is not None and report.profile_link_count == 0:
        sitemap_url = urljoin(page.final_url, "/sitemap.xml")
        sitemap = fetcher.get_optional(sitemap_url)
        head = sitemap.text[:4000].lower() if sitemap else ""
        report.sitemap_found = bool(
            sitemap and sitemap.status_code == 200 and ("<urlset" in head or "<sitemapindex" in head)
        )

    # --- recommendation ----------------------------------------------------
    types_lower = {t.lower() for t in report.jsonld_types}
    # An ItemList of profile URLs on the roster strongly implies the profiles
    # themselves carry schema.org markup, which is the higher-fidelity source.
    if report.profile_link_count and (types_lower & {"realestateagent", "person", "itemlist"}):
        report.recommended_adapter = "generic_jsonld"
    elif report.profile_link_count:
        report.recommended_adapter = "generic_html"
    elif report.container_count:
        report.recommended_adapter = "generic_html"
    elif report.requires_javascript:
        report.recommended_adapter = "browser_required"
    else:
        report.recommended_adapter = ""

    report.estimated_records = max(report.container_count, report.embedded_record_count)
    return report


_SITEMAP_PATHS = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/sitemaps/sitemap.xml",
    "/wp-sitemap.xml",
)
_SITEMAP_SKIP = re.compile(
    r"(/blog/|/news/|/article|/press/|/event|/career|/job|/faq|/privacy|/terms|"
    r"/legal|/search|/tag/|/category/|/author/|/page/|\.xml$|\.gz$|\.jpg|\.png|\.pdf)",
    re.I,
)


def _xml_locs(text: str) -> list[str]:
    """Read <loc> values without assuming a well-formed namespace."""
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(text.strip())
        locs: list[str] = []
        for element in root.iter():
            if str(element.tag).lower().endswith("loc") and element.text:
                value = clean_text(element.text)
                if value:
                    locs.append(value)
        if locs:
            return locs
    except Exception:
        pass
    return [clean_text(value) for value in re.findall(r"<loc[^>]*>(.*?)</loc>", text, re.I | re.S)]


def discover_sitemap_profile_urls(
    base_url: str,
    fetcher: PoliteFetcher,
    settings=None,
    max_urls: int = 50,
    max_sitemaps: int = 4,
) -> list[str]:
    """Discover profile URLs from a site's XML sitemap.

    This is the last-resort structural layer for a directory whose roster is
    JavaScript-only but whose profiles are individually listed in the sitemap.
    It only reads sitemap files; it does not bypass any access control.
    """
    base_url = base_url or ""
    host = host_of(base_url)
    if not host:
        return []
    root = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    queue = [urljoin(root, path) for path in _SITEMAP_PATHS]
    seen_sitemaps: set[str] = set()
    found: list[str] = []

    while queue and len(seen_sitemaps) < max_sitemaps and len(found) < max_urls:
        sitemap_url = queue.pop(0)
        key = canonical_url(sitemap_url)
        if not key or key in seen_sitemaps:
            continue
        seen_sitemaps.add(key)
        page = fetcher.get_optional(sitemap_url)
        if page is None or page.status_code != 200:
            continue
        text = page.text[:5_000_000]
        locs = _xml_locs(text)
        if not locs:
            continue
        if "<sitemapindex" in text.lower():
            for loc in locs[:12]:
                if len(seen_sitemaps) + len(queue) >= max_sitemaps * 2:
                    break
                queue.append(urljoin(root, loc))
            continue
        for loc in locs:
            absolute = urljoin(root, loc)
            if not same_site(absolute, base_url):
                continue
            path = urlparse(absolute).path
            if _SITEMAP_SKIP.search(path + "/"):
                continue
            if not _looks_like_profile(absolute, host):
                continue
            canonical = canonical_url(absolute)
            if canonical and canonical not in found:
                found.append(canonical)
            if len(found) >= max_urls:
                break
    return found


def inspect_url(url: str, fetcher: PoliteFetcher, check_sitemap: bool = True) -> InspectionReport:
    """Fetch and inspect. Raises FetchError subclasses with user-safe messages."""
    page = fetcher.get(url)
    if not page.is_html:
        raise FetchError(
            f"URL returned {page.content_type or 'a non-HTML type'}, not an HTML page.", url, page.status_code
        )
    report = inspect_page(page, fetcher, check_sitemap=check_sitemap)
    try:
        report.robots_detail = report.robots_detail or fetcher.robots_status(url)
    except Exception:
        report.robots_detail = report.robots_detail or ""
    return report
