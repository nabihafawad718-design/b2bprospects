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

import bs4

from ..inspect_site import parse_html
from ..models import ColumnProfile, StructureModel, signature
from ..normalize import (
    clean_text,
    email_from_fragment,
    find_emails,
    find_phones,
    looks_like_person_name,
    parse_address,
    tel_from_fragment,
)

try:  # soupsieve ships with BeautifulSoup; the fallback keeps the module importable.
    from soupsieve import escape as _css_escape
except Exception:  # pragma: no cover
    def _css_escape(value: str) -> str:
        return re.sub(r"([^a-zA-Z0-9_-])", r"\\\1", value)

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
_TITLE_CASE_NAME = re.compile(r"^[A-Z][A-Za-z'’\-]+(\s+[A-Z](?:[A-Za-z'’\-]*|\.)){1,4}$")
_DOMAIN_TEXT = re.compile(r"^(?:https?://|www\.)\S+\.\S+$", re.I)
_JOB_TITLE = re.compile(
    r"\b(senior|junior|associate broker|broker associate|listing specialist|buyer'?s agent|"
    r"team lead|manager|director|advisor|specialist|coordinator|officer|assistant|"
    r"agent|realtor|salesperson|consultant|partner)\b",
    re.I,
)


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
    for attribute in ("data-phone", "data-telephone", "data-tel", "data-email", "data-address"):
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


def row_cells(row: bs4.Tag, base_url: str = "") -> list[Cell]:
    """Flatten one row into addressable cells.

    For an element with element children, only its *own* text nodes are used. That
    avoids a wrapper `<a>` around a whole card producing a catch-all column that
    duplicates every field, while still capturing mixed content such as
    `<p>Call <span>now</span> (512) 555-0142</p>`.
    """
    cells: list[Cell] = []
    count = 0

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
def _guess_kind(values: list[str], href_rate: float) -> str:
    samples = [v for v in values if v][:8]
    if not samples:
        return "url" if href_rate > 0.8 else "text"

    email_hits = sum(1 for v in samples if find_emails(v, limit=1))
    if email_hits >= max(1, len(samples) // 2):
        return "email"

    phone_hits = sum(1 for v in samples if find_phones(v, limit=1))
    if phone_hits >= max(1, len(samples) // 2):
        return "phone"

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
    # win the name column just because they look like two capitalised words.
    title_hits = sum(1 for v in samples if _JOB_TITLE.search(v) and not _ORG_WORDS.search(v))
    if title_hits >= max(1, len(samples) // 2):
        return "text"

    # Names are frequently links, so this must be decided before the URL rule.
    name_hits = sum(1 for v in samples if looks_like_person_name(v) or _TITLE_CASE_NAME.match(v))
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


def _columns_for(rows: list[bs4.Tag], base_url: str) -> list[ColumnProfile]:
    per_row: list[dict[str, Cell]] = []
    for row in rows:
        per_row.append({cell.path: cell for cell in row_cells(row, base_url)})

    all_paths: set[str] = set()
    for mapping in per_row:
        all_paths.update(mapping)

    def sort_key(path: str):
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
        kind = _guess_kind(texts, href_rate)
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
            )
        )
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


def _score(rows: int, columns: list[ColumnProfile], has_links: bool, parent: bs4.Tag | None = None) -> float:
    kinds = {c.kind_hint for c in columns}
    score = rows * 2.0 + min(len(columns), 10) * 2.5
    score += 7 if kinds & {"phone", "email"} else 0
    score += 5 if "name" in kinds else 0
    score += 4 if kinds & {"phone", "email"} and "name" in kinds else 0
    score += 3 if has_links else 0
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


def analyze_html(html: str, base_url: str = "") -> list[StructureModel]:
    """Return up to five candidate repeated regions, best first."""
    soup = parse_html(html)
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()

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

            columns = _columns_for(rows, base_url)
            if not columns:
                continue

            has_links = any(c.href_rate > 0.5 for c in columns)
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
                    score=round(_score(len(rows), columns, has_links, parent), 2),
                )
            )

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
    soup = parse_html(html)
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


def column_for_click(structure: StructureModel, path: str, text: str = "") -> int | None:
    """Map a visual-picker click (position path + text) to a detected column.

    Exact path first, then the nearest ancestor/descendant path, then the column
    whose sample value contains the clicked text. This is what makes clicking an
    element in the snapshot equivalent to choosing a column by example.
    """
    path = str(path or "").strip(".")
    clicked_text = clean_text(text)

    for column in structure.columns:
        if column.path == path:
            return column.index

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
    sample = column.sample_values[0] if column.sample_values else ""
    sample = clean_text(sample)
    if sample:
        return sample[:40]
    return f"Column {column.index + 1}"
