"""Pagination and page interaction.

Six navigation styles, none assumed: next button, numbered links, numbered
buttons, query-parameter pages, load-more, and infinite scroll. Every walker is
bounded, waits for the content to actually change, and reports a precise reason
when it stops. A run that covered fewer pages than it should is never presented
as complete.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import bs4

from ..inspect_site import parse_html
from ..normalize import clean_text
from .driver import BrowserBlocked

_NEXT_TEXTS = {
    "next", "next page", "next ›", "next »", "›", "»", "next >", ">", "→", "next →",
}
_LOAD_MORE = re.compile(r"\b(load|show|view|see)\s+(more|additional)\b", re.I)
_SCROLL_HINTS = re.compile(r"(infinite[-_ ]?scroll|infinite-scroll-component|sentinel)", re.I)
_PAGE_PARAMS = ("page", "p", "pg", "paged", "page_num", "pagenumber", "pageindex", "start", "offset")

MAX_PAGES_DEFAULT = 20
CHANGE_TIMEOUT_MS = 8_000
POLL_MS = 300

try:
    from soupsieve import escape as _css_escape
except Exception:  # pragma: no cover
    def _css_escape(value: str) -> str:
        return re.sub(r"([^a-zA-Z0-9_-])", r"\\\1", value)


@dataclass
class PaginationPlan:
    strategy: str = "none"            # next_button | numbered | numbered_buttons | query_param | load_more | infinite_scroll | none
    selector: str = ""
    selector_candidates: list[str] = field(default_factory=list)
    text: str = ""
    page_urls: list[str] = field(default_factory=list)
    text_sequence: list[str] = field(default_factory=list)
    param: str = ""
    param_step: int = 1
    max_pages: int = MAX_PAGES_DEFAULT
    reason: str = ""

    def selectors(self) -> list[str]:
        return list(dict.fromkeys([s for s in [self.selector, *self.selector_candidates] if s]))

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "selector": self.selector,
            "text": self.text,
            "page_urls": self.page_urls[:10],
            "page_count": len(self.page_urls),
            "param": self.param,
            "param_step": self.param_step,
            "reason": self.reason,
        }


@dataclass
class WalkStats:
    pages: int = 0
    stopped_reason: str = ""
    warnings: list[str] = field(default_factory=list)
    clicked: int = 0
    scrolled: int = 0


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def _css_for(tag: bs4.Tag) -> str:
    name = tag.name or "a"
    if tag.get("id"):
        return f"#{_css_escape(str(tag['id']))}"
    classes = [c for c in (tag.get("class") or []) if isinstance(c, str) and c]
    if classes:
        return name + "." + ".".join(_css_escape(c) for c in classes[:2])
    label = tag.get("aria-label")
    if label:
        return f'{name}[aria-label="{str(label).replace(chr(34), "")}"]'
    return name


def _next_candidates(tag: bs4.Tag) -> list[str]:
    candidates = [_css_for(tag)]
    text = clean_text(tag.get_text(" "))
    if text:
        candidates.append(f"{tag.name or 'a'}:has-text(\"{text[:40]}\")")
    label = tag.get("aria-label")
    if label:
        candidates.append(f'{tag.name or "a"}[aria-label="{str(label)}"]')
    return candidates


def _page_url(base_url: str, param: str, number: int) -> str:
    parts = urlparse(base_url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() != param.lower()]
    query.append((param, str(number)))
    return urlunparse((parts.scheme, parts.netloc, parts.path, "", urlencode(query), ""))


def detect_pagination(html: str, base_url: str = "", row_selector: str = "") -> PaginationPlan:
    soup = parse_html(html)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # 1. Load-more controls win: they are explicit and unambiguous.
    for el in soup.find_all(["button", "a"]):
        text = clean_text(el.get_text(" "))
        attrs = " ".join(f"{k}={v}" for k, v in el.attrs.items() if isinstance(v, str)).lower()
        if (_LOAD_MORE.search(text) or "load-more" in attrs or "loadmore" in attrs
                or "show-more" in attrs):
            return PaginationPlan(
                strategy="load_more",
                selector=_css_for(el),
                selector_candidates=_next_candidates(el),
                text=text,
                reason=f"Found a 'load more' control labelled “{text or 'button'}”.",
            )

    # 2. rel=next.
    for link in soup.find_all("a", href=True):
        rel = link.get("rel") or []
        if isinstance(rel, str):
            rel = [rel]
        if any(str(r).lower() == "next" for r in rel):
            return PaginationPlan(
                strategy="next_button",
                selector=_css_for(link),
                selector_candidates=_next_candidates(link),
                text=clean_text(link.get_text(" ")),
                reason="Found a next-page link (rel=next).",
            )

    # 3. Text- or aria-labelled next.
    for link in soup.find_all(["a", "button"]):
        text = clean_text(link.get_text(" ")).lower()
        label = str(link.get("aria-label") or "").lower()
        if text in _NEXT_TEXTS or label in _NEXT_TEXTS:
            return PaginationPlan(
                strategy="next_button",
                selector=_css_for(link),
                selector_candidates=_next_candidates(link),
                text=clean_text(link.get_text(" ")),
                reason=f"Found a next-page control labelled “{text or label}”.",
            )

    # 4. Numbered links grouped in one pager. Paths can differ per page
    # (/agents/page/2), so group by parent element.
    by_parent: dict[int, dict[int, str]] = {}
    for link in soup.find_all("a", href=True):
        text = clean_text(link.get_text(" "))
        if not re.fullmatch(r"\d{1,3}", text):
            continue
        number = int(text)
        if not (1 <= number <= 500):
            continue
        parent = link.parent
        target = urljoin(base_url, link["href"])
        by_parent.setdefault(id(parent), {}).setdefault(number, target)
    pager = max(by_parent.values(), key=len, default={})
    if len(pager) >= 2:
        urls = [pager[n] for n in sorted(pager) if n > 1]
        if urls:
            return PaginationPlan(
                strategy="numbered",
                page_urls=urls,
                reason=f"Found numbered pagination ({len(pager)} links).",
            )

    # 5. Numbered buttons (React pagers often use <button>2</button>).
    button_groups: dict[int, list[bs4.Tag]] = {}
    for button in soup.find_all("button"):
        text = clean_text(button.get_text(" "))
        if re.fullmatch(r"\d{1,3}", text) and 2 <= int(text) <= 500:
            button_groups.setdefault(id(button.parent), []).append(button)
    buttons = max(button_groups.values(), key=len, default=[])
    if len(buttons) >= 2:
        return PaginationPlan(
            strategy="numbered_buttons",
            selector=_css_for(buttons[0]),
            selector_candidates=[_css_for(button) for button in buttons[:10]],
            text_sequence=[clean_text(button.get_text(" ")) for button in buttons[:10]],
            reason=f"Found {len(buttons)} numbered pagination buttons.",
        )

    # 6. Query-parameter pagination, including a page parameter already in the URL.
    params: dict[str, int] = {}
    values_by_param: dict[str, set[int]] = {}

    def note_query(query: str, weight: int = 1) -> None:
        for key, value in parse_qsl(query, keep_blank_values=True):
            key = key.lower()
            if key in _PAGE_PARAMS:
                params[key] = params.get(key, 0) + weight
                if value.isdigit():
                    values_by_param.setdefault(key, set()).add(int(value))

    for link in soup.find_all("a", href=True):
        note_query(urlparse(urljoin(base_url, link["href"])).query)
    note_query(urlparse(base_url).query, weight=3)
    if params:
        param = max(params, key=params.get)
        step = 1
        if param in ("start", "offset"):
            values = sorted(v for v in values_by_param.get(param, set()) if v >= 0)
            gaps = [b - a for a, b in zip(values, values[1:]) if b > a]
            if gaps:
                step = min(gaps)
        return PaginationPlan(
            strategy="query_param",
            param=param,
            param_step=max(1, step),
            reason=f"Pagination uses the “{param}” URL parameter.",
        )

    # 7. Infinite scroll: a scroll sentinel, or a populated page with no controls.
    #    A static page costs one extra scroll and then stops, so this is a cheap
    #    guess to make rather than skipping scroll-loaded sites entirely.
    if _SCROLL_HINTS.search(str(soup)[:200_000]):
        return PaginationPlan(
            strategy="infinite_scroll",
            reason="Found a scroll sentinel; the listing is probably loaded as you scroll.",
        )

    if row_selector:
        try:
            rows = len(soup.select(row_selector))
        except Exception:
            rows = 0
        if rows >= 5:
            return PaginationPlan(
                strategy="infinite_scroll",
                reason="No pagination controls found, but the page lists records. Trying scroll-to-load.",
            )

    return PaginationPlan(strategy="none", reason="No pagination controls were detected.")


# ---------------------------------------------------------------------------
# Walking
# ---------------------------------------------------------------------------
PageHandler = Callable[[str, str], list[dict]]
ProgressFn = Callable[[str, int, int], None]


def _noop(stage: str, done: int, total: int) -> None:
    return None


def _signature(session: Any, row_selector: str) -> str:
    from .js import CONTENT_SIGNATURE_JS

    if not row_selector:
        return ""
    value = session.evaluate(CONTENT_SIGNATURE_JS, row_selector)
    return str(value or "")


def _wait_for_change(session: Any, row_selector: str, before: str,
                     timeout_ms: int = CHANGE_TIMEOUT_MS) -> bool:
    waited = 0
    while waited < timeout_ms:
        session.wait(POLL_MS)
        waited += POLL_MS
        if _signature(session, row_selector) != before:
            return True
    return False


def _collect(session: Any, handler: PageHandler, seen: set[str]) -> list[dict]:
    html = session.content()
    url = session.current_url()
    records = handler(html, url) or []
    fresh: list[dict] = []
    for record in records:
        key = str(record.get("profile_url") or record.get("_key") or "") or repr(sorted(record.items()))
        if key in seen:
            continue
        seen.add(key)
        fresh.append(record)
    return fresh


def _click(session: Any, plan: PaginationPlan) -> bool:
    for selector in plan.selectors():
        if session.click(selector):
            return True
    click_text = getattr(session, "click_text", None)
    if click_text and plan.text:
        try:
            if click_text(plan.text):
                return True
        except Exception:
            pass
    return False


def _walk_direct(
    session: Any,
    urls: list[str],
    handler: PageHandler,
    seen: set[str],
    collected: list[dict],
    stats: WalkStats,
    limit_pages: int,
    max_records: int,
    progress: ProgressFn,
) -> None:
    for index, target in enumerate(urls[: max(0, limit_pages - 1)], start=2):
        if len(collected) >= max_records:
            stats.stopped_reason = f"Stopped at the {max_records}-record cap."
            break
        progress(f"Opening page {index}", index - 1, limit_pages)
        try:
            session.open(target)
        except BrowserBlocked:
            raise
        except Exception as exc:
            stats.warnings.append(f"Page {index} could not be opened: {exc}")
            break
        before_count = len(collected)
        collected.extend(_collect(session, handler, seen))
        stats.pages += 1
        if len(collected) == before_count:
            stats.warnings.append(
                f"Page {index} returned no new records; stopping to avoid a loop."
            )
            break


def walk_pages(
    session: Any,
    plan: PaginationPlan,
    handler: PageHandler,
    max_pages: int = MAX_PAGES_DEFAULT,
    max_records: int = 1000,
    row_selector: str = "",
    progress: ProgressFn | None = None,
    delay_ms: int = 0,
) -> tuple[list[dict], WalkStats]:
    """Drive the session through the listing and collect records page by page."""
    progress = progress or _noop
    stats = WalkStats()
    collected: list[dict] = []
    seen: set[str] = set()
    limit_pages = max(1, min(max_pages, plan.max_pages or max_pages))

    # First page is already open.
    progress("Reading records on page 1", 0, limit_pages)
    first = _collect(session, handler, seen)
    collected.extend(first)
    stats.pages = 1

    if plan.strategy in ("none", ""):
        stats.stopped_reason = "This page has no pagination; only the visible records were collected."
        return collected, stats

    if plan.strategy == "numbered" and plan.page_urls:
        _walk_direct(session, plan.page_urls, handler, seen, collected, stats,
                     limit_pages, max_records, progress)
        stats.stopped_reason = f"Walked {stats.pages} numbered pages."
        return collected, stats

    if plan.strategy == "query_param" and plan.param:
        current = urlparse(session.current_url() or "")
        current_value = 0 if plan.param.lower() in ("start", "offset") else 1
        for part in current.query.split("&"):
            key, _, value = part.partition("=")
            if key.lower() == plan.param.lower() and value.isdigit():
                current_value = int(value)
                break
        step = max(1, int(plan.param_step or 1))
        urls = [
            _page_url(session.current_url(), plan.param, current_value + step * offset)
            for offset in range(1, limit_pages)
        ]
        _walk_direct(session, urls, handler, seen, collected, stats,
                     limit_pages, max_records, progress)
        stats.stopped_reason = f"Walked {stats.pages} query-parameter pages."
        return collected, stats

    if plan.strategy == "numbered_buttons" and plan.text_sequence:
        click_text = getattr(session, "click_text", None)
        for number in plan.text_sequence:
            if stats.pages >= limit_pages or len(collected) >= max_records:
                break
            before = _signature(session, row_selector)
            progress(f"Opening page {stats.pages + 1}", stats.pages, limit_pages)
            clicked = False
            if click_text:
                try:
                    clicked = bool(click_text(number))
                except Exception:
                    clicked = False
            if not clicked:
                clicked = session.click(plan.selector)
            if not clicked:
                break
            stats.clicked += 1
            if not _wait_for_change(session, row_selector, before):
                break
            added = _collect(session, handler, seen)
            if not added:
                break
            collected.extend(added)
            stats.pages += 1
        stats.stopped_reason = f"Walked {stats.pages} numbered pages."
        return collected, stats

    if plan.strategy in ("next_button", "load_more") and not plan.selectors() and not plan.text:
        stats.stopped_reason = "A pagination control was detected but its selector could not be resolved."
        return collected, stats

    while stats.pages < limit_pages and len(collected) < max_records:
        before = _signature(session, row_selector)

        if plan.strategy == "infinite_scroll":
            progress(f"Scrolling for more records ({len(collected)} collected)", stats.pages, limit_pages)
            result = session.scroll_to_bottom()
            stats.scrolled += 1
            if int(result.get("steps") or 0) <= 1:
                stats.stopped_reason = "The page stopped growing while scrolling."
                break
        else:
            progress(f"Loading page {stats.pages + 1}", stats.pages, limit_pages)
            if not _click(session, plan):
                stats.stopped_reason = (
                    "The pagination control is no longer present or could not be clicked, "
                    "which usually means the last page was reached."
                )
                break
            stats.clicked += 1

        if delay_ms:
            session.wait(delay_ms)

        changed = _wait_for_change(session, row_selector, before)
        if not changed:
            stats.stopped_reason = (
                "The page did not change after the pagination action. It may be the last page, "
                "or the control may be disabled."
            )
            break

        added = _collect(session, handler, seen)
        if not added:
            stats.stopped_reason = "The next page produced no new records; stopping to avoid a loop."
            break
        collected.extend(added)
        stats.pages += 1

    if len(collected) >= max_records:
        stats.stopped_reason = f"Stopped at the {max_records}-record cap."
    if not stats.stopped_reason:
        stats.stopped_reason = f"Reached the {limit_pages}-page limit."
    return collected, stats
