"""Offline test harness: a fake website served from fixture files.

No network and no Streamlit are required to run these tests.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.net import PoliteFetcher  # noqa: E402
from core.settings import SETTINGS  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_settings(**overrides):
    base = {
        "request_delay_seconds": 0.0,
        "max_retries": 0,
        "respect_robots_txt": True,
    }
    base.update(overrides)
    return dataclasses.replace(SETTINGS, **base)


class FakeSite:
    """Callable transport. `routes` maps an absolute URL to HTML (or (status, html))."""

    def __init__(self, routes: dict[str, object], robots: str | None = None):
        self.routes = routes
        self.robots = robots
        self.requests: list[str] = []

    def __call__(self, url, headers, timeout, max_bytes):
        self.requests.append(url)
        if url.endswith("/robots.txt"):
            if self.robots is None:
                return 404, url, {"Content-Type": "text/plain"}, "not found"
            return 200, url, {"Content-Type": "text/plain"}, self.robots
        entry = self.routes.get(url)
        if entry is None:
            return 404, url, {"Content-Type": "text/html"}, "<html><body>not found</body></html>"
        if isinstance(entry, tuple):
            status, body = entry
            return status, url, {"Content-Type": "text/html"}, body
        return 200, url, {"Content-Type": "text/html"}, entry

    def fetcher(self, **overrides) -> PoliteFetcher:
        return PoliteFetcher(settings=test_settings(**overrides), transport=self, sleep=lambda _s: None)


def profile_html(name: str, phone: str = "", email: str = "", org: str = "Cedar & Oak Realty",
                 address: str = "") -> str:
    """A plain (non-JSON-LD) profile page used by the HTML adapter tests."""
    contact = ""
    if phone:
        contact += f'<a href="tel:{phone}">{phone}</a>'
    if email:
        contact += f'<a href="mailto:{email}">Email</a>'
    if address:
        contact += f"<address>{address}</address>"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{name} | {org}</title>
<meta property="og:site_name" content="{org}"></head>
<body><nav><a href="/">Home</a></nav>
<main class="agent-profile">
  <h1>{name}</h1>
  <div class="agent-contact">{contact}</div>
  <div class="brokerage">{org}</div>
</main>
<footer><p>{org} · (512) 555-0100</p></footer>
</body></html>"""


def jsonld_profile_html(name: str, phone: str, email: str, org: str,
                        street: str, city: str, state: str, postal: str, url: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{name} | {org}</title>
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"RealEstateAgent","name":"{name}",
"telephone":"{phone}","email":"{email}","url":"{url}",
"worksFor":{{"@type":"Organization","name":"{org}"}},
"address":{{"@type":"PostalAddress","streetAddress":"{street}","addressLocality":"{city}",
"addressRegion":"{state}","postalCode":"{postal}"}}}}
</script></head>
<body><main><h1>{name}</h1><a href="tel:{phone}">{phone}</a>
<a href="mailto:{email}">Email</a></main></body></html>"""
