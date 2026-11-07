"""Browser extraction tests, run offline against pre-rendered DOM fixtures."""
from __future__ import annotations

import io
import unittest
from pathlib import Path

from tests.fake_browser import FakeBrowserSession

from core.browser.driver import BrowserBlocked
from core.browser.engine import inspect_in_browser, run_browser_extraction
from core.browser.extract import FieldMapping, extract_page, suggest_mapping
from core.browser.pagination import detect_pagination, walk_pages
from core.browser.structure import analyze_html
from core.exporters import records_to_frame, to_csv_bytes, to_excel_bytes
from core.normalize import parse_address

FIX = Path(__file__).parent / "fixtures" / "browser"
ROSTER_URL = "https://cedarandoak.example/agents"


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def roster_session(**kwargs) -> FakeBrowserSession:
    return FakeBrowserSession(
        [fx("roster_p1.html"), fx("roster_p2.html"), fx("roster_p3.html")],
        start_url=ROSTER_URL,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
class DetectionTests(unittest.TestCase):
    def test_card_layout(self):
        models = analyze_html(fx("roster_p1.html"), ROSTER_URL)
        self.assertTrue(models)
        best = models[0]
        self.assertEqual(best.row_count, 12)
        self.assertEqual(best.row_selector, "article.agent-card")
        kinds = {c.kind_hint for c in best.columns}
        self.assertTrue({"name", "phone", "email", "brokerage", "address"} <= kinds)
        self.assertTrue(best.signature)

    def test_table_layout(self):
        best = analyze_html(fx("table.html"), ROSTER_URL)[0]
        self.assertEqual(best.row_count, 10)
        self.assertEqual(best.row_selector, "tr")
        kinds = [c.kind_hint for c in best.columns]
        self.assertIn("name", kinds)
        self.assertIn("phone", kinds)
        self.assertIn("email", kinds)

    def test_js_shell_without_rendering_yields_nothing(self):
        # The raw HTML of a JS app before the browser runs: no records to detect.
        self.assertEqual(analyze_html(fx("js_shell.html"), ROSTER_URL), [])

    def test_js_rendered_dom_is_detected(self):
        # The same page after client-side rendering.
        best = analyze_html(fx("js_rendered_roster.html"), ROSTER_URL)[0]
        self.assertEqual(best.row_count, 8)

    def test_embedded_payload_is_not_a_dom_structure(self):
        self.assertEqual(analyze_html(fx("embedded.html"), ROSTER_URL), [])


# ---------------------------------------------------------------------------
# Mapping and extraction
# ---------------------------------------------------------------------------
class MappingTests(unittest.TestCase):
    def setUp(self):
        self.html = fx("roster_p1.html")
        self.model = analyze_html(self.html, ROSTER_URL)[0]
        self.mapping = suggest_mapping(self.model)

    def test_auto_mapping_covers_the_core_fields(self):
        for field in ("agent_name", "brokerage_name", "phone", "email", "street_address", "profile_url"):
            self.assertIn(field, self.mapping.columns, field)

    def test_extracted_values_are_correct(self):
        rows = extract_page(self.html, ROSTER_URL, self.model, self.mapping)
        self.assertEqual(len(rows), 12)
        first = rows[0]
        self.assertEqual(first["agent_name"], "Jane Smith")
        self.assertEqual(first["brokerage_name"], "Cedar & Oak Realty")
        self.assertEqual(first["phone"], "(512) 555-1000")
        self.assertEqual(first["email"], "jane.smith@cedarandoak.example")
        self.assertEqual(first["city"], "Austin")
        self.assertEqual(first["state"], "TX")
        self.assertEqual(first["postal_code"], "78746")
        self.assertEqual(first["street_address"], "3300 Bee Caves Rd")
        self.assertTrue(first["profile_url"].endswith("/agents/jane-smith"))

    def test_manual_mapping_overrides_auto(self):
        # Point "agent_name" at the brokerage column; extraction must obey it.
        brokerage_col = next(c.index for c in self.model.columns if c.kind_hint == "brokerage")
        mapping = FieldMapping({**self.mapping.columns, "agent_name": brokerage_col})
        rows = extract_page(self.html, ROSTER_URL, self.model, mapping)
        self.assertEqual(rows[0]["agent_name"], "Cedar & Oak Realty")

    def test_unmapped_field_is_empty_not_invented(self):
        mapping = FieldMapping({"agent_name": self.mapping.columns["agent_name"]})
        rows = extract_page(self.html, ROSTER_URL, self.model, mapping)
        self.assertEqual(rows[0].get("email", ""), "")
        self.assertIn("profile_url", rows[0])  # fallback link is still captured

    def test_address_without_separate_city_column(self):
        parsed = parse_address("3300 Bee Caves Rd, Austin, TX 78746")
        self.assertEqual((parsed["city"], parsed["state"], parsed["postal_code"]),
                         ("Austin", "TX", "78746"))


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
class PaginationTests(unittest.TestCase):
    def test_detects_next_button(self):
        model = analyze_html(fx("roster_p1.html"), ROSTER_URL)[0]
        plan = detect_pagination(fx("roster_p1.html"), ROSTER_URL, model.row_selector)
        self.assertEqual(plan.strategy, "next_button")
        self.assertEqual(plan.selector, "a.pagination__next")

    def test_detects_load_more(self):
        model = analyze_html(fx("load_more_p1.html"), ROSTER_URL)[0]
        plan = detect_pagination(fx("load_more_p1.html"), ROSTER_URL, model.row_selector)
        self.assertEqual(plan.strategy, "load_more")
        self.assertIn("load-more", plan.selector)

    def test_next_button_walk_collects_three_pages(self):
        session = roster_session()
        model = analyze_html(fx("roster_p1.html"), ROSTER_URL)[0]
        mapping = suggest_mapping(model)
        plan = detect_pagination(fx("roster_p1.html"), ROSTER_URL, model.row_selector)

        rows, stats = walk_pages(
            session, plan,
            lambda html, url: extract_page(html, url, model, mapping),
            max_pages=5, row_selector=model.row_selector,
        )
        self.assertEqual(len(rows), 36)
        self.assertEqual(stats.pages, 3)
        self.assertEqual(stats.clicked, 2)

    def test_load_more_collects_appended_records(self):
        session = FakeBrowserSession(
            [fx("load_more_p1.html"), fx("load_more_p2.html")], start_url=ROSTER_URL
        )
        model = analyze_html(fx("load_more_p1.html"), ROSTER_URL)[0]
        mapping = suggest_mapping(model)
        plan = detect_pagination(fx("load_more_p1.html"), ROSTER_URL, model.row_selector)

        rows, stats = walk_pages(
            session, plan, lambda html, url: extract_page(html, url, model, mapping),
            max_pages=5, row_selector=model.row_selector,
        )
        self.assertEqual(len(rows), 12)
        self.assertEqual(stats.pages, 2)

    def test_infinite_scroll(self):
        session = FakeBrowserSession(
            [fx("infinite_p1.html"), fx("infinite_p2.html"), fx("infinite_p3.html")],
            start_url=ROSTER_URL,
        )
        model = analyze_html(fx("infinite_p1.html"), ROSTER_URL)[0]
        mapping = suggest_mapping(model)
        plan = detect_pagination(fx("infinite_p1.html"), ROSTER_URL, model.row_selector)
        self.assertEqual(plan.strategy, "infinite_scroll")

        rows, stats = walk_pages(
            session, plan, lambda html, url: extract_page(html, url, model, mapping),
            max_pages=5, row_selector=model.row_selector,
        )
        self.assertEqual(len(rows), 18)
        self.assertGreaterEqual(stats.scrolled, 2)

    def test_numbered_pages_are_opened_directly(self):
        pages = {
            "https://cedarandoak.example/agents?page=2": fx("roster_p2.html"),
            "https://cedarandoak.example/agents?page=3": fx("roster_p3.html"),
        }
        html = fx("roster_p1.html").replace(
            '<a class="pagination__next" href="/agents?page=2">Next</a>',
            '<a href="/agents?page=2">2</a><a href="/agents?page=3">3</a>',
        )
        session = FakeBrowserSession([html], start_url=ROSTER_URL, by_url=pages)
        model = analyze_html(html, ROSTER_URL)[0]
        mapping = suggest_mapping(model)
        plan = detect_pagination(html, ROSTER_URL, model.row_selector)
        self.assertEqual(plan.strategy, "numbered")
        self.assertEqual(len(plan.page_urls), 2)

        rows, stats = walk_pages(
            session, plan, lambda h, u: extract_page(h, u, model, mapping),
            max_pages=5, row_selector=model.row_selector,
        )
        self.assertEqual(len(rows), 36)  # page 1 already open + 2 more pages

    def test_stalled_pagination_reports_rather_than_loops(self):
        # Clicking 'next' returns the same page: must stop with a warning.
        session = FakeBrowserSession([fx("roster_p1.html")], start_url=ROSTER_URL)
        model = analyze_html(fx("roster_p1.html"), ROSTER_URL)[0]
        mapping = suggest_mapping(model)
        plan = detect_pagination(fx("roster_p1.html"), ROSTER_URL, model.row_selector)

        rows, stats = walk_pages(
            session, plan, lambda h, u: extract_page(h, u, model, mapping),
            max_pages=5, row_selector=model.row_selector,
        )
        self.assertEqual(len(rows), 12)
        self.assertEqual(stats.pages, 1)
        self.assertTrue(stats.stopped_reason)


# ---------------------------------------------------------------------------
# End-to-end engine
# ---------------------------------------------------------------------------
class EngineTests(unittest.TestCase):
    def test_full_run_across_pages(self):
        session = roster_session()
        result = run_browser_extraction(
            ROSTER_URL, session=session, max_pages=5, persist=False
        )
        self.assertEqual(result.adapter_id, "browser_dom")
        self.assertEqual(len(result.records), 36)
        self.assertEqual(result.pages_fetched, 3)
        self.assertEqual(result.quality.level, "ok")
        self.assertEqual(result.health.status, "first_run")
        jane = result.records[0]
        self.assertEqual(jane.agent_name, "Jane Smith")
        self.assertTrue(jane.collected_at)

    def test_duplicate_records_are_removed(self):
        # Same page served three times: 12 unique records, 24 duplicates.
        same = fx("roster_p1.html")
        session = FakeBrowserSession([same, same, same], start_url=ROSTER_URL)
        result = run_browser_extraction(ROSTER_URL, session=session, max_pages=5, persist=False)
        self.assertEqual(len(result.records), 12)
        # The page never changes, so the walker stops after one page with an
        # explicit warning instead of counting the same agents three times.
        self.assertEqual(result.pages_fetched, 1)
        self.assertTrue(any("did not change" in w for w in result.inspection.warnings))

    def test_embedded_payload_path(self):
        session = FakeBrowserSession([fx("embedded.html")], start_url=ROSTER_URL)
        result = run_browser_extraction(ROSTER_URL, session=session, persist=False)
        self.assertEqual(len(result.records), 8)
        self.assertEqual(result.records[0].agent_name, "Jane Smith")
        self.assertEqual(result.records[0].email, "jane.smith@cedarandoak.example")
        self.assertEqual(result.records[0].brokerage_name, "Cedar & Oak Realty")
        self.assertTrue(result.records[0].profile_url.endswith("/agents/jane-smith"))

    def test_blocked_site_raises_and_does_not_produce_a_result(self):
        session = FakeBrowserSession([fx("roster_p1.html")], blocked=True,
                                     blocked_message="anti-bot challenge")
        with self.assertRaises(BrowserBlocked):
            run_browser_extraction(ROSTER_URL, session=session, persist=False)

    def test_profile_enrichment_fills_missing_contact(self):
        listing = fx("roster_p1.html").replace(
            '<li class="agent-card__phone">(512) 555-1000</li>', ""
        ).replace(
            '<li class="agent-card__email"><a href="mailto:jane.smith@cedarandoak.example">jane.smith@cedarandoak.example</a></li>', ""
        )
        profile = """<!DOCTYPE html><html><head><title>Jane Smith</title></head><body>
        <main><h1>Jane Smith</h1>
        <a href="tel:+15125550100">(512) 555-0100</a>
        <a href="mailto:jane@cedarandoak.example">Email</a></main></body></html>"""
        session = FakeBrowserSession(
            [listing], start_url=ROSTER_URL,
            by_url={"https://cedarandoak.example/agents/jane-smith": profile},
        )
        model = analyze_html(listing, ROSTER_URL)[0]
        result = run_browser_extraction(
            ROSTER_URL, session=session, structure=model,
            mapping=suggest_mapping(model), enrich_profiles=True, max_profiles=1, persist=False,
        )
        jane = next(r for r in result.records if r.agent_name == "Jane Smith")
        self.assertEqual(jane.phone, "+1 (512) 555-0100")
        self.assertTrue(jane.email)

    def test_inspection_reports_structure_and_pagination(self):
        session = roster_session()
        report = inspect_in_browser(ROSTER_URL, session=session)
        self.assertTrue(report.rendered)
        self.assertEqual(report.row_count, 12)
        self.assertEqual(report.pagination.get("strategy"), "next_button")
        self.assertIsNotNone(report.structure)
        self.assertEqual(len(report.candidates), 1)

    def test_inspection_of_js_shell_warns_instead_of_crashing(self):
        session = FakeBrowserSession([fx("js_shell.html")], start_url=ROSTER_URL)
        report = inspect_in_browser(ROSTER_URL, session=session)
        self.assertEqual(report.row_count, 0)
        self.assertIsNone(report.structure)
        self.assertTrue(report.warnings)


# ---------------------------------------------------------------------------
# Export of browser results
# ---------------------------------------------------------------------------
class BrowserExportTests(unittest.TestCase):
    def test_csv_and_excel_contain_real_records(self):
        session = roster_session()
        result = run_browser_extraction(ROSTER_URL, session=session, max_pages=5, persist=False)

        csv_bytes = to_csv_bytes(result.records, result.selected_fields)
        text = csv_bytes.decode("utf-8-sig")
        self.assertIn("Jane Smith", text)
        self.assertIn("jane.smith@cedarandoak.example", text)
        self.assertNotIn("Jane Smith", text.split("\n")[0], "no fixture leakage into headers")

        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(to_excel_bytes(result, result.selected_fields)))
        self.assertEqual(workbook.sheetnames, ["Records", "Run Summary"])
        rows = list(workbook["Records"].iter_rows(values_only=True))
        self.assertEqual(len(rows), 37)  # header + 36 records

    def test_frame_has_no_internal_columns(self):
        session = roster_session()
        result = run_browser_extraction(ROSTER_URL, session=session, max_pages=2, persist=False)
        frame = records_to_frame(result.records, result.selected_fields)
        self.assertNotIn("_key", frame.columns)
        self.assertNotIn("phone_digits", frame.columns)


if __name__ == "__main__":
    unittest.main()
