"""A minimal stand-in for the Playwright sync API.

Used to exercise `PlaywrightSession` control flow without installing a browser:
navigation, status handling, challenge detection, timeouts, clicks and cleanup.
"""
from __future__ import annotations

import sys
import types


class FakeTimeoutError(Exception):
    pass


class FakeResponse:
    def __init__(self, status: int = 200, url: str = "") -> None:
        self.status = status
        self.url = url


class FakeLocator:
    def __init__(self, page: "FakePage", selector: str) -> None:
        self.page = page
        self.selector = selector
        self.first = self

    def click(self, timeout: int | None = None) -> None:
        if self.page.click_raises:
            raise FakeTimeoutError("click timed out")
        self.page.clicks.append(self.selector)


class FakePage:
    def __init__(
        self,
        html: str = "<html><body>ok</body></html>",
        title: str = "Test page",
        status: int = 200,
        goto_error: Exception | None = None,
        click_raises: bool = False,
    ) -> None:
        self._html = html
        self._title = title
        self.status = status
        self.goto_error = goto_error
        self.click_raises = click_raises
        self.url = ""
        self.clicks: list[str] = []
        self.evaluated: list[str] = []
        self.waits: list[int] = []
        self.reloads = 0
        self.default_timeout = None
        self.closed = False

    # -- API used by PlaywrightSession ------------------------------------
    def goto(self, url: str, wait_until: str = "load", timeout: int | None = None):
        if self.goto_error:
            raise self.goto_error
        self.url = url
        return FakeResponse(self.status, url)

    def wait_for_load_state(self, state: str, timeout: int | None = None) -> None:
        return None

    def content(self) -> str:
        return self._html

    def title(self) -> str:
        return self._title

    def evaluate(self, script, arg=None):
        self.evaluated.append(str(script)[:40])
        if "scrollingElement" in str(script):
            return {"height": 2000 + len(self.clicks) * 500, "y": 0}
        return None

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def set_default_timeout(self, timeout: int) -> None:
        self.default_timeout = timeout

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)

    def reload(self, wait_until: str = "load", timeout: int | None = None) -> None:
        self.reloads += 1


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.closed = False

    def new_page(self) -> FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.closed = False

    def new_context(self, **kwargs) -> FakeContext:
        self.context_kwargs = kwargs
        return FakeContext(self.page)

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, page: FakePage, launch_error: Exception | None = None) -> None:
        self.page = page
        self.launch_error = launch_error

    def launch(self, headless: bool = True) -> FakeBrowser:
        if self.launch_error:
            raise self.launch_error
        return FakeBrowser(self.page)


class _PlaywrightManager:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium
        self.stopped = False

    def start(self):
        return self

    def stop(self) -> None:
        self.stopped = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()
        return False


def install(
    html: str = "<html><body>ok</body></html>",
    title: str = "Test page",
    status: int = 200,
    goto_error: Exception | None = None,
    launch_error: Exception | None = None,
    click_raises: bool = False,
) -> tuple[FakePage, types.ModuleType]:
    """Inject a fake `playwright.sync_api` into sys.modules. Returns (page, module)."""
    page = FakePage(html=html, title=title, status=status,
                    goto_error=goto_error, click_raises=click_raises)
    chromium = FakeChromium(page, launch_error=launch_error)
    manager = _PlaywrightManager(chromium)

    package = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: manager
    sync_api.TimeoutError = FakeTimeoutError
    sync_api.Error = Exception
    package.sync_api = sync_api

    sys.modules["playwright"] = package
    sys.modules["playwright.sync_api"] = sync_api
    page._manager = manager  # type: ignore[attr-defined]
    return page, package


def uninstall() -> None:
    sys.modules.pop("playwright.sync_api", None)
    sys.modules.pop("playwright", None)
