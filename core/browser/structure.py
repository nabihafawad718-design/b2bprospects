"""Repeated-structure detection for rendered pages.

Given the DOM of a page that a real browser has finished rendering, find the
region that repeats — agent cards, a results table, a team grid — and describe it
as rows with named-position columns.

The output is deliberately selector-light. Columns are addressed by their position
path inside a row, so the operator can pick "the column showing Jane Smith" without
ever seeing CSS, and the same model still works on page 2.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import bs4

from ..inspect_site import parse_html
from ..models import ColumnProfile, StructureModel, signature
from ..normalize import (
    clean_text,
    email_from_fragment,
    find_emails,
    find_phones,
    host_of,
    looks_like_person_name,
    parse_address,
    tel_from_fragment,
)

try:  # soupsieve ships with BeautifulSoup; the fallback keeps the module importable.
    from soupsieve import escape as _css_escape
except Exception:  # pragma: no cover
    def _css_escape(value: str) -> str:
        return re.sub(r"([^a-zA-Z0-9_-])", r"\\\1", value)

# Tags that never describe a record. They are removed before position paths are
# computed so analysis, extraction and the visual snapshot all agree.
NON_CONTENT_TAGS = (
    "script", "style", "noscript", "template", "svg", "iframe", "object",
    "embed", "applet", "frame", "frameset",
)

# Parents worth treating as a listing container. `nav` is included but heavily
# penalised, because menus also repeat and would otherwise win on raw row count.
_CONTAINER_TAGS = ("div", "ul", "ol", "table", "tbody", "section", "article", "main", "form", "nav")

# How deep inside a row we look for field values.
_MAX_CELL_DEPTH = 5
_MAX_CELLS_PER_ROW = 120

# Classes that change between otherwise identical rows (odd/even, hover state,
# animation delays) must not split the group.
_STATE_CLASS = re.compile(
    r"^(odd|even|first|last|active|selected|current|hover|focus|open|closed|"
    r"is-[\w-]+|has-[\w-]+|row-\d+|item-\d+|col-\d+|index-\d+|delay-\d+|"
    r"animate[\w-]*|fade[\w-]*|css-[\w-]+|\d+)$",
    re.I,
)
_NAV_WORDS = re.compile(r"(nav|menu|breadcrumb|footer|header|social|cookie|banner|toolbar)", re.I)

_ORG_WORDS = re.compile(
    r"\b(realty|real estate|properties|property|group|partners|agency|brokerage|homes|"
    r"associates|team|inc|llc|ltd|corp|company|capital|estates|realtors|management|"
    r"services|consulting|solutions|holdings|ventures)\b", re.I
)
_ADDRESS_WORDS = re.compile(
    r"\b(st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|lane|way|ct|court|pl|place|"
    r"pkwy|parkway|hwy|highway|suite|ste|floor|fl|unit|apt)\b\.?", re.I
)
_CITY_STATE = re.compile(r"^[A-Za-z][A-Za-z .'\-]{1,40},\s*[A-Z]{2}$")
_TITLE_CASE_NAME = re.compile(
    r"^[^\W\d_][\w'’\-]*(?:\s+[^\W\d_][\w'’\-.]*){1,4}$", re.UNICODE
)
_DOMAIN_TEXT = re.compile(r"^(?:https?://|www\.)\S+\.\S+$", re.I)
_JOB_TITLE = re.compile(
    r"\b(senior|junior|associate broker|broker associate|listing specialist|buyer'?s agent|"
    r"team lead|manager|director|advisor|specialist|coordinator|officer|assistant|"
    r"agent|realtor|salesperson|consultant|partner)\b",
    re.I,
)
_POSTAL_ONLY = re.compile(r"^\d{5}(?:-\d{4})?$")
_ONE_WORD_NAME = re.compile(r"^[A-Z][A-Za-z'’\-]{1,30}$")

# Column headings are the strongest field signal on table-based directories.
# Matching is deliberately explicit: an unrecognised heading falls back to value
# profiling rather than guessing.
_HEADER_KIND = {
    "name": "name", "full name": "name", "agent name": "name", "agent": "name",
    "associate name": "name", "sales associate": "name", "contact name": "name",
    "name of agent": "name", "agent full name": "name", "contact": "name",
    "representative": "name", "salesperson": "name",
    "first name": "first_name", "firstname": "first_name", "first": "first_name",
    "given name": "first_name", "givenname": "first_name",
    "last name": "last_name", "lastname": "last_name", "last": "last_name",
    "surname": "last_name", "family name": "last_name", "familyname": "last_name",
    "phone": "phone", "phone number": "phone", "telephone": "phone", "tel": "phone",
    "telephone number": "phone", "phone no": "phone", "tel no": "phone",
    "office phone": "phone", "direct phone": "phone", "mobile": "phone",
    "mobile phone": "phone", "cell": "phone", "cell phone": "phone", "work phone": "phone",
    "business phone": "phone", "primary phone": "phone", "direct line": "phone",
    "email": "email", "e-mail": "email", "email address": "email", "e mail": "email",
    "email id": "email", "e-mail address": "email",
    "office": "brokerage", "office name": "brokerage", "brokerage": "brokerage",
    "company": "brokerage", "company name": "brokerage", "firm": "brokerage",
    "agency": "brokerage", "organization": "brokerage",
    "address": "address", "office address": "address", "street address": "address",
    "business address": "address", "location address": "address",
    "city": "city", "town": "city", "state": "state", "province": "state",
    "state province": "state", "region": "state",
    "zip": "postal_code", "zip code": "postal_code", "postal code": "postal_code",
    "postcode": "postal_code", "postal": "postal_code", "postal zip": "postal_code",
    "country": "country",
    "title": "title", "job title": "title", "position": "title", "role": "title",
    "designation": "title", "specialty": "title", "specialties": "title",
    "website": "website", "web": "website", "web site": "website",
    "profile": "url", "profile url": "url", "link": "url", "details": "url",
}


@dataclass
class Cell:
    path: str
    tag: str
    text: str
    href: str = ""


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------
def _path(parent: bs4.Tag, node: bs4.Tag) -> str:
    """Position path of `node` inside `parent`, e.g. '1.4'.

    Index is over all element siblings, not same-tag siblings: a div and a p must
    not both become index 0, or fields from different parts of a card collide.
    """
    parts: list[str] = []
    current: bs4.Tag | None = node
    while current is not None and current is not parent:
        parent_node = current.parent
        if parent_node is None:
            break
        element_siblings = [c for c in parent_node.children if getattr(c, "name", None)]
        try:
            index = element_siblings.index(current)
        except ValueError:
            index = 0
        parts.append(str(index))
        current = parent_node
    return ".".join(reversed(parts))


def _is_data_row(row: bs4.Tag) -> bool:
    if row.name != "tr":
        return True
    if row.find("th"):
        return False
    if row.find_parent("thead"):
        return False
    return True


def _attribute_text(node: bs4.Tag) -> str:
    """A usable value when the element has no text of its own.

    Icons, image links and data-attribute cards are common on modern directories;
    without this, a phone icon with a `tel:` href disappears from the column model.
    """
    href = str(node.get("href") or "").strip()
    if href.lower().startswith("tel:"):
        phone = tel_from_fragment(href)
        if phone:
            return phone
    if href.lower().startswith("mailto:"):
        email = email_from_fragment(href)
        if email:
            return email
    for attribute in (
        "data-phone", "data-telephone", "data-tel", "data-mobile", "data-cell",
        "data-email", "data-address", "data-street", "data-city", "data-state",
        "data-zip", "data-postal-code", "data-name", "data-full-name",
        "data-brokerage", "data-company", "data-office", "data-title",
    ):
        value = node.get(attribute)
        if value:
            return clean_text(value)
    for attribute in ("aria-label", "title"):
        value = node.get(attribute)
        if value and len(clean_text(value)) <= 160:
            lowered = clean_text(value).lower()
            if lowered not in {"phone", "email", "call", "contact", "view profile", "website"}:
                return clean_text(value)
    return ""


_ROW_DATA_ATTRIBUTES = (
    "data-name", "data-full-name", "data-agent-name",
    "data-phone", "data-telephone", "data-tel", "data-mobile", "data-cell",
    "data-email", "data-address", "data-street", "data-city", "data-state",
    "data-zip", "data-postal-code", "data-brokerage", "data-company",
    "data-office", "data-title",
)


def row_cells(row: bs4.Tag, base_url: str = "") -> list[Cell]:
    """Flatten one row into addressable cells.

    For an element with element children, only its *own* text nodes are used. That
    avoids a wrapper `<a>` around a whole card producing a catch-all column that
    duplicates every field, while still capturing mixed content such as
    `<p>Call <span>now</span> (512) 555-0142</p>`.

    Data attributes on the row container itself become their own cells. Many
    modern directories put the real values there and render only icons.
    """
    cells: list[Cell] = [ ]
    count = 0
    for attribute in _ROW_DATA_ATTRIBUTES:
        value = row.get(attribute)
        if value:
            text = clean_text(value)
            if text:
                href = text if attribute in ("data-url", "data-href") else ""
                cells.append(Cell(f"@{attribute}", row.name or "", text, href))

    def direct_text(el: bs4.Tag) -> str:
        parts = [
            str(node) for node in el.children
            if isinstance(node, bs4.NavigableString) and not isinstance(node, bs4.Comment)
        ]
        return clean_text(" ".join(parts))

    def walk(node: bs4.Tag, depth: int) -> None:
        nonlocal count
        if depth > _MAX_CELL_DEPTH or count >= _MAX_CELLS_PER_ROW:
            return
        for child in node.find_all(True, recursive=False):
            count += 1
            has_element_children = child.find(True) is not None
            text = direct_text(child) if has_element_children else clean_text(child.get_text(" ", strip=True))
            if not text:
                text = _attribute_text(child)
            href = str(child.get("href") or "").strip()
            if href.startswith("#"):
                href = ""
            if text:
                cells.append(Cell(_path(row, child), child.name, text, href))
            elif href:
                cells.append(Cell(_path(row, child), child.name, "", href))
            walk(child, depth + 1)

    walk(row, 0)
    return cells


# ---------------------------------------------------------------------------
# Column profiling
# ---------------------------------------------------------------------------
def normalize_header(text: object) -> str:
    value = clean_text(text).lower()
    value = re.sub(r"[^a-z0-9 _-]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def kind_for_header(header: str) -> str:
    return _HEADER_KIND.get(normalize_header(header), "")


def _guess_kind(values: list[str], href_rate: float, header: str = "") -> str:
    samples = [v for v in values if v][:8]
    if not samples:
        return "url" if href_rate > 0.8 else "text"

    header_kind = kind_for_header(header)
    # A heading is a strong hint, but never let it call a list of organisations
    # person names or a column of job titles a person's name.
    if header_kind:
        if header_kind == "name":
            if all(_ORG_WORDS.search(v) for v in samples):
                return "brokerage"
            name_hits = sum(
                1 for v in samples
                if looks_like_person_name(v) or (_TITLE_CASE_NAME.match(v) and v.istitle())
            )
            title_hits = sum(1 for v in samples if _JOB_TITLE.search(v))
            if title_hits >= max(1, len(samples) // 2) and name_hits < max(1, len(samples) // 2):
                return "title"
            return "name"
        if header_kind == "brokerage" and any(
            looks_like_person_name(v) for v in samples
        ) and not all(_ORG_WORDS.search(v) for v in samples):
            return "name"
        return header_kind

    email_hits = sum(1 for v in samples if find_emails(v, limit=1))
    if email_hits >= max(1, len(samples) // 2):
        return "email"

    phone_hits = sum(1 for v in samples if find_phones(v, limit=1))
    if phone_hits >= max(1, len(samples) // 2):
        return "phone"

    postal_hits = sum(1 for v in samples if _POSTAL_ONLY.match(v.strip()))
    if postal_hits >= max(1, len(samples) // 2):
        return "postal_code"

    address_hits = sum(
        1 for v in samples
        if _ADDRESS_WORDS.search(v) and (re.search(r"\d", v) or parse_address(v).get("state"))
    )
    if address_hits >= max(1, len(samples) // 2):
        return "address"

    if sum(1 for v in samples if _CITY_STATE.match(v)) >= max(1, len(samples) // 2):
        return "city"

    # A value repeated across rows and shaped like a firm name is the brokerage.
    # Checked before "name" so "Harborline Properties" is not read as a person.
    if len(set(v.lower() for v in samples)) <= 2 and _ORG_WORDS.search(samples[0]):
        return "brokerage"

    # Job titles are title-case too ("Senior Agent", "Team Lead"). They must not
    # win the name column just because they look like two capitalised words, so
    # this check comes before the name rule.
    title_hits = sum(
        1 for v in samples
        if _JOB_TITLE.search(v) and not _ORG_WORDS.search(v) and len(v) <= 80
    )
    if title_hits >= max(1, len(samples) // 2):
        return "title"

    # Names are frequently links, so this must be decided before the URL rule.
    name_hits = sum(
        1 for v in samples
        if looks_like_person_name(v) or (_TITLE_CASE_NAME.match(v) and v.istitle())
    )
    if name_hits >= max(1, len(samples) // 2):
        # A firm name made only of title-case words is still a firm.
        if all(_ORG_WORDS.search(v) for v in samples):
            return "brokerage"
        return "name"

    if all(_ORG_WORDS.search(v) for v in samples):
        return "brokerage"

    if all(_DOMAIN_TEXT.match(v) for v in samples):
        return "website"

    if href_rate > 0.8:
        if any(v.lower().startswith(("http", "/", "www.")) for v in samples) or href_rate > 0.95:
            return "url"

    return "text"


def _header_labels(parent: bs4.Tag, rows: list[bs4.Tag]) -> list[str]:
    """Return column headings for a table-like group, in visual order.

    `th` cells are excluded from data rows, so value profiling cannot see them.
    Reading them is what lets a plain table be understood instead of guessed.
    """
    headers: list[str] = []
    head = parent.find("thead")
    if head is None and parent.parent is not None and parent.parent.name == "table":
        head = parent.parent.find("thead")
    if head is not None:
        header_row = head.find("tr")
        if header_row is not None:
            cells = header_row.find_all(["th", "td"], recursive=False)
            headers = [clean_text(cell.get_text(" ")) for cell in cells]
    if not headers and rows and rows[0].name == "tr":
        first = rows[0]
        th_cells = first.find_all("th", recursive=False)
        if th_cells:
            headers = [clean_text(cell.get_text(" ")) for cell in th_cells]
    if not headers:
        # Some grids use a dedicated header row of divs with role=columnheader.
        header_nodes = parent.find_all(attrs={"role": "columnheader"})
        if header_nodes:
            headers = [clean_text(node.get_text(" ")) for node in header_nodes]
    return [header for header in headers if header]


def _columns_for(rows: list[bs4.Tag], base_url: str,
                 headers: list[str] | None = None) -> list[ColumnProfile]:
    per_row: list[dict[str, Cell]] = []
    for row in rows:
        per_row.append({cell.path: cell for cell in row_cells(row, base_url)})

    all_paths: set[str] = set()
    for mapping in per_row:
        all_paths.update(mapping)

    def sort_key(path: str):
        if path.startswith("@"):
            return (1000, path)
        return tuple(int(p) if p.isdigit() else 0 for p in path.split("."))

    columns: list[ColumnProfile] = []
    for index, path in enumerate(sorted(all_paths, key=sort_key)):
        present = [m[path] for m in per_row if path in m]
        if not present:
            continue
        texts: list[str] = []
        for cell in present:
            value = cell.text or cell.href
            if value and value not in texts:
                texts.append(value)
        if not texts:
            continue
        href_rate = sum(1 for c in present if c.href) / len(present)
        tag = present[0].tag
        # A top-level table cell path is its ordinal ("0", "1", ...), which lines
        # up with the header row.
        header = ""
        if headers and path.isdigit() and int(path) < len(headers):
            header = headers[int(path)]
        kind = _guess_kind(texts, href_rate, header)
        columns.append(
            ColumnProfile(
                index=index,
                path=path,
                tag=tag,
                sample_values=texts[:3],
                fill_rate=round(len(present) / len(per_row), 3),
                href_rate=round(href_rate, 3),
                kind_hint=kind,
                label=f"Column {index + 1}",
                header=header,
            )
        )

    # Adjacent single-word columns are often first/last name even without a header.
    for left, right in zip(columns, columns[1:]):
        if left.kind_hint != "text" or right.kind_hint != "text":
            continue
        if left.fill_rate < 0.6 or right.fill_rate < 0.6:
            continue
        left_samples = [v for v in left.sample_values if _ONE_WORD_NAME.match(v)]
        right_samples = [v for v in right.sample_values if _ONE_WORD_NAME.match(v)]
        if not left_samples or not right_samples:
            continue
        combined = [f"{a} {b}" for a, b in zip(left.sample_values, right.sample_values)]
        if sum(1 for value in combined if looks_like_person_name(value)) >= max(1, len(combined) // 2):
            left.kind_hint = "first_name"
            right.kind_hint = "last_name"
    return columns


# ---------------------------------------------------------------------------
# Container scoring
# ---------------------------------------------------------------------------
def _stable_classes(tag: bs4.Tag) -> tuple[str, ...]:
    classes = [c for c in (tag.get("class") or []) if isinstance(c, str) and c and not _STATE_CLASS.match(c)]
    return tuple(sorted(classes))


def _shape(tag: bs4.Tag) -> tuple[str, tuple[str, ...]]:
    return (tag.name or "", _stable_classes(tag))


def _css_class_selector(tag: bs4.Tag) -> str:
    classes = [c for c in _stable_classes(tag) if c]
    name = tag.name or "div"
    if classes:
        return name + "." + ".".join(_css_escape(c) for c in classes[:2])
    if tag.get("id"):
        return f"#{_css_escape(str(tag['id']))}"
    return name


def _score(rows: int, columns: list[ColumnProfile], has_links: bool,
           parent: bs4.Tag | None = None, profile_ratio: float = 0.0,
           method: str = "browser-dom") -> float:
    kinds = {c.kind_hint for c in columns}
    score = rows * 2.0 + min(len(columns), 10) * 2.5
    score += 7 if kinds & {"phone", "email"} else 0
    score += 5 if kinds & {"name", "first_name"} else 0
    score += 4 if kinds & {"phone", "email"} and kinds & {"name", "first_name"} else 0
    score += 3 if has_links else 0
    score += min(6.0, profile_ratio * 8.0)
    if method == "link-cluster":
        score -= 6  # a fallback detector must not outrank a clean card group
    if rows < 3:
        score -= 4
    # Page furniture repeats too; make sure it cannot outrank a roster.
    if parent is not None:
        if parent.name in ("nav", "header", "footer"):
            score -= 14
        classes = " ".join(_stable_classes(parent))
        if _NAV_WORDS.search(classes) or _NAV_WORDS.search(str(parent.get("id") or "")):
            score -= 10
    return score


def _fallback_groups(parent: bs4.Tag, children: list[bs4.Tag]) -> list[list[bs4.Tag]]:
    """Group cards whose classes differ per record.

    Many sites give every card a unique class (or no class at all). If no
    class-based group produced a candidate, fall back to grouping by tag when the
    children look like records: same tag, several descendants, at least two links.
    """
    by_tag: dict[str, list[bs4.Tag]] = {}
    for child in children:
        by_tag.setdefault(child.name or "", []).append(child)
    groups: list[list[bs4.Tag]] = []
    for tag, group in by_tag.items():
        if len(group) < 3 or len(group) < len(children) * 0.75:
            continue
        usable = [
            child for child in group
            if len(child.find_all(True)) >= 2 and (child.find("a", href=True) or len(clean_text(child.get_text(" "))) >= 8)
        ]
        if len(usable) >= 3:
            groups.append(usable)
    return groups


def _profile_ratio(rows: list[bs4.Tag], base_url: str) -> float:
    if not rows or not base_url:
        return 0.0
    try:
        from ..inspect_site import _looks_like_profile

        host = host_of(base_url)
        hits = 0
        for row in rows:
            if any(
                _looks_like_profile(urljoin(base_url, str(a["href"])), host)
                for a in row.find_all("a", href=True)
            ):
                hits += 1
        return hits / len(rows)
    except Exception:
        return 0.0


def _path_shape(href: str) -> str:
    segments = [s for s in urlparse(href).path.split("/") if s]
    if not segments:
        return ""
    # Keep the section root (/agents, /team) and wildcard the record slug.
    if len(segments) == 1:
        return "*"
    return segments[0].lower() + "/" + "/".join("*" for _ in segments[1:])


def _link_cluster_candidates(soup: bs4.BeautifulSoup, base_url: str) -> list[StructureModel]:
    """Find records by clustering profile links, for layouts with no clean card group.

    Some directories render each record as an irregular wrapper, a table-like grid
    of divs, or a card whose classes differ per record. Grouping anchors by their
    URL shape and then walking up to the nearest record-sized container recovers
    those layouts without a site-specific selector.
    """
    if not base_url:
        return []
    try:
        from ..inspect_site import _looks_like_profile

        host = host_of(base_url)
        groups: dict[str, list[bs4.Tag]] = {}
        for anchor in soup.find_all("a", href=True):
            if anchor.find_parent(["nav", "header", "footer"]):
                continue
            href = urljoin(base_url, str(anchor["href"]))
            if not _looks_like_profile(href, host):
                continue
            shape = _path_shape(href)
            if shape:
                groups.setdefault(shape, []).append(anchor)
    except Exception:
        return []

    models: list[StructureModel] = []
    for _shape_name, anchors in sorted(groups.items(), key=lambda item: -len(item[1]))[:6]:
        if len(anchors) < 3:
            continue
        containers: list[bs4.Tag] = []
        for anchor in anchors:
            node: bs4.Tag | None = anchor
            chosen = None
            for _depth in range(8):
                if node is None or node.name in ("body", "main", "html"):
                    break
                text = clean_text(node.get_text(" "))
                descendants = len(node.find_all(True))
                if text and (len(text) >= 20 or descendants >= 3) and len(text) <= 1200 and descendants <= 100:
                    chosen = node
                    break
                node = node.parent
            if chosen is not None:
                containers.append(chosen)

        by_parent: dict[int, list[bs4.Tag]] = {}
        for container in containers:
            if container.parent is not None:
                by_parent.setdefault(id(container.parent), []).append(container)

        for group in by_parent.values():
            rows = [row for row in dict.fromkeys(group) if clean_text(row.get_text(" "))]
            if len(rows) < 3:
                continue
            columns = _columns_for(rows, base_url)
            if not columns:
                continue
            if not any(c.kind_hint in {"name", "first_name", "last_name", "phone", "email", "title"}
                       for c in columns):
                continue
            parent = rows[0].parent
            container_selector = _css_class_selector(parent) if parent is not None else "body"
            shapes = {_shape(row) for row in rows}
            if len(shapes) == 1 and _stable_classes(rows[0]):
                row_selector = _css_class_selector(rows[0])
            else:
                tags = sorted({row.name for row in rows if row.name})
                siblings = [child for child in (parent.children if parent is not None else [])
                            if getattr(child, "name", None)]
                if parent is not None and len(rows) >= max(3, int(len(siblings) * 0.75)):
                    # All/most children are records even though their tags differ.
                    row_selector = f"{container_selector} > *"
                elif tags:
                    row_selector = f"{container_selector} > :is({','.join(tags)})"
                else:
                    row_selector = f"{container_selector} > {rows[0].name}"
            models.append(
                StructureModel(
                    container_selector=container_selector,
                    row_selector=row_selector,
                    row_count=len(rows),
                    columns=columns,
                    signature=signature(
                        "link-cluster", parent.name if parent is not None else "",
                        rows[0].name or "", str(len(columns)),
                    ),
                    heading=clean_text(parent.find(["h1", "h2", "h3"]).get_text(" "))
                    if parent is not None and parent.find(["h1", "h2", "h3"]) else "",
                    score=round(_score(len(rows), columns, True, parent, 1.0, "link-cluster"), 2),
                    method="link-cluster",
                    headers=_header_labels(parent, rows) if parent is not None else [],
                )
            )
    return models


def clean_soup(html: str) -> bs4.BeautifulSoup:
    """Parse HTML with non-content elements removed, for consistent paths."""
    soup = parse_html(html)
    for tag in soup(list(NON_CONTENT_TAGS)):
        tag.decompose()
    return soup


def analyze_html(html: str, base_url: str = "") -> list[StructureModel]:
    """Return up to five candidate repeated regions, best first."""
    soup = clean_soup(html)

    candidates: list[StructureModel] = []
    seen: set[tuple[str, int, int]] = set()

    for parent in soup.find_all(_CONTAINER_TAGS):
        children = parent.find_all(True, recursive=False)
        if not (3 <= len(children) <= 600):
            continue

        groups: dict[tuple, list[bs4.Tag]] = {}
        for child in children:
            groups.setdefault(_shape(child), []).append(child)

        candidate_groups: list[tuple[list[bs4.Tag], bool]] = [
            (group, False) for group in groups.values() if len(group) >= 3
        ]
        if not candidate_groups:
            candidate_groups = [(group, True) for group in _fallback_groups(parent, children)]

        for group, is_fallback in candidate_groups:
            rows = [r for r in group if _is_data_row(r) and clean_text(r.get_text(" "))]
            if len(rows) < 3:
                continue

            headers = _header_labels(parent, rows)
            columns = _columns_for(rows, base_url, headers)
            if not columns:
                continue

            has_links = any(c.href_rate > 0.5 for c in columns)
            profile_ratio = _profile_ratio(rows, base_url)
            container_selector = _css_class_selector(parent)
            if is_fallback:
                # Unique classes per card (card-0, card-1, ...) make a class
                # selector match one row. Scope by tag inside the container instead.
                row_selector = f"{container_selector} > {rows[0].name}"
            else:
                row_selector = _css_class_selector(rows[0])
            key = (container_selector, row_selector, len(rows), len(columns))
            if key in seen:
                continue
            seen.add(key)

            candidates.append(
                StructureModel(
                    container_selector=container_selector,
                    row_selector=row_selector,
                    row_count=len(rows),
                    columns=columns,
                    signature=signature(
                        parent.name or "", *_stable_classes(parent),
                        rows[0].name or "", *_stable_classes(rows[0]), str(len(columns)),
                    ),
                    heading=clean_text(parent.find(["h1", "h2", "h3"]).get_text(" "))
                    if parent.find(["h1", "h2", "h3"]) else "",
                    score=round(_score(len(rows), columns, has_links, parent, profile_ratio), 2),
                    headers=headers,
                )
            )

    # If the ordinary grouping found no convincing roster, profile-link clusters
    # are the next-best structural signal.
    if not any(
        model.row_count >= 3
        and any(c.kind_hint in {"name", "first_name", "last_name"} for c in model.columns)
        for model in candidates
    ):
        for model in _link_cluster_candidates(soup, base_url):
            key = (model.container_selector, model.row_selector, model.row_count, len(model.columns))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(model)

    candidates.sort(key=lambda c: (-c.score, -c.row_count))
    return candidates[:5]


def best_structure(html: str, base_url: str = "") -> StructureModel | None:
    models = analyze_html(html, base_url)
    return models[0] if models else None


# ---------------------------------------------------------------------------
# Extraction from a structure
# ---------------------------------------------------------------------------
def select_rows(html: str, structure: StructureModel) -> list[bs4.Tag]:
    """Locate the rows on a new page, falling back to re-detection."""
    soup = clean_soup(html)
    rows: list[bs4.Tag] = []
    if structure.row_selector:
        try:
            rows = [r for r in soup.select(structure.row_selector) if _is_data_row(r)]
        except Exception:
            rows = []
    if len(rows) >= 2:
        return rows

    # The markup changed enough that the selector missed: re-detect and take the
    # candidate that looks most similar to the one the user mapped.
    for model in analyze_html(html):
        try:
            candidate = [r for r in soup.select(model.row_selector) if _is_data_row(r)]
        except Exception:
            continue
        if len(candidate) >= 2 and abs(len(model.columns) - len(structure.columns)) <= 2:
            return candidate
    return rows


def row_values(row: bs4.Tag, base_url: str = "") -> dict[str, Cell]:
    return {cell.path: cell for cell in row_cells(row, base_url)}


def value_for(cells: dict[str, Cell], column: ColumnProfile | None) -> str:
    if column is None:
        return ""
    cell = cells.get(column.path)
    if cell is None:
        return ""
    return cell.text or cell.href


def href_for(cells: dict[str, Cell], column: ColumnProfile | None) -> str:
    if column is None:
        return ""
    cell = cells.get(column.path)
    return cell.href if cell else ""


def column_for_click(structure: StructureModel, path: str, text: str = "",
                     html: str = "", selector: str = "") -> int | None:
    """Map a visual-picker click (position path + text) to a detected column.

    Exact path first, then the clicked element located by its CSS selector inside
    the actual snapshot (which is authoritative when the picker's row guess
    differs), then the nearest ancestor/descendant path, then the column whose
    sample value contains the clicked text.
    """
    path = str(path or "").strip(".")
    clicked_text = clean_text(text)

    for column in structure.columns:
        if column.path == path:
            return column.index

    # Resolve the clicked element in the snapshot and compute its position path
    # relative to the structure's real row. This is more reliable than trusting
    # the JavaScript row heuristic on deeply nested or irregular cards.
    if html and selector:
        try:
            soup = parse_html(html)
            node = soup.select_one(selector)
            rows = soup.select(structure.row_selector) if structure.row_selector else []
            if node is not None:
                for row in rows:
                    if node is row or row in node.parents:
                        resolved = _path(row, node)
                        for column in structure.columns:
                            if column.path == resolved:
                                return column.index
                        # The click may be on a text wrapper inside the column.
                        prefix = resolved.split(".") if resolved else []
                        best = (-1, None)
                        for column in structure.columns:
                            parts = column.path.split(".") if column.path else []
                            shared = 0
                            for left, right in zip(prefix, parts):
                                if left != right:
                                    break
                                shared += 1
                            if shared > best[0]:
                                best = (shared, column.index)
                        if best[1] is not None and best[0] > 0:
                            return best[1]
        except Exception:
            pass

    # A click often lands on a text node wrapper inside the recorded column, or on
    # a child element of it. Longest shared prefix wins.
    best: tuple[int, int | None] = (-1, None)
    clicked_parts = path.split(".") if path else []
    for column in structure.columns:
        column_parts = column.path.split(".") if column.path else []
        shared = 0
        for left, right in zip(clicked_parts, column_parts):
            if left != right:
                break
            shared += 1
        if shared > best[0]:
            best = (shared, column.index)
    if best[1] is not None and best[0] > 0:
        return best[1]

    if clicked_text:
        needle = clicked_text.lower()
        for column in structure.columns:
            for sample in column.sample_values:
                sample_text = clean_text(sample).lower()
                if sample_text and (sample_text in needle or needle in sample_text):
                    return column.index
    return None


def suggested_extra_label(column: ColumnProfile | None) -> str:
    if column is None:
        return "Extra field"
    if column.header:
        return column.header[:40]
    sample = column.sample_values[0] if column.sample_values else ""
    sample = clean_text(sample)
    if sample:
        return sample[:40]
    return f"Column {column.index + 1}"
