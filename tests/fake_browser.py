"""A scripted browser session for offline tests.

It implements the same interface as `PlaywrightSession`, but instead of a real
browser it walks a list of pre-rendered HTML documents. That lets the extraction,
pagination and field-mapping logic be tested end to end without Playwright.

Important honesty note: `sequence` entries represent the DOM *after* JavaScript has
run. This harness proves the pipeline handles a rendered DOM; it does not prove
that a real browser executes a given site's JavaScript. Only a live run does that.
"""
from __future__ import annotations

from core.browser.driver import BrowserBlocked, RenderedPage
from core.browser.js import CONTENT_SIGNATURE_JS
from core.inspect_site import parse_html


def _signature(html: str, row_selector: str) -> str:
    soup = parse_html(html)
    try:
        rows = soup.select(row_selector)
    except Exception:
        return ""
    texts = [" ".join(r.get_text(" ", strip=True).split())[:120] for r in rows[:12]]
    return f"{len(rows)}|" + "||".join(texts)


class FakeBrowserSession:
    def __init__(
        self,
        sequence: list[str],
        start_url: str = "https://cedarandoak.example/agents",
        by_url: dict[str, str] | None = None,
        blocked: bool = False,
        blocked_message: str = "antibot",
        title: str = "Agents",
        status: int = 200,
    ) -> None:
        self.sequence = sequence
        self.start_url = start_url
        self.by_url = by_url or {}
        self.blocked = blocked
        self.blocked_message = blocked_message
        self.title = title
        self.status = status
        self.index = 0
        self._url = start_url
        self._html = sequence[0] if sequence else ""
        self.clicks = 0
        self.scrolls = 0
        self.opened: list[str] = []
        self.closed = False

    # -- protocol ----------------------------------------------------------
    def open(self, url: str) -> RenderedPage:
        self.opened.append(url)
        if self.blocked:
            raise BrowserBlocked(self.blocked_message)
        if url in self.by_url:
            self._html = self.by_url[url]
            self._url = url
            self.index = -1
            return RenderedPage(url=url, final_url=url, title=self.title,
                                status=self.status, html=self._html)
        self._url = url
        self.index = 0
        self._html = self.sequence[0] if self.sequence else ""
        return RenderedPage(url=url, final_url=url, title=self.title,
                            status=self.status, html=self._html)

    def content(self) -> str:
        return self._html

    def current_url(self) -> str:
        return self._url

    def click(self, selector: str, timeout_ms: int | None = None) -> bool:
        self.clicks += 1
        if self.index < 0 or self.index + 1 >= len(self.sequence):
            return False
        self.index += 1
        self._html = self.sequence[self.index]
        return True

    def evaluate(self, script: str, arg=None):
        if script.strip() == CONTENT_SIGNATURE_JS.strip():
            return _signature(self._html, str(arg or ""))
        return None

    def scroll_to_bottom(self, steps: int = 10, pause_ms: int = 250) -> dict:
        self.scrolls += 1
        if self.index < 0 or self.index + 1 >= len(self.sequence):
            return {"height": 0, "steps": 1}
        self.index += 1
        self._html = self.sequence[self.index]
        return {"height": 1000 * (self.index + 1), "steps": 2}

    def wait(self, ms: int) -> None:
        return None

    def reload(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True
