"""Breadth tests for layouts that commonly defeat naive scrapers:
unique per-card classes, icon-only contact links, embedded JSON vs DOM, and
arbitrary extra columns in the browser engine.
"""
from __future__ import annotations

import json
import unittest

from tests.fake_browser import FakeBrowserSession
from tests.helpers import FakeSite, profile_html

from core.browser.engine import run_browser_extraction
from core.browser.extract import extract_page, suggest_mapping
from core.browser.structure import analyze_html, select_rows
from scripts.serve_demo import listing_html

URL = "https://breadth.example/agents"
NAMES = ["Jane Smith", "Marcus Lee", "Priya Nair", "Dana Whitfield", "Omar Haddad"]


def _unique_cards() -> str:
    return ('<html><body><main><div class="grid">' + "".join(
        f'<div class="card-{i}"><h3>{name}</h3>'
        f'<a href="/agents/{i}">View profile</a></div>'
        for i, name in enumerate(NAMES)
    ) + "</div></main></body></html>")


def _icon_contacts() -> str:
    return ('<html><body><main><div class="grid">' + "".join(
        f'<article class="card"><h3>{name}</h3>'
        f'<a class="icon phone" href="tel:+1512555{1000 + i}"><svg></svg></a>'
        f'<a class="icon email" href="mailto:person{i}@breadth.example"><i></i></a>'
        f'<a href="/agents/{i}">View profile</a></article>'
        for i, name in enumerate(NAMES)
    ) + "</div></main></body></html>")


class LayoutBreadthTests(unittest.TestCase):
    def test_unique_classes_per_card_still_produce_a_repeatable_row_selector(self):
        html = _unique_cards()
        model = analyze_html(html, URL)[0]
        self.assertEqual(model.row_count, 5)
        self.assertIn(">", model.row_selector)
        self.assertEqual(len(select_rows(html, model)), 5)
        rows = extract_page(html, URL, model, suggest_mapping(model))
        self.assertEqual([row["agent_name"] for row in rows], NAMES)
        self.assertTrue(rows[0]["profile_url"].endswith("/agents/0"))

    def test_icon_only_contact_links_are_profiled_from_href(self):
        html = _icon_contacts()
        model = analyze_html(html, URL)[0]
        kinds = {column.kind_hint for column in model.columns}
        self.assertIn("name", kinds)
        self.assertIn("phone", kinds)
        self.assertIn("email", kinds)
        rows = extract_page(html, URL, model, suggest_mapping(model))
        self.assertEqual(rows[0]["phone"], "+1 (512) 555-1000")
        self.assertEqual(rows[0]["email"], "person0@breadth.example")

    def test_static_inspection_falls_back_for_unique_classes(self):
        from core.inspect_site import inspect_page
        from core.net import FetchedPage

        html = _unique_cards()
        page = FetchedPage(url=URL, final_url=URL, status_code=200, text=html)
        report = inspect_page(page, check_sitemap=False)
        self.assertEqual(report.profile_link_count, 5)
        self.assertEqual(report.container_count, 5)
        self.assertTrue(report.container_signature)
        self.assertIn(">", report.container_selector)

    def test_static_extraction_works_for_unique_class_cards(self):
        from core.engine import run_extraction

        routes = {URL: _unique_cards()}
        for index, name in enumerate(NAMES):
            routes[f"https://breadth.example/agents/{index}"] = profile_html(
                name, f"512-555-{1000 + index}", f"person{index}@breadth.example",
                "Breadth Realty",
            )
        site = FakeSite(routes)
        result = run_extraction(
            URL, fetcher=site.fetcher(), max_list_pages=2, max_profiles=10, persist=False
        )
        self.assertEqual(len(result.records), 5)
        self.assertEqual({record.agent_name for record in result.records}, set(NAMES))

    def test_data_attribute_phone_is_profiled(self):
        html = ('<html><body><main><div class="grid">' + "".join(
            f'<article class="card"><h3>{name}</h3>'
            f'<span data-phone="(512) 555-{1000 + i}"></span>'
            f'<a href="/agents/{i}">View profile</a></article>'
            for i, name in enumerate(NAMES)
        ) + "</div></main></body></html>")
        model = analyze_html(html, URL)[0]
        phone_col = next((c for c in model.columns if c.kind_hint == "phone"), None)
        self.assertIsNotNone(phone_col)
        self.assertIn("512", phone_col.sample_values[0])


class JsonLdListingTests(unittest.TestCase):
    def test_complete_person_nodes_on_the_listing_are_used_without_profiles(self):
        from core.engine import run_extraction

        people = [
            {
                "@type": "Person",
                "name": name,
                "telephone": f"512-555-{1000 + index}",
                "email": f"person{index}@breadth.example",
                "url": f"https://breadth.example/agents/{index}",
                "jobTitle": "Agent",
            }
            for index, name in enumerate(NAMES)
        ]
        payload = {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "item": person}
                for i, person in enumerate(people)
            ],
        }
        html = (
            '<html><head><script type="application/ld+json">'
            + json.dumps(payload)
            + '</script></head><body><main><div class="grid">'
            + "".join(f'<div class="card"><a href="/agents/{i}">{name}</a></div>'
                      for i, name in enumerate(NAMES))
            + "</div></main></body></html>"
        )
        site = FakeSite({URL: html})
        result = run_extraction(URL, fetcher=site.fetcher(), max_profiles=10, persist=False)
        self.assertEqual(result.adapter_id, "generic_jsonld")
        self.assertEqual(len(result.records), 5)
        self.assertEqual(result.profiles_attempted, 0)
        self.assertEqual(result.records[0].extra.get("title"), "Agent")
        self.assertTrue(result.records[0].profile_url.endswith("/agents/0"))


class EmbeddedPreferenceTests(unittest.TestCase):
    def _payload_html(self, records: int = 5) -> str:
        agents = [
            {
                "fullName": f"Agent {i} Name",
                "email": f"agent{i}@embedded.example",
                "phone": f"512555{1000 + i}",
                "jobTitle": "Agent",
            }
            for i in range(records)
        ]
        return ('<html><body><main><div class="grid">' + "".join(
            f'<article class="card"><h3>Card {i}</h3><a href="/agents/{i}">View</a></article>'
            for i in range(3)
        ) + f'</div></main><script id="__NEXT_DATA__" type="application/json">'
            f'{json.dumps({"props": {"agents": agents}})}</script></body></html>')

    def test_embedded_payload_is_preferred_when_it_has_more_records(self):
        html = self._payload_html(5)
        session = FakeBrowserSession([html], start_url=URL)
        result = run_browser_extraction(URL, session=session, max_pages=1, persist=False)
        self.assertEqual(result.adapter_id, "embedded_json")
        self.assertEqual(len(result.records), 5)
        self.assertTrue(result.records[0].email.endswith("@embedded.example"))

    def test_navigation_payload_is_ignored_and_dom_is_used(self):
        menu = [{"name": f"Menu {i}", "url": f"/page/{i}"} for i in range(6)]
        html = ('<html><body><main><div class="grid">' + "".join(
            f'<article class="card"><h3>{name}</h3>'
            f'<a href="/agents/{i}">View profile</a></article>'
            for i, name in enumerate(NAMES)
        ) + f'</div></main><script type="application/json">{json.dumps(menu)}</script></body></html>')
        session = FakeBrowserSession([html], start_url=URL)
        result = run_browser_extraction(URL, session=session, max_pages=1, persist=False)
        self.assertEqual(result.adapter_id, "browser_dom")
        self.assertEqual(len(result.records), 5)

    def test_browser_engine_keeps_extra_columns(self):
        html = listing_html(1)  # the demo page has title and licence columns
        model = analyze_html(html, "http://127.0.0.1:8765/agents")[0]
        mapping = suggest_mapping(model)
        title_col = next(c.index for c in model.columns if "Senior Agent" in c.sample_values)
        license_col = next(c.index for c in model.columns if "TX-100000" in c.sample_values)
        mapping.columns["title"] = title_col
        mapping.columns["license_number"] = license_col

        session = FakeBrowserSession([html], start_url="http://127.0.0.1:8765/agents")
        result = run_browser_extraction(
            "http://127.0.0.1:8765/agents",
            session=session, structure=model, mapping=mapping,
            max_pages=1, persist=False,
        )
        self.assertEqual(len(result.records), 12)
        self.assertEqual(result.records[0].extra["title"], "Senior Agent")
        self.assertEqual(result.records[0].extra["license_number"], "TX-100000")


if __name__ == "__main__":
    unittest.main()
