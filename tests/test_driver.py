"""Playwright session control-flow tests using a fake Playwright module."""
from __future__ import annotations

import dataclasses
import unittest

from tests import fake_playwright

from core.browser.driver import (
    BrowserBlocked,
    BrowserError,
    BrowserTimeout,
    BrowserUnavailable,
    PlaywrightSession,
    playwright_available,
)
from core.settings import SETTINGS

_OFFLINE = dataclasses.replace(SETTINGS, robots_policy="ignore")


class DriverTests(unittest.TestCase):
    def tearDown(self):
        fake_playwright.uninstall()

    def _session(self, **kwargs) -> PlaywrightSession:
        fake_playwright.install(**kwargs)
        return PlaywrightSession(settings=_OFFLINE, respect_robots=False, nav_timeout_ms=5_000)

    def test_open_returns_rendered_page(self):
        session = self._session(html="<html><body><h1>Agents</h1></body></html>", title="Agents")
        with session:
            page = session.open("https://example.com/agents")
        self.assertEqual(page.status, 200)
        self.assertEqual(page.title, "Agents")
        self.assertIn("Agents", page.html)
        self.assertEqual(page.final_url, "https://example.com/agents")
        self.assertFalse(page.blocked)

    def test_forbidden_raises_blocked(self):
        session = self._session(status=403)
        with session:
            with self.assertRaises(BrowserBlocked) as ctx:
                session.open("https://example.com/agents")
        self.assertIn("403", str(ctx.exception))

    def test_rate_limit_raises_blocked(self):
        session = self._session(status=429)
        with session:
            with self.assertRaises(BrowserBlocked):
                session.open("https://example.com/agents")

    def test_challenge_title_is_blocked(self):
        session = self._session(title="Just a moment...")
        with session:
            with self.assertRaises(BrowserBlocked):
                session.open("https://example.com/agents")

    def test_challenge_body_is_blocked(self):
        session = self._session(html="<html><body>Please verify you are human</body></html>")
        with session:
            with self.assertRaises(BrowserBlocked):
                session.open("https://example.com/agents")

    def test_timeout_is_translated(self):
        session = self._session(goto_error=fake_playwright.FakeTimeoutError("Timeout 5000ms exceeded"))
        with session:
            with self.assertRaises(BrowserTimeout):
                session.open("https://example.com/agents")

    def test_generic_navigation_error_is_wrapped(self):
        session = self._session(goto_error=RuntimeError("net::ERR_NAME_NOT_RESOLVED"))
        with session:
            with self.assertRaises(BrowserError) as ctx:
                session.open("https://nope.invalid/agents")
        self.assertIn("resolved", str(ctx.exception).lower())

    def test_click_and_scroll(self):
        page, _module = fake_playwright.install()
        session = PlaywrightSession(settings=_OFFLINE, respect_robots=False)
        with session:
            session.open("https://example.com/agents")
            self.assertTrue(session.click("a.next"))
            session.scroll_to_bottom(steps=3)
        self.assertEqual(page.clicks, ["a.next"])
        self.assertGreaterEqual(len(page.waits), 1)
        self.assertEqual(page.default_timeout, 30_000)

    def test_click_failure_returns_false(self):
        fake_playwright.install(click_raises=True)
        session = PlaywrightSession(settings=_OFFLINE, respect_robots=False)
        with session:
            session.open("https://example.com/agents")
            self.assertFalse(session.click("a.next"))

    def test_close_resets_session(self):
        page, module = fake_playwright.install()
        session = PlaywrightSession(settings=_OFFLINE, respect_robots=False)
        session.start()
        session.close()
        self.assertIsNone(session._page)
        self.assertTrue(page._manager.stopped)

    def test_missing_playwright_reports_install_help(self):
        fake_playwright.uninstall()
        if playwright_available():
            self.skipTest("Playwright is genuinely installed in this environment")
        session = PlaywrightSession(settings=_OFFLINE, respect_robots=False)
        with self.assertRaises(BrowserUnavailable) as ctx:
            session.start()
        self.assertIn("playwright install chromium", str(ctx.exception))

    def test_launch_failure_is_explained(self):
        session = self._session(launch_error=RuntimeError("Executable doesn't exist"))
        with self.assertRaises(BrowserUnavailable) as ctx:
            session.start()
        self.assertIn("browser could not start", str(ctx.exception).lower() + " browser could not start")


if __name__ == "__main__":
    unittest.main()
