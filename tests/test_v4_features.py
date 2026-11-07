"""v4 engine regressions: layered fallback, wider detection, pagination and diagnosis.

Everything here runs offline. The static integration class uses a real loopback
HTTP server; the browser class uses the scripted rendered-DOM session. Neither
proves that a third-party site's JavaScript runs in Chromium — only a live run
does that.
"""
from __future__ import annotations

import dataclasses
import io
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from tests.fake_browser import FakeBrowserSession

from core.browser.engine import run_browser_extraction
from core.browser.extract import FieldMapping, extract_page, suggest_mapping
from core.browser.pagination import detect_pagination, walk_pages
from core.browser.structure import analyze_html, select_rows
from core.engine import run_extraction
from core.exporters import to_csv_bytes, to_excel_bytes
from core.models import ExtractionDiagnosis, RunResult
from core.net import BlockedError
from core.quality import build_record, evaluate
from core.settings import SETTINGS
from core.browser.extract import to_raw_records
from ui.visual_picker import map_selection, sanitize_snapshot


def cards(names, title=None, with_links=True, with_title=False):
    parts = []
    for index, (first, last) in enumerate(names, start=1):
        link = f'<a class="card-link" href="/agents/{first.lower()}-{last.lower()}">View profile</a>' if with_links else ""
        title_html = f'<p class="card-title">{title or "Senior Agent"}</p>' if with_title else ""
        parts.append(
            f'<article class="agent-card"><h3>{first} {last}</h3>{title_html}'
            f'<p class="phone">(512) 555-10{index:02d}</p>'
            f'<p class="email">{first.lower()}.{last.lower()}@cedarandoak.example</p>'
            f'<p class="office">Cedar & Oak Realty</p>{link}</article>'
        )
    return "".join(parts)


# ---------------------------------------------------------------------------
# Detection and field inference
# ---------------------------------------------------------------------------
class FieldInferenceTests(unittest.TestCase):
    def test_table_headers_drive_first_last_title_and_location(self):
        names = [("Jane", "Smith"), ("John", "Doe"), ("Ana", "Garcia"),
                 ("Liam", "Brown"), ("Mia", "Wilson")]
        rows = "".join(
            f"<tr><td>{first}</td><td>{last}</td><td>Senior Agent</td>"
            f"<td>(512) 555-10{index:02d}</td><td>{first.lower()}@cedarandoak.example</td>"
            f"<td>Cedar & Oak Realty</td><td>Austin</td><td>TX</td><td>78746</td></tr>"
            for index, (first, last) in enumerate(names, start=1)
        )
        html = (
            "<html><body><table><thead><tr><th>First Name</th><th>Last Name</th>"
            "<th>Title</th><th>Office Phone</th><th>Email</th><th>Office</th>"
            "<th>City</th><th>State</th><th>Zip</th></tr></thead><tbody>"
            + rows + "</tbody></table></body></html>"
        )
        model = analyze_html(html, "https://x.example/agents")[0]
        mapping = suggest_mapping(model)
        self.assertEqual(mapping.column_for("agent_name"), 0)
        self.assertIn("title", mapping.columns)
        self.assertEqual(mapping.column_for("state"), 7)
        row = extract_page(html, "https://x.example/agents", model, mapping)[0]
        self.assertEqual(row["agent_name"], "Jane Smith")
        self.assertEqual(row["title"], "Senior Agent")
        self.assertEqual(row["state"], "TX")
        self.assertEqual(row["postal_code"], "78746")

    def test_headerless_first_and_last_columns_are_joined(self):
        rows = "".join(
            f"<tr><td>{first}</td><td>{last}</td><td>(512) 555-10{index:02d}</td></tr>"
            for index, (first, last) in enumerate(
                [("Jane", "Smith"), ("John", "Doe"), ("Ana", "Garcia"), ("Liam", "Brown")],
                start=1,
            )
        )
        html = f"<html><body><table><tbody>{rows}</tbody></table></body></html>"
        model = analyze_html(html, "https://x.example/team")[0]
        mapping = suggest_mapping(model)
        row = extract_page(html, "https://x.example/team", model, mapping)[0]
        self.assertEqual(row["agent_name"], "Jane Smith")

    def test_data_attributes_are_read_when_text_is_icon_only(self):
        html = "".join(
            f'<div class="person" data-name="{first} {last}" data-phone="51255510{index:02d}" '
            f'data-email="{first.lower()}@cedarandoak.example"><span aria-hidden="true">person</span>'
            f'<i class="icon-phone"></i></div>'
            for index, (first, last) in enumerate(
                [("Jane", "Smith"), ("John", "Doe"), ("Ana", "Garcia"),
                 ("Liam", "Brown")], start=1)
        )
        page = f"<html><body><main>{html}</main></body></html>"
        model = analyze_html(page, "https://x.example/team")[0]
        mapping = suggest_mapping(model)
        rows = extract_page(page, "https://x.example/team", model, mapping)
        self.assertEqual(rows[0]["agent_name"], "Jane Smith")
        self.assertEqual(rows[0]["phone"], "(512) 555-1001")
        self.assertEqual(rows[0]["email"], "jane@cedarandoak.example")

    def test_link_cluster_recovers_irregular_rows(self):
        parts = []
        for index, (tag, first, last) in enumerate(
            [("section", "Jane", "Smith"), ("article", "John", "Doe"),
             ("div", "Ana", "Garcia"), ("aside", "Liam", "Brown")]
        ):
            parts.append(
                f'<{tag} class="row-{index}"><a href="/agents/person-{index}">{first} {last}</a>'
                f"<p>(512) 555-10{index:02d}</p><p>Cedar Realty</p></{tag}>"
            )
        html = f'<html><body><main><div id="roster">{"".join(parts)}</div></main></body></html>'
        model = analyze_html(html, "https://x.example/agents")[0]
        self.assertEqual(model.method, "link-cluster")
        self.assertEqual(len(select_rows(html, model)), 4)
        mapping = suggest_mapping(model)
        rows = extract_page(html, "https://x.example/agents", model, mapping)
        self.assertEqual([row["agent_name"] for row in rows],
                         ["Jane Smith", "John Doe", "Ana Garcia", "Liam Brown"])

    def test_embedded_relative_urls_resolve_against_the_listing(self):
        from core.browser.embedded import extract_from_embedded

        payload = {"props": {"agents": [
            {"name": "Jane Smith", "phone": "5125551000",
             "email": "jane@cedarandoak.example", "url": "agents/jane-smith"},
            {"name": "John Doe", "phone": "5125551001",
             "email": "john@cedarandoak.example", "url": "/agents/john-doe"},
            {"name": "Ana Garcia", "phone": "5125551002",
             "email": "ana@cedarandoak.example", "url": "https://x.example/agents/ana"},
        ]}}
        html = (f'<html><body><script id="__NEXT_DATA__" type="application/json">'
                f"{json.dumps(payload)}</script></body></html>")
        records, _payload = extract_from_embedded(html, "https://x.example/directory")
        urls = {record.fields["agent_name"]: record.profile_url for record in records}
        self.assertEqual(urls["Jane Smith"], "https://x.example/agents/jane-smith")
        self.assertEqual(urls["John Doe"], "https://x.example/agents/john-doe")

    def test_jsonld_listing_keeps_name_and_profile_url(self):
        from core.adapters.generic_jsonld import GenericJsonLdAdapter
        from core.net import FetchedPage

        payload = {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "itemListElement": [
                {"@type": "ListItem", "item": {"@type": "Person", "name": "Jane Smith",
                                                "url": "/people/jane-smith"}},
                {"@type": "ListItem", "item": {"@type": "Person", "name": "John Doe",
                                                "url": "/people/john-doe"}},
                {"@type": "ListItem", "item": {"@type": "Person", "name": "Ana Garcia",
                                                "url": "/people/ana-garcia"}},
            ],
        }
        html = f'<html><body><script type="application/ld+json">{json.dumps(payload)}</script></body></html>'
        page = FetchedPage(url="https://x.example/agents", final_url="https://x.example/agents",
                           status_code=200, text=html, headers={"content-type": "text/html"})
        records = GenericJsonLdAdapter().listing_records(page, ctx=None)
        self.assertEqual(len(records), 3)
        self.assertTrue(records[0].profile_url.endswith("/people/jane-smith"))

    def test_accented_names_are_recognised_and_reordered(self):
        from core.normalize import looks_like_person_name, normalize_name

        self.assertTrue(looks_like_person_name("José García"))
        self.assertTrue(looks_like_person_name("Álvaro Núñez"))
        self.assertEqual(normalize_name("García, José"), "José García")
        self.assertFalse(looks_like_person_name("meet our agents"))

    def test_job_title_column_is_not_the_agent_name(self):
        html = f"<html><body><main>{cards([('Jane', 'Smith'), ('John', 'Doe'), ('Ana', 'Garcia')], with_title=True)}</main></body></html>"
        model = analyze_html(html, "https://x.example/agents")[0]
        mapping = suggest_mapping(model)
        row = extract_page(html, "https://x.example/agents", model, mapping)[0]
        self.assertEqual(row["agent_name"], "Jane Smith")
        self.assertEqual(row.get("title"), "Senior Agent")


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
class PaginationV4Tests(unittest.TestCase):
    def test_path_pagination_from_current_url(self):
        plan = detect_pagination(
            "<html><body><main>records</main></body></html>",
            "https://x.example/agents/page/2", "article",
        )
        self.assertEqual(plan.strategy, "path_pages")
        self.assertTrue(plan.page_urls[0].endswith("/agents/page/3"))

    def test_path_pagination_links(self):
        html = ('<html><body><div class="pager">'
                '<a href="/agents/page/1">1</a><a href="/agents/page/2">2</a>'
                '<a href="/agents/page/3">3</a></div></body></html>')
        plan = detect_pagination(html, "https://x.example/agents", "article")
        self.assertEqual(plan.strategy, "numbered")
        self.assertIn("https://x.example/agents/page/2", plan.page_urls)

    def test_load_more_with_a_count_is_detected(self):
        html = '<html><body><button class="more">Load 20 more agents</button></body></html>'
        plan = detect_pagination(html, "https://x.example/agents")
        self.assertEqual(plan.strategy, "load_more")
        self.assertIn("more", plan.selector)

    def test_next_control_identified_by_attribute_and_disabled_is_skipped(self):
        html = ('<html><body><button disabled aria-label="Next">Next</button>'
                '<button data-testid="next-page" title="Next page">›</button></body></html>')
        plan = detect_pagination(html, "https://x.example/agents")
        self.assertEqual(plan.strategy, "next_button")
        self.assertIn("data-testid", plan.selector)

    def test_path_walk_opens_next_page_and_stops_on_empty(self):
        page1 = f'<html><body><main>{cards([("Jane", "Smith"), ("John", "Doe"), ("Ana", "Garcia")])}</main></body></html>'
        page2 = f'<html><body><main>{cards([("Liam", "Brown"), ("Mia", "Wilson"), ("Noah", "Davis")])}</main></body></html>'
        start = "https://x.example/agents/page/1"
        session = FakeBrowserSession(
            [page1], start_url=start,
            by_url={"https://x.example/agents/page/2": page2},
        )
        model = analyze_html(page1, start)[0]
        mapping = suggest_mapping(model)
        plan = detect_pagination(page1, start, model.row_selector)
        plan = dataclasses.replace(plan, strategy="path_pages",
                                   page_urls=["https://x.example/agents/page/2",
                                              "https://x.example/agents/page/3"])
        rows, stats = walk_pages(
            session, plan,
            lambda html, url: extract_page(html, url, model, mapping),
            max_pages=4, row_selector=model.row_selector,
        )
        self.assertEqual(len(rows), 6)
        self.assertEqual(stats.pages, 3)  # page 3 was fetched and contributed nothing


# ---------------------------------------------------------------------------
# Visual picker
# ---------------------------------------------------------------------------
class VisualPickerV4Tests(unittest.TestCase):
    def test_sanitizer_keeps_element_positions_identical(self):
        html = (
            '<html><head><script>var a=1</script><style>.card{color:red}</style></head><body>'
            '<main><div class="grid">'
            '<article class="card"><script>track()</script><h3>Jane Smith</h3>'
            '<a href="tel:5125551000">call</a></article>'
            '<article class="card"><h3>John Doe</h3><a href="tel:5125551001">call</a>'
            '<script>track()</script></article>'
            '<article class="card"><h3>Ana Garcia</h3><span>(512) 555-1002</span></article>'
            "</div></main></body></html>"
        )
        original = analyze_html(html, "https://x.example/agents")[0]
        snapshot, ok = sanitize_snapshot(html, "https://x.example/agents")
        self.assertTrue(ok)
        self.assertNotIn("<script", snapshot.lower())
        self.assertIn("<style", snapshot.lower())
        cleaned = analyze_html(snapshot, "https://x.example/agents")[0]
        self.assertEqual(
            [(column.path, column.kind_hint) for column in original.columns],
            [(column.path, column.kind_hint) for column in cleaned.columns],
        )

    def test_selection_is_mapped_by_snapshot_selector(self):
        html = f"<html><body><main>{cards([('Jane', 'Smith'), ('John', 'Doe'), ('Ana', 'Garcia')])}</main></body></html>"
        model = analyze_html(html, "https://x.example/agents")[0]
        selection = {
            "path": "0",
            "text": "Jane Smith",
            "selector": "article.agent-card:nth-of-type(1) > h3",
        }
        index = map_selection(model, selection, html)
        self.assertIsNotNone(index)
        self.assertEqual(model.column(index).kind_hint, "name")


# ---------------------------------------------------------------------------
# Layered browser extraction and diagnosis
# ---------------------------------------------------------------------------
class BrowserLayerTests(unittest.TestCase):
    def _embedded_page(self):
        agents = [
            {"name": f"{first} {last}", "phone": f"512-555-10{index:02d}",
             "email": f"{first.lower()}@cedarandoak.example", "url": f"/agents/{first.lower()}"}
            for index, (first, last) in enumerate(
                [("Jane", "Smith"), ("John", "Doe"), ("Ana", "Garcia"),
                 ("Liam", "Brown"), ("Mia", "Wilson")], start=1)
        ]
        payload = json.dumps({"props": {"agents": agents}})
        return (
            f'<html><body><main><div class="grid">'
            f'<div class="row"><span>No usable mapping</span></div>'
            f'<div class="row"><span>No usable mapping</span></div>'
            f'<div class="row"><span>No usable mapping</span></div>'
            f'</div></main><script id="__NEXT_DATA__" type="application/json">{payload}</script>'
            f"</body></html>"
        )

    def test_empty_dom_mapping_falls_back_to_embedded_records(self):
        html = self._embedded_page()
        structure = analyze_html(html, "https://x.example/agents")[0]
        session = FakeBrowserSession([html], start_url="https://x.example/agents")
        result = run_browser_extraction(
            "https://x.example/agents",
            session=session,
            structure=structure,
            mapping=FieldMapping({"agent_name": 999}),
            persist=False,
        )
        self.assertEqual(len(result.records), 5)
        self.assertEqual(result.diagnosis.selected_method, "embedded")
        statuses = {attempt.method: attempt.status for attempt in result.diagnosis.attempts}
        self.assertEqual(statuses.get("dom"), "empty")
        self.assertEqual(statuses.get("embedded"), "ok")

    def test_javascript_shell_explains_itself_instead_of_crashing(self):
        html = '<html><head><script src="app.js"></script></head><body><div id="root"></div></body></html>'
        settings = dataclasses.replace(SETTINGS, sitemap_fallback=False)
        session = FakeBrowserSession([html], start_url="https://x.example/agents")
        result = run_browser_extraction(
            "https://x.example/agents", session=session, settings=settings, persist=False
        )
        self.assertEqual(result.records, [])
        self.assertIn(result.diagnosis.failure_code, {"javascript_only", "not_a_listing", "single_profile"})
        self.assertTrue(result.diagnosis.recommendations)
        self.assertIn("dom", {attempt.method for attempt in result.diagnosis.attempts})

    def test_title_extra_is_carried_into_the_export(self):
        html = f"<html><body><main>{cards([('Jane', 'Smith'), ('John', 'Doe'), ('Ana', 'Garcia')], with_title=True)}</main></body></html>"
        session = FakeBrowserSession([html], start_url="https://x.example/agents")
        result = run_browser_extraction(
            "https://x.example/agents", session=session, persist=False
        )
        self.assertEqual(len(result.records), 3)
        self.assertEqual(result.records[0].extra.get("title"), "Senior Agent")
        csv_text = to_csv_bytes(result.records, result.selected_fields).decode("utf-8-sig")
        self.assertIn("Title", csv_text.splitlines()[0])
        self.assertIn("Senior Agent", csv_text)


class ExportDiagnosisTests(unittest.TestCase):
    def test_formula_like_values_are_escaped_but_phones_are_not(self):
        from core.exporters import records_to_frame
        from core.models import RawRecord

        record = build_record(
            RawRecord(fields={"agent_name": "=HYPERLINK(\"http://bad\")",
                              "phone": "+1 (512) 555-1000"}),
            ["agent_name", "phone"], "test",
        )
        frame = records_to_frame([record], ["agent_name", "phone"])
        self.assertTrue(frame.iloc[0]["Agent name"].startswith("'="))
        self.assertEqual(frame.iloc[0]["Public phone"], "+1 (512) 555-1000")

    def test_excel_summary_contains_method_attempts(self):
        result = RunResult(url="https://x.example/agents", adapter_id="browser_dom",
                           adapter_label="Browser DOM extraction")
        result.quality = evaluate([], 0, 0, ("agent_name",), SETTINGS)
        result.diagnosis = ExtractionDiagnosis()
        result.diagnosis.add("dom", "Rendered DOM cards", "empty", 0, "No rows.")
        result.diagnosis.add("embedded", "Embedded JSON (payload)", "ok", 12, "12 records.")
        result.diagnosis.selected_method = "embedded"
        result.diagnosis.selected_label = "Embedded JSON (payload)"
        data = to_excel_bytes(result, ["agent_name"])
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(data))
        summary = " ".join(
            str(cell) for row in workbook["Run Summary"].iter_rows(values_only=True)
            for cell in row if cell
        )
        self.assertIn("Extraction methods tried", summary)
        self.assertIn("Rendered DOM cards", summary)
        self.assertIn("Embedded JSON (payload)", summary)


# ---------------------------------------------------------------------------
# Real HTTP static integration (loopback only)
# ---------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    routes = {}

    def do_GET(self):
        path = urlparse(self.path).path
        status, body = getattr(self.server, "routes", {}).get(path, (404, "not found"))
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if status != 200 or path != "/sitemap.xml"
                         else "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return None


def _page_cards(names, page):
    links = "".join(
        f'<a class="card-link" href="/people/{first.lower()}-{last.lower()}">profile</a>'
        for first, last in names
    )
    body = "".join(
        f'<article class="agent-card"><h3>{first} {last}</h3>'
        f'<p>(512) 555-10{index:02d}</p><p>{first.lower()}@cedarandoak.example</p>'
        f'<p>Cedar & Oak Realty</p></article>'
        for index, (first, last) in enumerate(names, start=1)
    )
    next_link = f'<a class="next" href="/paged/page/{page + 1}">Next</a>' if page < 2 else ""
    return (f'<html><body><main><h1>Our agents</h1><div class="grid">{body}</div>'
            f'{next_link}</main>{links}</body></html>')


def _profile(first, last):
    payload = {
        "@context": "https://schema.org",
        "@type": "RealEstateAgent",
        "name": f"{first} {last}",
        "telephone": "(512) 555-0199",
        "email": f"{first.lower()}@cedarandoak.example",
        "worksFor": {"@type": "Organization", "name": "Cedar & Oak Realty"},
    }
    return (
        f'<html><head><script type="application/ld+json">{json.dumps(payload)}</script></head>'
        f"<body><main><h1>{first} {last}</h1><p>Public profile.</p></main></body></html>"
    )


class StaticIntegrationV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 127.0.0.2 is loopback but is not the configured demo adapter's domain;
        # keep it out of any proxy so the request reaches the local server.
        for key in ("NO_PROXY", "no_proxy"):
            current = os.environ.get(key, "")
            if "127.0.0.2" not in current:
                os.environ[key] = (current + ",127.0.0.2").lstrip(",")
        names1 = [("Jane", "Smith"), ("John", "Doe"), ("Ana", "Garcia"),
                  ("Liam", "Brown"), ("Mia", "Wilson")]
        names2 = [("Noah", "Davis"), ("Emma", "Miller"), ("Lucas", "Moore"),
                  ("Sofia", "Taylor"), ("Ethan", "Anderson")]
        people = names1 + names2
        agents = [
            {"name": f"{first} {last}", "phone": f"512-555-10{index:02d}",
             "email": f"{first.lower()}@cedarandoak.example", "url": f"/people/{first.lower()}",
             "street_address": "3300 Bee Caves Rd", "city": "Austin", "state": "TX",
             "postal_code": "78746"}
            for index, (first, last) in enumerate(people, start=1)
        ]
        embedded = (
            f'<html><body><div id="root"></div>'
            f'<script id="__NEXT_DATA__" type="application/json">'
            f'{json.dumps({"props": {"pageProps": {"agents": agents}}})}</script></body></html>'
        )
        sitemap = (
            '<?xml version="1.0" encoding="UTF-8"?><urlset>'
            + "".join(f"<url><loc>http://HOST/people/{first.lower()}-{last.lower()}</loc></url>"
                      for first, last in [("Jane", "Smith"), ("John", "Doe"), ("Ana", "Garcia")])
            + "</urlset>"
        )
        filler = "Public brokerage directory. " * 30
        cls.routes = {
            "/embedded": (200, embedded),
            "/paged/page/1": (200, _page_cards(names1, 1)),
            "/paged/page/2": (200, _page_cards(names2, 2)),
            "/sitemap-only": (200, f"<html><body><main><h1>Agents</h1><p>{filler}</p></main></body></html>"),
            "/sitemap.xml": (200, sitemap),
            "/forbidden": (403, "forbidden"),
        }
        for index, (first, last) in enumerate(
            [("Jane", "Smith"), ("John", "Doe"), ("Ana", "Garcia")], start=1
        ):
            cls.routes[f"/people/{first.lower()}-{last.lower()}"] = (200, _profile(first, last))
        cls.server = ThreadingHTTPServer(("0.0.0.0", 0), _Handler)
        cls.server.routes = cls.routes  # type: ignore[attr-defined]
        cls.port = cls.server.server_address[1]
        cls.host = f"127.0.0.2:{cls.port}"
        client_routes = {
            path: (status, body.replace("http://HOST", f"http://{cls.host}"))
            for path, (status, body) in cls.routes.items()
        }
        cls.server.routes = client_routes  # type: ignore[attr-defined]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.settings = dataclasses.replace(
            SETTINGS, allow_local_urls=True, request_delay_seconds=0, max_retries=0
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def url(self, path):
        return f"http://{self.host}{path}"

    def test_static_extracts_records_from_embedded_json(self):
        result = run_extraction(self.url("/embedded"), settings=self.settings, persist=False)
        self.assertEqual(len(result.records), 10)
        self.assertEqual(result.diagnosis.selected_method, "embedded_listing")
        self.assertTrue(any("embedded" in layer for layer in result.inspection.detected_layers))
        # Flat payload fields (street_address/city/state/postal_code) must survive.
        self.assertTrue(result.records[0].street_address)
        self.assertEqual(result.records[0].city, "Austin")
        self.assertEqual(result.records[0].state, "TX")

    def test_static_follows_path_pagination(self):
        result = run_extraction(self.url("/paged/page/1"), settings=self.settings, persist=False)
        self.assertEqual(result.pages_fetched, 2)
        self.assertEqual(len(result.records), 10)
        self.assertEqual(result.records[0].agent_name, "Jane Smith")

    def test_static_uses_sitemap_when_listing_has_no_links(self):
        result = run_extraction(self.url("/sitemap-only"), settings=self.settings, persist=False)
        self.assertEqual(len(result.records), 3)
        self.assertEqual(result.diagnosis.selected_method, "sitemap_profiles")
        self.assertEqual(result.records[0].phone, "(512) 555-0199")

    def test_static_respects_http_403(self):
        with self.assertRaises(BlockedError):
            run_extraction(self.url("/forbidden"), settings=self.settings, persist=False)


if __name__ == "__main__":
    unittest.main()
