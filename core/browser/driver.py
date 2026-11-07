"""Playwright session wrapper.

The rest of the application talks to `BrowserSession` and never to Playwright, so
the engine can be tested with a fixture driver and a different browser backend can
be added later without touching extraction code.

Responsible-use posture, enforced here rather than documented and forgotten:
  * robots.txt is checked before navigation,
  * access-denied and rate-limit responses stop the run instead of being retried,
  * challenge pages (Cloudflare, CAPTCHA, "verify you are human") raise an error,
  * no automation-hiding flags, stealth patches or CAPTCHA solving are used.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..net import PoliteFetcher, UnsafeURLError, assert_public_url
from ..settings import SETTINGS, Settings

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Short wait used by wait_for_rows; defined here so the driver has no pagination
# import cycle.
POLL_MS = 300

_BLOCK_TITLES = re.compile(
    r"(just a moment|attention required|access denied|are you a robot|security check|"
    r"pardon our interruption|request blocked|too many requests)",
    re.I,
)
_BLOCK_BODY = re.compile(
    r"(verify you are human|checking your browser|enable javascript and cookies to continue|"
    r"unusual traffic from your computer|cf-error-details|cf-browser-verification|"
    r"please complete the security check|ddos protection by)",
    re.I,
)

INSTALL_HELP = """\
Playwright is not available in this environment.

Install it once, then download the browser:

    pip install playwright
    python -m playwright install chromium

On a fresh Linux server you may also need:

    python -m playwright install-deps chromium
"""


class BrowserError(RuntimeError):
    """Base class for browser failures with an operator-readable message."""


class BrowserUnavailable(BrowserError):
    """Playwright or its browser binary is not installed."""


class BrowserBlocked(BrowserError):
    """The site refused access or served an anti-bot challenge. Never auto-retried."""


class BrowserTimeout(BrowserError):
    """A navigation or interaction exceeded its time budget."""


@dataclass
class RenderedPage:
    url: str
    final_url: str
    title: str
    status: int
    html: str
    blocked: bool = False
    block_reason: str = ""
    robots_allowed: bool = True
    robots_detail: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class BrowserSession(Protocol):
    def open(self, url: str) -> RenderedPage: ...
    def content(self) -> str: ...
    def current_url(self) -> str: ...
    def click(self, selector: str, timeout_ms: int | None = None) -> bool: ...
    def evaluate(self, script: str, arg: Any = None) -> Any: ...
    def scroll_to_bottom(self, steps: int = 10, pause_ms: int = 250) -> dict[str, Any]: ...
    def wait(self, ms: int) -> None: ...
    def reload(self) -> None: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------
def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return True


def diagnose() -> tuple[bool, str]:
    """Try to launch a browser once. Returns (ok, message)."""
    if not playwright_available():
        return False, INSTALL_HELP
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
        return True, "Playwright and Chromium are ready."
    except Exception as exc:
        return False, (
            f"Playwright is installed but the browser could not start: {exc}\n\n"
            "Run:  python -m playwright install chromium"
        )


# ---------------------------------------------------------------------------
# Playwright implementation
# ---------------------------------------------------------------------------
class PlaywrightSession:
    """A single browser context, opened on demand and always closed."""

    def __init__(
        self,
        settings: Settings = SETTINGS,
        headless: bool | None = None,
        respect_robots: bool | None = None,
        nav_timeout_ms: int | None = None,
        idle_timeout_ms: int | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        robots_fetcher: PoliteFetcher | None = None,
        block_media: bool | None = None,
        reveal_contacts: bool | None = None,
    ) -> None:
        self.settings = settings
        self.headless = settings.browser_headless if headless is None else headless
        self.respect_robots = settings.robots_enforced if respect_robots is None else respect_robots
        self.nav_timeout_ms = nav_timeout_ms or settings.browser_nav_timeout_ms
        self.idle_timeout_ms = idle_timeout_ms or settings.browser_idle_timeout_ms
        self.user_agent = user_agent
        self.block_media = settings.browser_block_media if block_media is None else block_media
        self.reveal_contacts_enabled = (
            settings.browser_reveal_contacts if reveal_contacts is None else reveal_contacts
        )
        # One fetcher per session so robots.txt is cached per host instead of being
        # downloaded again for every profile page.
        self._robots_fetcher = robots_fetcher or PoliteFetcher(settings)
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self.pages_opened = 0
        self.last_robots_allowed = True
        self.last_robots_detail = ""

    # -- lifecycle ----------------------------------------------------------
    def __enter__(self) -> "PlaywrightSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        if self._page is not None:
            return
        if not playwright_available():
            raise BrowserUnavailable(INSTALL_HELP)
        try:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context(
                user_agent=self.user_agent,
                viewport={"width": 1440, "height": 900},
                locale="en-US",
                java_script_enabled=True,
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            self._page = self._context.new_page()
            self._page.set_default_timeout(self.nav_timeout_ms)
            try:
                self._page.set_default_navigation_timeout(self.nav_timeout_ms)
            except Exception:
                pass
            try:
                self._page.on("dialog", lambda dialog: dialog.dismiss())
            except Exception:
                pass
            if self.block_media:
                try:
                    self._page.route(
                        "**/*",
                        lambda route: route.abort()
                        if route.request.resource_type in {"image", "media", "font"}
                        else route.continue_(),
                    )
                except Exception:
                    pass
        except BrowserUnavailable:
            raise
        except Exception as exc:
            self.close()
            raise BrowserUnavailable(f"Could not start the browser: {exc}\n\n{INSTALL_HELP}") from exc

    def close(self) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._context = self._page = None

    # -- robots -------------------------------------------------------------
    def _check_robots(self, url: str) -> tuple[bool, str]:
        """Return (allowed, detail). Only enforce when the operator asked us to."""
        if self.settings.robots_silent:
            return True, "robots.txt not checked (policy: ignore)"
        fetcher = self._robots_fetcher
        allowed, detail = fetcher.can_fetch(url)
        if not allowed and self.respect_robots:
            raise BrowserBlocked(
                f"robots.txt on {url} disallows automated access ({detail}). "
                "Choose a permitted page, set B2B_ROBOTS_POLICY=warn to review the "
                "restriction, or obtain written permission from the site owner."
            )
        return allowed, detail

    # -- navigation ---------------------------------------------------------
    def open(self, url: str) -> RenderedPage:
        if self._page is None:
            self.start()
        try:
            assert_public_url(url, allow_local=self.settings.allow_local_urls)
        except UnsafeURLError as exc:
            raise BrowserError(str(exc)) from exc
        allowed, detail = self._check_robots(url)
        self.last_robots_allowed = allowed
        self.last_robots_detail = detail

        try:
            response = self._page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms)
        except Exception as exc:
            raise self._translate(exc, url) from exc

        self.pages_opened += 1
        status = int(getattr(response, "status", 0) or 0) if response is not None else 0
        try:
            assert_public_url(self._page.url, allow_local=self.settings.allow_local_urls)
        except UnsafeURLError as exc:
            raise BrowserError(str(exc)) from exc

        # Give client-side rendering time, but never wait forever on a page that
        # keeps polling.
        try:
            self._page.wait_for_load_state("networkidle", timeout=self.idle_timeout_ms)
        except Exception:
            pass

        self._dismiss_overlays()

        html = self._page.content()
        title = self._safe_title()
        page = RenderedPage(
            url=url,
            final_url=self._page.url,
            title=title,
            status=status,
            html=html,
            robots_allowed=allowed,
            robots_detail=detail,
        )
        self._detect_block(page)
        return page

    def _safe_title(self) -> str:
        try:
            return self._page.title() or ""
        except Exception:
            return ""

    def _detect_block(self, page: RenderedPage) -> None:
        if page.status in (401, 403, 429):
            page.blocked = True
            page.block_reason = f"HTTP {page.status}"
            raise BrowserBlocked(
                f"The site refused access (HTTP {page.status}). Stopping. "
                "Do not retry in a loop; check the site's terms and your permission to collect."
            )
        if page.status == 404:
            raise BrowserError(f"That page was not found (HTTP 404): {page.final_url or page.url}")
        if page.status >= 500:
            raise BrowserError(f"The site returned a server error (HTTP {page.status}). Try later.")

        # Challenge detection looks at the title always, and at the body only when
        # the page is short. A long article that merely mentions "verify you are
        # human" must not be mistaken for an anti-bot wall.
        visible_text = re.sub(r"<[^>]+>", " ", page.html or "")
        visible_text = re.sub(r"\s+", " ", visible_text).strip()
        haystack = f"{page.title}\n{visible_text[:8000]}"
        challenge_body = _BLOCK_BODY.search(haystack) and (len(visible_text) < 15_000 or page.status == 403)
        if _BLOCK_TITLES.search(page.title or "") or challenge_body:
            page.blocked = True
            page.block_reason = "anti-bot challenge"
            raise BrowserBlocked(
                "The site served an anti-bot challenge or access-denied page. "
                "This tool does not bypass those protections. Use a permitted source instead."
            )

    def _dismiss_overlays(self) -> None:
        from .js import DISMISS_OVERLAYS_JS

        try:
            self._page.evaluate(DISMISS_OVERLAYS_JS)
        except Exception:
            pass

    # -- interaction --------------------------------------------------------
    def click(self, selector: str, timeout_ms: int | None = None) -> bool:
        if self._page is None:
            return False
        try:
            locator = self._page.locator(selector).first
            locator.click(timeout=timeout_ms or 8_000)
            self._settle()
            return True
        except Exception:
            return False

    def _settle(self) -> None:
        try:
            self._page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            pass

    def evaluate(self, script: str, arg: Any = None) -> Any:
        if self._page is None:
            return None
        try:
            return self._page.evaluate(script, arg)
        except Exception:
            return None

    def scroll_to_bottom(self, steps: int = 10, pause_ms: int = 250) -> dict[str, Any]:
        from .js import SCROLL_BOTTOM_JS

        steps = max(1, int(steps))
        last_height = -1
        stable = 0
        used = 0
        for used in range(1, steps + 1):
            metrics = self.evaluate(SCROLL_BOTTOM_JS) or {}
            height = int(metrics.get("height") or 0)
            self.wait(pause_ms)
            if height == last_height:
                stable += 1
                if stable >= 2:
                    break
            else:
                stable = 0
            last_height = height
        return {"height": last_height, "steps": used}

    def auto_scroll(self, steps: int | None = None, pause_ms: int = 200) -> dict[str, Any]:
        """Scroll through the page to trigger lazy-loaded rows, then return to top."""
        from .js import SCROLL_TOP_JS

        result = self.scroll_to_bottom(steps=steps or self.settings.browser_scroll_steps,
                                       pause_ms=pause_ms)
        self.evaluate(SCROLL_TOP_JS)
        return result

    def click_text(self, text: str, timeout_ms: int | None = None) -> bool:
        """Click a control by its visible text (for pagers without stable CSS)."""
        if self._page is None or not text:
            return False
        locator = None
        try:
            locator = self._page.get_by_text(text, exact=True).first
        except Exception:
            try:
                escaped = str(text).replace('"', '\\"')
                locator = self._page.locator(f'text="{escaped}"').first
            except Exception:
                return False
        try:
            locator.click(timeout=timeout_ms or 8_000)
            self._settle()
            return True
        except Exception:
            return False

    def reveal_contacts(self) -> int:
        """Click ordinary 'show phone/email' controls so rendered contacts appear."""
        if not self.reveal_contacts_enabled or self._page is None:
            return 0
        from .js import REVEAL_CONTACT_JS

        clicked = self.evaluate(REVEAL_CONTACT_JS)
        try:
            clicked = int(clicked or 0)
        except (TypeError, ValueError):
            clicked = 0
        if clicked:
            self.wait(600)
        return clicked

    def reveal_listing_contacts(self) -> int:
        """Reveal per-card phone/email controls on a listing page."""
        if not self.reveal_contacts_enabled or self._page is None:
            return 0
        from .js import REVEAL_LISTING_CONTACT_JS

        clicked = self.evaluate(REVEAL_LISTING_CONTACT_JS)
        try:
            clicked = int(clicked or 0)
        except (TypeError, ValueError):
            clicked = 0
        if clicked:
            self.wait(900)
        return clicked

    def count_rows(self, selector: str) -> int:
        from .js import COUNT_ROWS_JS

        try:
            return int(self.evaluate(COUNT_ROWS_JS, selector) or 0)
        except (TypeError, ValueError):
            return 0

    def wait_for_rows(self, selector: str, min_count: int = 1, timeout_ms: int = 8_000) -> int:
        waited = 0
        count = 0
        while waited < timeout_ms:
            count = self.count_rows(selector)
            if count >= min_count:
                return count
            self.wait(POLL_MS)
            waited += POLL_MS
        return count

    def screenshot(self, path: str) -> bool:
        if self._page is None:
            return False
        try:
            self._page.screenshot(path=path, full_page=True)
            return True
        except Exception:
            return False

    def wait(self, ms: int) -> None:
        try:
            self._page.wait_for_timeout(ms)
        except Exception:
            pass

    def reload(self) -> None:
        try:
            self._page.reload(wait_until="domcontentloaded", timeout=self.nav_timeout_ms)
            self._settle()
        except Exception:
            pass

    def content(self) -> str:
        try:
            return self._page.content()
        except Exception:
            return ""

    def current_url(self) -> str:
        try:
            return self._page.url
        except Exception:
            return ""

    # -- errors -------------------------------------------------------------
    def _translate(self, exc: Exception, url: str) -> BrowserError:
        name = exc.__class__.__name__
        message = str(exc)
        if "Timeout" in name or "timeout" in message.lower():
            return BrowserTimeout(
                f"The page took longer than {self.nav_timeout_ms // 1000}s to respond ({url}). "
                "The site may be slow or blocking automated browsers."
            )
        if "ERR_NAME_NOT_RESOLVED" in message:
            return BrowserError("That address could not be resolved. Check the URL for typos.")
        if "ERR_CONNECTION" in message:
            return BrowserError("The site refused the connection.")
        if "ERR_ABORTED" in message:
            return BrowserError("The navigation was aborted by the site or a redirect loop.")
        return BrowserError(f"Could not open the page: {name}: {message}")
