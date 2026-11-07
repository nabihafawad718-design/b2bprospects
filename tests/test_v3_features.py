"""Tests for the v3 features: extra fields, cleaning, config adapters, embedded
payloads, visual selection, CLI and the local demo directory.

The integration class starts a real HTTP server on loopback and extracts from it.
That exercises requests, robots handling, adapter resolution, pagination, address
normalization, extra columns and export without mocking the transport.
"""
from __future__ import annotations

import dataclasses
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.fake_browser import FakeBrowserSession
from tests import fake_playwright
from tests.helpers import fixture

from core.adapters.base import AdapterContext
from core.adapters.config_adapter import ConfigSiteAdapter, load_config_adapters
from core.adapters.generic_html import GenericHtmlAdapter
from core.adapters.registry import resolve_adapter
from core.browser.driver import PlaywrightSession, RenderedPage
from core.browser.engine import run_browser_extraction
from core.browser.js import CONTENT_SIGNATURE_JS
from core.browser.embedded import extract_from_embedded
from core.browser.extract import FieldMapping, extract_page, suggest_mapping, to_raw_records
from core.browser.pagination import detect_pagination, walk_pages
from core.browser.structure import analyze_html, column_for_click
from core.cleaning import FilterSpec, dedupe_records, filter_records, issue_rows
from core.engine import run_extraction
from core.exporters import records_to_frame, to_csv_bytes, to_excel_bytes
from core.inspect_site import parse_html
from core.models import Record, RunResult
from core.net import FetchedPage, PoliteFetcher, RobotsDisallowed
from core.normalize import clean_text, find_emails, normalize_name, normalize_phone, parse_address
from core.quality import build_record, evaluate
from core.settings import SETTINGS
from scripts.serve_demo import listing_html, make_server

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "browser"


class HttpBrowserSession:
    """A BrowserSession that fetches real URLs over HTTP and follows links.

    It does not execute JavaScript, but it exercises the browser engine's real
    navigation, pagination, detection and extraction code against the local demo
    server. The Playwright session implements the same protocol.
    """

    def __init__(self, base_url: str) -> None:
        import requests

        self.base_url = base_url
        self.http = requests.Session()
        self._html = ""
        self._url = ""
        self._title = ""

    def open(self, url: str) -> RenderedPage:
        response = self.http.get(url, timeout=8)
        self._html = response.text
        self._url = response.url
        soup = parse_html(self._html)
        self._title = clean_text(soup.title.get_text()) if soup.title else ""
        return RenderedPage(url=url, final_url=response.url, title=self._title,
                            status=response.status_code, html=self._html)

    def content(self) -> str:
        return self._html

    def current_url(self) -> str:
        return self._url

    def click(self, selector: str, timeout_ms: int | None = None) -> bool:
        from urllib.parse import urljoin

        soup = parse_html(self._html)
        try:
            element = soup.select_one(selector)
        except Exception:
            element = None
        if element is None or not element.get("href"):
            return False
        self.open(urljoin(self._url, str(element["href"])))
        return True

    def click_text(self, text: str, timeout_ms: int | None = None) -> bool:
        from urllib.parse import urljoin

        soup = parse_html(self._html)
        for element in soup.find_all(["a", "button"]):
            if clean_text(element.get_text(" ")).lower() == clean_text(text).lower():
                if element.get("href"):
                    self.open(urljoin(self._url, str(element["href"])))
                    return True
        return False

    def evaluate(self, script: str, arg=None):
        if str(script).strip() == CONTENT_SIGNATURE_JS.strip():
            soup = parse_html(self._html)
            try:
                rows = soup.select(str(arg or ""))
            except Exception:
                return ""
            texts = [clean_text(row.get_text(" "))[:120] for row in rows[:12]]
            return f"{len(rows)}|" + "||".join(texts)
        return None

    def scroll_to_bottom(self, steps: int = 10, pause_ms: int = 250) -> dict:
        return {"height": 1000, "steps": 1}

    def auto_scroll(self, steps: int | None = None, pause_ms: int = 200) -> dict:
        return {"height": 1000, "steps": 1}

    def wait(self, ms: int) -> None:
        return None

    def reload(self) -> None:
        if self._url:
            self.open(self._url)

    def close(self) -> None:
        self.http.close()


def _context(url: str = "http://demo.test/agents") -> AdapterContext:
    return AdapterContext(start_url=url, fetcher=PoliteFetcher(SETTINGS))


def _local_settings(**overrides):
    base = {
        "allow_local_urls": True,
        "request_delay_seconds": 0.0,
        "max_retries": 0,
        "respect_robots_txt": False,
        "robots_policy": "warn",
    }
    base.update(overrides)
    return dataclasses.replace(SETTINGS, **base)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
class NormalizationV3Tests(unittest.TestCase):
    def test_phone_extension_is_kept_in_display_not_digits(self):
        display, digits = normalize_phone("(512) 555-0142 ext 12")
        self.assertEqual(display, "(512) 555-0142 ext. 12")
        self.assertEqual(digits, "5125550142")

    def test_collapsed_br_address_is_split_correctly(self):
        parsed = parse_address("901 Round Rock Ave Round Rock, TX 78681")
        self.assertEqual(parsed["street_address"], "901 Round Rock Ave")
        self.assertEqual(parsed["city"], "Round Rock")
        self.assertEqual(parsed["state"], "TX")
        self.assertEqual(parsed["postal_code"], "78681")

    def test_saint_louis_is_not_split_as_a_street(self):
        parsed = parse_address("123 Main St, St. Louis, MO 63101")
        self.assertEqual(parsed["city"], "St. Louis")
        self.assertEqual(parsed["state"], "MO")

    def test_obfuscated_email_is_found(self):
        self.assertEqual(find_emails("jane [at] demo-brokerage [dot] example", limit=1),
                         ["jane@demo-brokerage.example"])

    def test_all_caps_name_is_title_cased(self):
        self.assertEqual(normalize_name("JANE SMITH, ABR"), "Jane Smith")


# ---------------------------------------------------------------------------
# Extra fields
# ---------------------------------------------------------------------------
CARD_HTML = """<!DOCTYPE html><html><body><main><div class="cards">
  <div class="card"><h3>Jane Smith</h3><p class="title">Senior Agent</p><p class="license">TX-1</p></div>
  <div class="card"><h3>Marcus Lee</h3><p class="title">Broker Associate</p><p class="license">TX-2</p></div>
  <div class="card"><h3>Priya Nair</h3><p class="title">Team Lead</p><p class="license">TX-3</p></div>
</div></main></body></html>"""


class ExtraFieldTests(unittest.TestCase):
    def test_extra_columns_survive_mapping_record_and_export(self):
        model = analyze_html(CARD_HTML, "https://x.example/agents")[0]
        mapping = suggest_mapping(model)
        title_col = next(c.index for c in model.columns if "Senior Agent" in c.sample_values)
        license_col = next(c.index for c in model.columns if "TX-1" in c.sample_values)
        mapping.columns["title"] = title_col
        mapping.columns["license_number"] = license_col

        rows = extract_page(CARD_HTML, "https://x.example/agents", model, mapping)
        self.assertEqual(rows[0]["title"], "Senior Agent")
        self.assertEqual(rows[0]["license_number"], "TX-1")

        raw = to_raw_records(rows, "https://x.example/agents")[0]
        record = build_record(raw, ["agent_name", "title", "license_number"], "test")
        self.assertEqual(record.extra["title"], "Senior Agent")
        self.assertEqual(record.extra["license_number"], "TX-1")

        frame = records_to_frame([record], ["agent_name", "title", "license_number"],
                                 ["title", "license_number"])
        self.assertIn("Title", frame.columns)
        self.assertIn("License Number", frame.columns)
        csv = to_csv_bytes([record], ["agent_name", "title", "license_number"],
                           ["title", "license_number"]).decode("utf-8-sig")
        self.assertIn("License Number", csv.splitlines()[0])
        self.assertIn("TX-1", csv)


# ---------------------------------------------------------------------------
# Embedded payloads
# ---------------------------------------------------------------------------
class EmbeddedV3Tests(unittest.TestCase):
    def test_next_flight_chunks_are_read(self):
        records = [
            {"fullName": "Jane Smith", "email": "jane@x.example", "phone": "5125550100", "position": "Agent"},
            {"fullName": "Marcus Lee", "email": "marcus@x.example", "phone": "5125550101", "position": "Broker"},
            {"fullName": "Priya Nair", "email": "priya@x.example", "phone": "5125550102", "position": "Lead"},
        ]
        chunks = "".join(
            f"self.__next_f.push([1,{json.dumps(json.dumps(record))}]);"
            for record in records
        )
        html = f"<html><body><script>{chunks}</script></body></html>"
        found, payload = extract_from_embedded(html, "https://x.example/agents")
        self.assertEqual(payload, "next-flight")
        self.assertEqual(len(found), 3)
        self.assertEqual(found[0].fields["agent_name"], "Jane Smith")
        self.assertEqual(found[0].fields["title"], "Agent")

    def test_menu_payload_is_not_treated_as_records(self):
        menu = [{"name": f"Menu item {i}", "url": f"/page/{i}"} for i in range(6)]
        html = f'<html><body><script type="application/json">{json.dumps(menu)}</script></body></html>'
        found, _payload = extract_from_embedded(html, "https://x.example/")
        self.assertEqual(found, [])

    def test_jsonld_graph_itemlist_of_people_is_read(self):
        people = [
            {"@type": "Person", "name": "Jane Smith", "telephone": "512-555-0100",
             "email": "jane@x.example"},
            {"@type": "Person", "name": "Marcus Lee", "telephone": "512-555-0101",
             "email": "marcus@x.example"},
            {"@type": "Person", "name": "Priya Nair", "telephone": "512-555-0102",
             "email": "priya@x.example"},
        ]
        payload = {"@context": "https://schema.org", "@graph": [
            {"@type": "ItemList", "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "item": person}
                for i, person in enumerate(people)
            ]}
        ]}
        html = f'<html><body><script type="application/ld+json">{json.dumps(payload)}</script></body></html>'
        found, _name = extract_from_embedded(html, "https://x.example/agents")
        self.assertEqual(len(found), 3)
        self.assertEqual({record.fields["agent_name"] for record in found},
                         {"Jane Smith", "Marcus Lee", "Priya Nair"})


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------
class CleaningV3Tests(unittest.TestCase):
    def _records(self):
        return [
            Record(agent_name="Jane Smith", email="jane@x.example", phone="(512) 555-0100",
                   phone_digits="5125550100", city="Austin", state="TX",
                   quality_flags=["phone only"]),
            Record(agent_name="Jane Smith", email="jane@x.example", phone="(512) 555-0100",
                   phone_digits="5125550100", city="Austin", state="TX",
                   quality_flags=["phone only"]),
            Record(agent_name="Marcus Lee", email="", phone="",
                   phone_digits="", city="Round Rock", state="TX",
                   quality_flags=["no contact details"]),
            Record(agent_name="", email="", phone="", city="", state="",
                   quality_flags=["missing name"]),
        ]

    def test_strategy_specific_dedupe(self):
        self.assertEqual(dedupe_records(self._records(), "email")[1], 1)
        self.assertEqual(dedupe_records(self._records(), "profile_url")[1], 0)
        self.assertEqual(dedupe_records(self._records(), "name_phone")[1], 1)

    def test_filters_and_issue_rows(self):
        records = self._records()
        named = filter_records(records, FilterSpec(require_name=True))
        self.assertEqual(len(named), 3)
        contacted = filter_records(records, FilterSpec(require_contact=True))
        self.assertEqual(len(contacted), 2)
        texas = filter_records(records, FilterSpec(state="tx"))
        self.assertEqual(len(texas), 3)
        issues = issue_rows(records)
        self.assertTrue(any("No phone or email" in row["Issues"] for row in issues))


# ---------------------------------------------------------------------------
# Pagination and visual mapping
# ---------------------------------------------------------------------------
class PaginationV3Tests(unittest.TestCase):
    def test_query_parameter_pagination_is_detected_from_current_url(self):
        html = "<html><body><p>no pagination controls</p></body></html>"
        plan = detect_pagination(html, "https://x.example/agents?page=1")
        self.assertEqual(plan.strategy, "query_param")
        self.assertEqual(plan.param, "page")

    def test_offset_pagination_infers_the_step(self):
        html = '<html><body><a href="?offset=20">Older</a></body></html>'
        plan = detect_pagination(html, "https://x.example/agents?offset=0")
        self.assertEqual(plan.strategy, "query_param")
        self.assertEqual(plan.param, "offset")
        self.assertEqual(plan.param_step, 20)

        pages = {
            "https://x.example/agents?offset=20": "<html><body>2</body></html>",
            "https://x.example/agents?offset=40": "<html><body>3</body></html>",
        }
        session = FakeBrowserSession(["<html><body>1</body></html>"],
                                     start_url="https://x.example/agents?offset=0",
                                     by_url=pages)
        rows, stats = walk_pages(
            session, plan, lambda _h, url: [{"agent_name": url, "_key": url}], max_pages=3
        )
        self.assertEqual(stats.pages, 3)
        self.assertEqual(len(rows), 3)

    def test_static_offset_pagination_uses_the_inferred_step(self):
        from core.adapters.generic_html import next_page_urls_from_soup

        soup = parse_html('<html><body><a href="?offset=20">Older</a></body></html>')
        urls = next_page_urls_from_soup(soup, "https://x.example/agents?offset=0", max_list_pages=3)
        self.assertIn("https://x.example/agents?offset=20", urls)
        self.assertIn("https://x.example/agents?offset=40", urls)

    def test_query_parameter_walker_opens_each_page(self):
        plan = detect_pagination("<html><body><p>no controls</p></body></html>",
                                 "https://x.example/agents?page=1")
        self.assertEqual(plan.strategy, "query_param")
        pages = {
            "https://x.example/agents?page=2": "<html><body>2</body></html>",
            "https://x.example/agents?page=3": "<html><body>3</body></html>",
        }
        session = FakeBrowserSession(["<html><body>1</body></html>"],
                                     start_url="https://x.example/agents?page=1",
                                     by_url=pages)

        def handler(_html, url):
            return [{"agent_name": url, "_key": url}]

        rows, stats = walk_pages(session, plan, handler, max_pages=3)
        self.assertEqual(stats.pages, 3)
        self.assertEqual(len(rows), 3)


class VisualMappingTests(unittest.TestCase):
    def setUp(self):
        self.html = (FIX / "roster_p1.html").read_text(encoding="utf-8")
        self.model = analyze_html(self.html, "https://cedarandoak.example/agents")[0]

    def test_click_path_maps_to_name_and_phone_columns(self):
        name_col = next(c.index for c in self.model.columns if c.kind_hint == "name")
        phone_col = next(c.index for c in self.model.columns if c.kind_hint == "phone")
        name_column = self.model.column(name_col)
        phone_column = self.model.column(phone_col)
        self.assertEqual(column_for_click(self.model, name_column.path, "Jane Smith"), name_col)
        self.assertEqual(column_for_click(self.model, phone_column.path, "(512) 555-1000"), phone_col)

    def test_click_text_fallback_maps_to_the_right_column(self):
        phone_col = next(c.index for c in self.model.columns if c.kind_hint == "phone")
        self.assertEqual(column_for_click(self.model, "9.9.9", "(512) 555-1000"), phone_col)

    def test_snapshot_sanitizer_strips_executable_content(self):
        from ui.visual_picker import sanitize_snapshot

        html = """<html><head><script>alert(1)</script></head>
        <body><div onclick="evil()">Hello</div><iframe src="x"></iframe></body></html>"""
        cleaned, ok = sanitize_snapshot(html, "https://x.example/agents", max_bytes=100_000)
        self.assertTrue(ok)
        self.assertNotIn("<script", cleaned.lower())
        self.assertNotIn("onclick", cleaned.lower())
        self.assertNotIn("<iframe", cleaned.lower())
        self.assertIn('<base href="https://x.example/agents"', cleaned)
        self.assertIn("b2b-snapshot", cleaned)

    def test_snapshot_size_cap(self):
        from ui.visual_picker import sanitize_snapshot

        cleaned, ok = sanitize_snapshot("<html><body>" + "x" * 500 + "</body></html>",
                                        "https://x.example/", max_bytes=100)
        self.assertFalse(ok)
        self.assertEqual(cleaned, "")

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_visual_picker_javascript_is_syntactically_valid(self):
        html = (ROOT / "ui" / "visual_picker" / "index.html").read_text(encoding="utf-8")
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        self.assertTrue(scripts)
        with tempfile.TemporaryDirectory() as tmp:
            for index, script in enumerate(scripts):
                path = Path(tmp) / f"picker-{index}.js"
                path.write_text(script, encoding="utf-8")
                result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


# ---------------------------------------------------------------------------
# Config adapters and listing records
# ---------------------------------------------------------------------------
class ConfigAdapterTests(unittest.TestCase):
    def test_config_directory_loads_without_errors(self):
        adapters, errors = load_config_adapters()
        self.assertEqual(errors, [])
        self.assertTrue(any(adapter.id == "demo_brokerage" for adapter in adapters))

    def test_config_adapter_extracts_listing_records_and_extras(self):
        html = listing_html(1)
        page = FetchedPage(url="http://127.0.0.1:8899/agents",
                           final_url="http://127.0.0.1:8899/agents",
                           status_code=200, text=html)
        adapter = resolve_adapter(page.final_url, html)
        self.assertEqual(adapter.id, "demo_brokerage")
        records = adapter.listing_records(page, _context(page.final_url))
        self.assertEqual(len(records), 12)
        first = records[0].fields
        self.assertEqual(first["agent_name"], "Jane Smith")
        self.assertEqual(first["title"], "Senior Agent")
        self.assertEqual(first["license_number"], "TX-100000")
        second = records[1].fields
        self.assertEqual(second["street_address"], "901 Round Rock Ave")
        self.assertEqual(second["city"], "Round Rock")

    def test_generic_html_adapter_also_reads_the_demo_listing(self):
        html = listing_html(1)
        page = FetchedPage(url="http://127.0.0.1:8899/agents",
                           final_url="http://127.0.0.1:8899/agents",
                           status_code=200, text=html)
        records = GenericHtmlAdapter().listing_records(page, _context(page.final_url))
        self.assertEqual(len(records), 12)
        self.assertEqual(records[0].fields["agent_name"], "Jane Smith")


# ---------------------------------------------------------------------------
# Export with issues
# ---------------------------------------------------------------------------
class ExportV3Tests(unittest.TestCase):
    def test_excel_issues_sheet_is_optional_and_populated(self):
        from openpyxl import load_workbook
        import io

        records = [
            Record(agent_name="Jane Smith", phone="(512) 555-0100", city="Austin", state="TX",
                   quality_flags=[]),
            Record(agent_name="", quality_flags=["missing name", "no contact details"]),
        ]
        run = RunResult(url="https://x.example/agents", records=records,
                        selected_fields=["agent_name", "phone", "email"])
        run.quality = evaluate(records, 2, 0, ("agent_name", "phone", "email"))

        workbook = load_workbook(io.BytesIO(to_excel_bytes(
            run, run.selected_fields, include_issues=True)))
        self.assertIn("Issues", workbook.sheetnames)
        rows = list(workbook["Issues"].iter_rows(values_only=True))
        self.assertEqual(rows[0][0], "Row")
        self.assertTrue(any("missing" in str(row).lower() for row in rows[1:]))


# ---------------------------------------------------------------------------
# Robots policy
# ---------------------------------------------------------------------------
class RobotsPolicyTests(unittest.TestCase):
    def test_warn_policy_reports_instead_of_blocking(self):
        from tests.helpers import FakeSite, test_settings

        site = FakeSite({"https://x.example/agents": "<html><body>ok</body></html>"},
                        robots="User-agent: *\nDisallow: /agents\n")
        settings = test_settings(respect_robots_txt=False, robots_policy="warn")
        page = PoliteFetcher(settings=settings, transport=site, sleep=lambda _s: None).get(
            "https://x.example/agents")
        self.assertFalse(page.robots_allowed)
        self.assertIn("disallow", page.robots_detail.lower())

    def test_enforce_policy_still_blocks(self):
        from tests.helpers import FakeSite, test_settings

        site = FakeSite({"https://x.example/agents": "<html><body>ok</body></html>"},
                        robots="User-agent: *\nDisallow: /agents\n")
        settings = test_settings(respect_robots_txt=True, robots_policy="enforce")
        with self.assertRaises(RobotsDisallowed):
            PoliteFetcher(settings=settings, transport=site, sleep=lambda _s: None).get(
                "https://x.example/agents")


# ---------------------------------------------------------------------------
# Driver additions
# ---------------------------------------------------------------------------
class DriverV3Tests(unittest.TestCase):
    def tearDown(self):
        fake_playwright.uninstall()

    def test_click_text_uses_a_text_locator(self):
        page, _module = fake_playwright.install()
        session = PlaywrightSession(respect_robots=False)
        with session:
            session.open("https://example.com/agents")
            self.assertTrue(session.click_text("Next"))
        self.assertTrue(any("Next" in selector for selector in page.clicks))

    def test_auto_scroll_returns_to_top(self):
        fake_playwright.install()
        session = PlaywrightSession(respect_robots=False)
        with session:
            session.open("https://example.com/agents")
            result = session.auto_scroll(steps=2)
        self.assertGreaterEqual(result["steps"], 1)


# ---------------------------------------------------------------------------
# Real HTTP integration: demo server + engine + CLI
# ---------------------------------------------------------------------------
class DemoIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.base = make_server(0)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _fetcher(self):
        return PoliteFetcher(settings=_local_settings(), sleep=lambda _s: None)

    def test_static_engine_extracts_all_36_across_three_pages(self):
        result = run_extraction(
            f"{self.base}/agents",
            fetcher=self._fetcher(),
            max_list_pages=5,
            max_profiles=10,
            persist=False,
        )
        self.assertEqual(result.adapter_id, "demo_brokerage")
        self.assertEqual(len(result.records), 36)
        self.assertEqual(result.pages_fetched, 3)
        self.assertEqual(result.profiles_attempted, 0)
        self.assertEqual(result.quality.level, "ok")

        marcus = next(record for record in result.records if record.agent_name == "Marcus Lee")
        self.assertEqual(marcus.street_address, "901 Round Rock Ave")
        self.assertEqual(marcus.city, "Round Rock")
        self.assertEqual(marcus.state, "TX")
        self.assertEqual(marcus.extra["title"], "Broker Associate")
        self.assertEqual(marcus.extra["license_number"], "TX-100001")
        self.assertTrue(marcus.profile_url.endswith("/agents/marcus-lee"))

    def test_operator_can_force_profile_pages_for_richer_fields(self):
        result = run_extraction(
            f"{self.base}/agents",
            fetcher=self._fetcher(),
            max_list_pages=5,
            max_profiles=5,
            persist=False,
            prefer_listing_records=False,
        )
        self.assertEqual(result.profiles_attempted, 5)
        self.assertEqual(len(result.records), 5)
        self.assertEqual(result.export_context.get("Extraction source"), "profile pages")

    def test_inspection_reports_robots_and_structure(self):
        from core.inspect_site import inspect_url

        report = inspect_url(f"{self.base}/agents", self._fetcher())
        self.assertTrue(report.robots_allowed)
        self.assertGreaterEqual(report.container_count, 12)
        self.assertEqual(report.pagination_type, "next_link")

    def test_browser_engine_navigates_and_extracts_over_real_http(self):
        from core.browser.engine import inspect_in_browser

        session = HttpBrowserSession(self.base)
        report = inspect_in_browser(f"{self.base}/agents", session=session)
        self.assertEqual(report.row_count, 12)
        self.assertEqual(report.pagination.get("strategy"), "next_button")

        session = HttpBrowserSession(self.base)
        result = run_browser_extraction(
            f"{self.base}/agents", session=session, max_pages=3,
            max_records=100, persist=False,
        )
        self.assertEqual(result.adapter_id, "browser_dom")
        self.assertEqual(len(result.records), 36)
        self.assertEqual(result.pages_fetched, 3)
        jane = result.records[0]
        self.assertEqual(jane.agent_name, "Jane Smith")
        self.assertEqual(jane.phone, "(512) 555-1000")
        self.assertTrue(jane.profile_url.endswith("/agents/jane-smith"))

    def test_cli_writes_a_36_row_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "demo"
            completed = subprocess.run(
                [sys.executable, "scripts/extract_cli.py", f"{self.base}/agents",
                 "--allow-local", "--max-pages", "3", "--max-profiles", "10",
                 "--out", str(out), "--format", "csv"],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            csv_path = Path(str(out) + ".csv")
            self.assertTrue(csv_path.exists())
            lines = csv_path.read_text(encoding="utf-8-sig").splitlines()
            self.assertEqual(len(lines), 37)  # header + 36 records
            self.assertIn("License Number", lines[0])


if __name__ == "__main__":
    unittest.main()
