"""Inspection + end-to-end extraction tests, run entirely offline."""
from __future__ import annotations

import unittest

from tests.helpers import FakeSite, fixture, jsonld_profile_html, profile_html

from core.engine import run_extraction
from core.inspect_site import inspect_page
from core.net import RobotsDisallowed
from core.adapters.registry import resolve_adapter

BASE = "https://cedarandoak.example"


def _roster_site() -> FakeSite:
    agents = {
        "jane-smith": ("Jane Smith, ABR, CRS", "+1-512-555-0142", "jane.smith@cedarandoak.example",
                       "3300 Bee Caves Rd, Suite 210", "Austin", "TX", "78746"),
        "marcus-lee": ("Marcus Lee", "+1-512-555-0188", "", "901 Round Rock Ave", "Round Rock", "TX", "78681"),
        "priya-nair": ("Priya Nair", "+1-512-555-0119", "priya@cedarandoak.example",
                       "3300 Bee Caves Rd, Suite 210", "Austin", "TX", "78746"),
        "dana-whitfield": ("Dana Whitfield", "+1-512-555-0173", "", "1200 Cedar Park Dr", "Cedar Park", "TX", "78613"),
        "omar-haddad": ("Omar Haddad", "+1-512-555-0164", "", "901 Round Rock Ave", "Round Rock", "TX", "78681"),
        "lucia-fernandez": ("Lucia Fernandez", "+1-512-555-0155", "lucia@cedarandoak.example",
                            "3300 Bee Caves Rd, Suite 210", "Austin", "TX", "78746"),
    }
    routes = {f"{BASE}/agents": fixture("roster_jsonld.html")}
    for slug, (name, phone, email, street, city, state, postal) in agents.items():
        routes[f"{BASE}/agents/{slug}"] = jsonld_profile_html(
            name, phone, email, "Cedar & Oak Realty", street, city, state, postal,
            f"{BASE}/agents/{slug}",
        )
    # Page 2 intentionally returns the same six agents: proves dedupe across pages.
    routes[f"{BASE}/agents?page=2"] = fixture("roster_jsonld.html")
    return FakeSite(routes)


class InspectionTests(unittest.TestCase):
    def test_detects_repeated_blocks_and_pagination(self):
        site = _roster_site()
        page = site.fetcher().get(f"{BASE}/agents")
        report = inspect_page(page, check_sitemap=False)

        self.assertEqual(report.container_count, 6)
        self.assertEqual(report.profile_link_count, 6)
        self.assertEqual(report.pagination_type, "next_link")
        self.assertIn("itemlist", [t.lower() for t in report.jsonld_types])
        self.assertEqual(report.recommended_adapter, "generic_jsonld")
        self.assertEqual(report.container_selector, "div.agent-card")
        self.assertTrue(report.container_signature)
        self.assertFalse(report.requires_javascript)

    def test_flags_javascript_shell(self):
        site = FakeSite({"https://spa.example/agents": fixture("js_shell.html")})
        page = site.fetcher().get("https://spa.example/agents")
        report = inspect_page(page, check_sitemap=False)

        self.assertEqual(report.profile_link_count, 0)
        self.assertTrue(report.requires_javascript)
        self.assertTrue(any("JavaScript" in w for w in report.warnings))

    def test_plain_roster_recommends_html_adapter(self):
        site = FakeSite({"https://harborline.example/team": fixture("roster_plain.html")})
        page = site.fetcher().get("https://harborline.example/team")
        report = inspect_page(page, check_sitemap=False)

        self.assertEqual(report.container_count, 4)
        self.assertEqual(report.recommended_adapter, "generic_html")


class ExtractionTests(unittest.TestCase):
    def _run(self, site: FakeSite, url: str):
        return run_extraction(
            url,
            fetcher=site.fetcher(),
            max_list_pages=3,
            max_profiles=20,
            persist=False,
        )

    def test_jsonld_end_to_end(self):
        site = _roster_site()
        result = self._run(site, f"{BASE}/agents")

        self.assertEqual(result.adapter_id, "generic_jsonld")
        self.assertEqual(len(result.records), 6, "six agents expected, two pages of identical links")
        # Page 2 repeats page 1's links; those are collapsed at link level before
        # any profile is fetched again, so no duplicate records exist.
        self.assertEqual(result.quality.duplicates_removed, 0)
        self.assertEqual(result.quality.level, "ok")
        # Regression: the JSON-LD adapter used to inherit a no-op paginator, so
        # page 2 was never fetched and the run silently looked complete.
        self.assertEqual(result.pages_fetched, 2)

        jane = next(r for r in result.records if r.agent_name.startswith("Jane Smith"))
        self.assertEqual(jane.agent_name, "Jane Smith", "credentials must be stripped")
        self.assertEqual(jane.brokerage_name, "Cedar & Oak Realty")
        self.assertEqual(jane.phone, "+1 (512) 555-0142")
        self.assertEqual(jane.email, "jane.smith@cedarandoak.example")
        self.assertEqual(jane.city, "Austin")
        self.assertEqual(jane.state, "TX")
        self.assertEqual(jane.postal_code, "78746")
        self.assertEqual(jane.street_address, "3300 Bee Caves Rd, Suite 210")
        self.assertTrue(jane.profile_url.endswith("/agents/jane-smith"))
        self.assertTrue(jane.collected_at)

    def test_plain_html_end_to_end(self):
        routes = {
            "https://harborline.example/team": fixture("roster_plain.html"),
            "https://harborline.example/team/elena-marchetti": profile_html(
                "Elena Marchetti", "617-555-0101", "", "Harborline Properties"),
            "https://harborline.example/team/david-okafor": profile_html(
                "David Okafor", "", "david.okafor@harborline.example", "Harborline Properties"),
            "https://harborline.example/team/sara-benitez": profile_html(
                "Sara Benitez", "617-555-0133", "sara.benitez@harborline.example", "Harborline Properties"),
            "https://harborline.example/team/tom-velasquez": profile_html(
                "Tom Velasquez", "", "", "Harborline Properties"),
        }
        site = FakeSite(routes)
        result = self._run(site, "https://harborline.example/team")

        self.assertEqual(result.adapter_id, "generic_html")
        self.assertEqual(len(result.records), 4)
        names = {r.agent_name for r in result.records}
        self.assertIn("Elena Marchetti", names)
        self.assertIn("Tom Velasquez", names)

        tom = next(r for r in result.records if r.agent_name == "Tom Velasquez")
        self.assertIn("no contact details", tom.quality_flags)
        # The brokerage switchboard in the footer must not be attributed to him.
        self.assertEqual(tom.phone, "")
        self.assertTrue(any("phone or email" in w.lower() for w in result.quality.warnings))

    def test_robots_disallow_blocks_run(self):
        site = _roster_site()
        site.robots = "User-agent: *\nDisallow: /agents\n"
        with self.assertRaises(RobotsDisallowed):
            run_extraction(f"{BASE}/agents", fetcher=site.fetcher(), persist=False)

    def test_js_site_reports_broken_instead_of_empty(self):
        site = FakeSite({"https://spa.example/agents": fixture("js_shell.html")})
        result = self._run(site, "https://spa.example/agents")

        self.assertEqual(result.quality.level, "broken")
        self.assertEqual(result.records, [])
        self.assertTrue(any("JavaScript" in e for e in result.errors))

    def test_never_leaves_the_source_host(self):
        roster = fixture("roster_jsonld.html").replace(
            "</div>\n        <nav", "</div>\n        <div class='agent-card'>"
            "<a class='agent-card__link' href='https://evil.example/agents/x'>X</a></div>\n        <nav"
        )
        site = _roster_site()
        site.routes[f"{BASE}/agents"] = roster
        result = run_extraction(f"{BASE}/agents", fetcher=site.fetcher(), persist=False)
        self.assertFalse(any("evil.example" in r.profile_url for r in result.records))
        self.assertFalse(any("evil.example" in u for u in site.requests))


SMALL_ROSTER = """<!DOCTYPE html><html><head><title>Our Team</title></head><body>
<main><div class="team">
  <div class="member"><a href="/team/anna-ruiz">Anna Ruiz</a></div>
  <div class="member"><a href="/team/ben-cole">Ben Cole</a></div>
</div></main></body></html>"""

BLOG_ROSTER = """<!DOCTYPE html><html><head><title>News</title></head><body>
<main><div class="posts">
  <div class="post"><a href="/news/spring-market-2026">Spring market</a></div>
  <div class="post"><a href="/news/rate-outlook">Rate outlook</a></div>
  <div class="post"><a href="/news/new-office">New office</a></div>
</div></main></body></html>"""

FOOTER_YEAR = """<!DOCTYPE html><html><head><title>Agents</title></head><body>
<main><div class="grid">
  <div class="c"><a href="/agents/a-one">A One</a></div>
  <div class="c"><a href="/agents/b-two">B Two</a></div>
</div></main>
<footer><a href="/terms">Terms</a><a href="/archive/2026">2026</a></footer></body></html>"""


class EdgeCaseTests(unittest.TestCase):
    def test_two_agent_page_is_detected(self):
        site = FakeSite({"https://small.example/team": SMALL_ROSTER})
        page = site.fetcher().get("https://small.example/team")
        report = inspect_page(page, check_sitemap=False)
        self.assertEqual(report.profile_link_count, 2)
        self.assertEqual(report.container_count, 2)

    def test_blog_listing_is_not_a_roster(self):
        site = FakeSite({"https://blog.example/news": BLOG_ROSTER})
        page = site.fetcher().get("https://blog.example/news")
        report = inspect_page(page, check_sitemap=False)
        self.assertEqual(report.profile_link_count, 0)

    def test_blog_listing_does_not_become_records(self):
        site = FakeSite({"https://blog.example/news": BLOG_ROSTER})
        result = run_extraction("https://blog.example/news", fetcher=site.fetcher(), persist=False)
        self.assertEqual(result.records, [])
        self.assertEqual(result.quality.level, "broken")

    def test_footer_year_is_not_pagination(self):
        from core.adapters.generic_html import next_page_urls_from_soup
        from core.inspect_site import parse_html

        soup = parse_html(FOOTER_YEAR)
        urls = next_page_urls_from_soup(soup, "https://x.example/agents")
        self.assertFalse(any("2026" in u for u in urls), urls)
        self.assertFalse(any("archive" in u for u in urls), urls)


class PersistenceTests(unittest.TestCase):
    """Regression: summaries used to be written before health was computed."""

    def test_persisted_summary_carries_computed_health(self):
        import tempfile
        from pathlib import Path

        from core import runs as runs_module

        site = _roster_site()
        with tempfile.TemporaryDirectory() as tmp:
            original = runs_module.HISTORY_FILE
            runs_module.HISTORY_FILE = Path(tmp) / "history.jsonl"
            try:
                run_extraction(f"{BASE}/agents", fetcher=site.fetcher(), max_profiles=2, persist=True)
                entry = runs_module.load_history()[0]
            finally:
                runs_module.HISTORY_FILE = original

        self.assertEqual(entry["health_status"], "first_run",
                         "a default 'ok' here means health was computed after the summary was built")

    def test_javascript_site_persists_broken_health(self):
        import tempfile
        from pathlib import Path

        from core import runs as runs_module

        site = FakeSite({"https://spa.example/agents": fixture("js_shell.html")})
        with tempfile.TemporaryDirectory() as tmp:
            original = runs_module.HISTORY_FILE
            runs_module.HISTORY_FILE = Path(tmp) / "history.jsonl"
            try:
                run_extraction("https://spa.example/agents", fetcher=site.fetcher(), persist=True)
                entry = runs_module.load_history()[0]
            finally:
                runs_module.HISTORY_FILE = original

        self.assertEqual(entry["health_status"], "broken")


class AdapterResolutionTests(unittest.TestCase):
    def test_jsonld_preferred_when_recommended(self):
        adapter = resolve_adapter(BASE + "/agents", "", "generic_jsonld")
        self.assertEqual(adapter.id, "generic_jsonld")

    def test_generic_is_the_default(self):
        self.assertEqual(resolve_adapter("https://unknown.example/agents").id, "generic_html")

    def test_template_adapter_claims_nothing(self):
        self.assertEqual(resolve_adapter("https://cedarandoak.example/agents").id, "generic_html")


if __name__ == "__main__":
    unittest.main()
