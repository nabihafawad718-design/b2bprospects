"""Render-path smoke tests.

Streamlit is not installed in the build environment, so a strict stand-in is
injected and app.py is executed for each workspace view. This catches import
errors, NameErrors, bad Streamlit kwargs and unpacking mistakes. It does not
replace looking at the app in a browser.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from tests import streamlit_stub
from tests.helpers import ROOT, FakeSite, fixture, jsonld_profile_html

sys.modules.setdefault("streamlit", streamlit_stub)

from core import runs as runs_module  # noqa: E402
from core.engine import run_extraction  # noqa: E402
from core.models import InspectionReport  # noqa: E402

APP_PATH = ROOT / "app.py"
BASE = "https://cedarandoak.example"


def _load_app(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, APP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _site() -> FakeSite:
    routes = {f"{BASE}/agents": fixture("roster_jsonld.html")}
    for slug in ("jane-smith", "marcus-lee"):
        routes[f"{BASE}/agents/{slug}"] = jsonld_profile_html(
            "Jane Smith" if "jane" in slug else "Marcus Lee",
            "+1-512-555-0142", "jane@cedarandoak.example", "Cedar & Oak Realty",
            "3300 Bee Caves Rd", "Austin", "TX", "78746", f"{BASE}/agents/{slug}",
        )
    return FakeSite(routes)


def _fake_result():
    with tempfile.TemporaryDirectory() as tmp:
        original = runs_module.HISTORY_FILE
        runs_module.HISTORY_FILE = Path(tmp) / "history.jsonl"
        try:
            return run_extraction(
                f"{BASE}/agents",
                selected_fields=["agent_name", "brokerage_name", "phone", "email",
                                 "city", "state", "profile_url", "source_url"],
                fetcher=_site().fetcher(),
                max_profiles=2,
                persist=False,
            )
        finally:
            runs_module.HISTORY_FILE = original


def _fake_inspection() -> InspectionReport:
    return InspectionReport(
        url=f"{BASE}/agents",
        final_url=f"{BASE}/agents",
        host="cedarandoak.example",
        status_code=200,
        page_title="Meet Our Agents | Cedar & Oak Realty",
        jsonld_blocks=1,
        jsonld_types=["ItemList"],
        container_selector="div.agent-card",
        container_signature="abc123",
        container_count=6,
        profile_link_count=6,
        pagination_type="next_link",
        pagination_pages=1,
        available_fields=["agent_name", "phone", "email", "city", "state",
                          "profile_url", "source_url", "brokerage_name"],
        recommended_adapter="generic_jsonld",
        robots_detail="robots.txt loaded",
    )


def _errors() -> list[str]:
    return [body for kind, body in streamlit_stub.rendered
            if kind == "message" and str(body).startswith("This screen hit an error")]


def _exceptions() -> list[str]:
    return [body for kind, body in streamlit_stub.rendered if kind == "exception"]


class AppSmokeTests(unittest.TestCase):
    def setUp(self):
        streamlit_stub.reset()

    def tearDown(self):
        streamlit_stub.reset()

    def test_extract_view_empty_state(self):
        streamlit_stub.session_state["nav"] = "Directory Extraction"
        _load_app("app_empty")
        texts = " ".join(body for _kind, body in streamlit_stub.rendered)
        self.assertIn("No directory inspected yet", texts)
        self.assertEqual(_errors(), [])
        self.assertEqual(_exceptions(), [])

    def test_extract_view_full_flow(self):
        streamlit_stub.session_state["nav"] = "Directory Extraction"
        streamlit_stub.session_state["x_inspection"] = _fake_inspection()
        streamlit_stub.session_state["x_run"] = _fake_result()

        _load_app("app_full")

        texts = " ".join(body for _kind, body in streamlit_stub.rendered)
        self.assertIn("Inspection report", texts)
        self.assertIn("Data quality", texts)
        self.assertIn("Preview and export", texts)
        self.assertEqual(_errors(), [], f"render error: {_errors()}")
        self.assertEqual(_exceptions(), [])

        frames = [body for kind, body in streamlit_stub.rendered if kind == "dataframe"]
        self.assertTrue(any("2 rows" in f for f in frames), frames)

        downloads = [body for kind, body in streamlit_stub.rendered if kind == "download"]
        self.assertIn("⬇️ Download CSV", downloads)
        self.assertIn("⬇️ Download Excel", downloads)

    def test_discover_view_empty_state(self):
        streamlit_stub.session_state["nav"] = "Local Discovery"
        _load_app("app_discover")
        texts = " ".join(body for _kind, body in streamlit_stub.rendered)
        self.assertIn("Your prospect pipeline is empty", texts)
        self.assertEqual(_errors(), [])
        self.assertEqual(_exceptions(), [])

    def test_health_view_empty(self):
        streamlit_stub.session_state["nav"] = "Adapter Health"
        with tempfile.TemporaryDirectory() as tmp:
            original = runs_module.HISTORY_FILE
            runs_module.HISTORY_FILE = Path(tmp) / "history.jsonl"
            try:
                _load_app("app_health")
            finally:
                runs_module.HISTORY_FILE = original
        texts = " ".join(body for _kind, body in streamlit_stub.rendered)
        self.assertIn("No runs recorded yet", texts)
        self.assertEqual(_errors(), [])
        self.assertEqual(_exceptions(), [])

    def test_history_view(self):
        streamlit_stub.session_state["nav"] = "Run History"
        with tempfile.TemporaryDirectory() as tmp:
            original = runs_module.HISTORY_FILE
            runs_module.HISTORY_FILE = Path(tmp) / "history.jsonl"
            try:
                runs_module.append_run({
                    "run_id": "20261107-120000-abc123",
                    "finished_at": "2026-11-07T12:00:00+00:00",
                    "started_at": "2026-11-07T11:59:00+00:00",
                    "url": f"{BASE}/agents",
                    "adapter_label": "schema.org structured data",
                    "records": 6, "success_rate": 1.0, "level": "ok",
                    "health_status": "first_run", "field_fill": {"phone": 1.0},
                    "warnings": [], "errors": [],
                })
                _load_app("app_history")
            finally:
                runs_module.HISTORY_FILE = original
        texts = " ".join(body for _kind, body in streamlit_stub.rendered)
        self.assertIn("Run history", texts)
        self.assertEqual(_errors(), [])
        self.assertEqual(_exceptions(), [])

    # -- browser mode ------------------------------------------------------
    def _seed_browser(self, with_result: bool = False):
        from core.browser.structure import analyze_html
        from core.models import BrowserInspection
        from tests.fake_browser import FakeBrowserSession

        html = (Path(__file__).parent / "fixtures" / "browser" / "roster_p1.html").read_text(encoding="utf-8")
        model = analyze_html(html, BASE)[0]
        inspection = BrowserInspection(
            url=BASE, final_url=BASE, title="Our Agents", status_code=200, rendered=True,
            rendered_html=html, row_count=model.row_count, structure=model, candidates=[model],
            pagination={"strategy": "next_button", "reason": "Found a next-page link."},
        )
        streamlit_stub.session_state["nav"] = "Directory Extraction"
        streamlit_stub.session_state["x_mode"] = "Browser"
        streamlit_stub.session_state["x_url_input"] = BASE
        streamlit_stub.session_state["x_browser_inspection"] = inspection
        streamlit_stub.session_state["x_browser_html"] = html
        if with_result:
            from core.browser.engine import run_browser_extraction
            session = FakeBrowserSession([html], start_url=BASE)
            streamlit_stub.session_state["x_run"] = run_browser_extraction(
                BASE, session=session, structure=model, mapping=None, persist=False,
            )

    def test_browser_mode_renders_mapping_and_preview(self):
        self._seed_browser()
        _load_app("app_browser")
        texts = " ".join(body for _kind, body in streamlit_stub.rendered)
        self.assertIn("Open the page in a browser", texts)
        self.assertIn("What was found", texts)
        self.assertIn("Choose what to collect", texts)
        self.assertIn("Preview of the first records", texts)
        self.assertEqual(_errors(), [], _errors())
        self.assertEqual(_exceptions(), [])

        frames = [body for kind, body in streamlit_stub.rendered if kind == "dataframe"]
        self.assertTrue(frames, "the mapping preview should render a table")

    def test_browser_mode_without_playwright_shows_install_help(self):
        self._seed_browser()
        _load_app("app_browser_nopw")
        codes = [body for kind, body in streamlit_stub.rendered if kind == "code"]
        self.assertTrue(any("playwright install chromium" in c for c in codes), codes)
        self.assertEqual(_errors(), [])

    def test_browser_mode_with_result_renders_quality_and_export(self):
        self._seed_browser(with_result=True)
        _load_app("app_browser_result")
        texts = " ".join(body for _kind, body in streamlit_stub.rendered)
        self.assertIn("Data quality", texts)
        self.assertIn("Preview and export", texts)
        downloads = [body for kind, body in streamlit_stub.rendered if kind == "download"]
        self.assertIn("⬇️ Download CSV", downloads)
        self.assertEqual(_errors(), [], _errors())
        self.assertEqual(_exceptions(), [])

    def test_paywall_enabled_renders_lock(self):
        from ui import components

        original = components.SETTINGS
        components.SETTINGS = dataclasses.replace(original, license_enabled=True)
        try:
            streamlit_stub.session_state["nav"] = "Directory Extraction"
            streamlit_stub.session_state["x_inspection"] = _fake_inspection()
            streamlit_stub.session_state["x_run"] = _fake_result()
            _load_app("app_paywall")
        finally:
            components.SETTINGS = original

        texts = " ".join(body for _kind, body in streamlit_stub.rendered)
        self.assertIn("Export is Locked", texts)
        downloads = [body for kind, body in streamlit_stub.rendered if kind == "download"]
        self.assertEqual(downloads, [], "no download buttons should render while locked")
        self.assertEqual(_errors(), [])


if __name__ == "__main__":
    unittest.main()
