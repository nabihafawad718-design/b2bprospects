"""Pagination and page interaction.

Five navigation styles, none assumed: next button, numbered links, load-more,
infinite scroll, and plain URL navigation. Every walker is bounded, waits for the
content to actually change, and reports a precise reason when it stops. A run that
covered fewer pages than it should is never presented as complete.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urljoin

import bs4

from ..inspect_site import parse_html
from ..normalize import clean_text

_NEXT_TEXTS = {"next", "next page", "next ›", "next »", "›", "»", "next >", ">"}
_LOAD_MORE = re.compile(r"\b(load|show|view|see)\s+(more|additional)\b", re.I)
_SCROLL_HINTS = re.compile(r"(infinite[-_ ]?scroll|infinite-scroll-component|sentinel)", re.I)

MAX_PAGES_DEFAULT = 20
CHANGE_TIMEOUT_MS = 8_000
POLL_MS = 300


@dataclass
class PaginationPlan:
    strategy: str = "none"            # next_button | numbered | load_more | infinite_scroll | none
    selector: str = ""
    page_urls: list[str] = field(default_factory=list)
    max_pages: int = MAX_PAGES_DEFAULT
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "selector": self.selector,
            "page_urls": self.page_urls[:10],
            "page_count": len(self.page_urls),
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
    classes = [c for c in (tag.get("class") or []) if isinstance(c, str) and c]
    if tag.get("id"):
        return f"#{tag['id']}"
    if classes:
        return name + "." + ".".join(classes[:2])
    return name


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
                reason="Found a next-page link (rel=next).",
            )

    # 3. Text-labelled next.
    for link in soup.find_all(["a", "button"]):
        text = clean_text(link.get_text(" ")).lower()
        if text in _NEXT_TEXTS:
            return PaginationPlan(
                strategy="next_button",
                selector=_css_for(link),
                reason=f"Found a next-page control labelled “{text}”.",
            )

    # 4. Numbered links: collect absolute targets and navigate directly.
    numbered: dict[int, str] = {}
    for link in soup.find_all("a", href=True):
        text = clean_text(link.get_text(" "))
        if re.fullmatch(r"\d{1,4}", text):
            number = int(text)
            if 1 <= number <= 500:
                numbered.setdefault(number, urljoin(base_url, link["href"]))
    if len(numbered) >= 2:
        urls = [numbered[n] for n in sorted(numbered) if n > 1]
        return PaginationPlan(
            strategy="numbered",
            page_urls=urls,
            reason=f"Found numbered pagination ({len(numbered)} links).",
        )

    # 5. Infinite scroll: a scroll sentinel, or a populated page with no controls.
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


def walk_pages(
    session: Any,
    plan: PaginationPlan,
    handler: PageHandler,
    max_pages: int = MAX_PAGES_DEFAULT,
    max_records: int = 1000,
    row_selector: str = "",
    progress: ProgressFn | None = None,
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
        for index, target in enumerate(plan.page_urls[: limit_pages - 1], start=2):
            if len(collected) >= max_records:
                stats.stopped_reason = f"Stopped at the {max_records}-record cap."
                break
            progress(f"Opening page {index}", index - 1, limit_pages)
            try:
                session.open(target)
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
        stats.stopped_reason = f"Walked {stats.pages} numbered pages."
        return collected, stats

    if plan.strategy in ("next_button", "load_more", "infinite_scroll") and not plan.selector \
            and plan.strategy != "infinite_scroll":
        stats.stopped_reason = "A pagination control was detected but its selector could not be resolved."
        return collected, stats

    while stats.pages < limit_pages and len(collected) < max_records:
        before = _signature(session, row_selector)

        if plan.strategy == "infinite_scroll":
            progress(f"Scrolling for more records ({stats.pages} loaded)", stats.pages, limit_pages)
            result = session.scroll_to_bottom()
            stats.scrolled += 1
            if int(result.get("steps") or 0) <= 1:
                stats.stopped_reason = "The page stopped growing while scrolling."
                break
        else:
            progress(f"Loading page {stats.pages + 1}", stats.pages, limit_pages)
            if not session.click(plan.selector):
                stats.stopped_reason = (
                    "The pagination control is no longer present or could not be clicked, "
                    "which usually means the last page was reached."
                )
                break
            stats.clicked += 1

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
