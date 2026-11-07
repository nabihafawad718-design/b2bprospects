#!/usr/bin/env python3
"""Serve a local, realistic directory for end-to-end testing and demos.

    python scripts/serve_demo.py --port 8765

Then, in another terminal, start the dashboard with local URLs allowed:

    Windows PowerShell:  $env:B2B_ALLOW_LOCAL="true"; streamlit run app.py
    Windows cmd:         set B2B_ALLOW_LOCAL=true && streamlit run app.py
    macOS / Linux:       B2B_ALLOW_LOCAL=true streamlit run app.py

Enter http://127.0.0.1:8765/agents as the directory URL.

The demo has 36 agents across three pages, individual profiles, schema.org data,
a site-specific JSON adapter (core/adapters/sites/demo-brokerage.json), extra
columns (title, licence number) and a profile email that is only revealed by an
ordinary "Show email" click. It is deliberately small and deterministic.
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]

FIRST_NAMES = [
    "Jane", "Marcus", "Priya", "Dana", "Omar", "Lucia", "Ethan", "Sofia",
    "Noah", "Amara", "Liam", "Maya", "Elena", "David", "Sara", "Tom",
    "Aisha", "Ben", "Chloe", "Diego", "Emma", "Farah", "Grace", "Hassan",
    "Isabel", "Jonah", "Kira", "Lucas", "Mina", "Nadia", "Owen", "Paula",
    "Quinn", "Rafael", "Sana", "Theo",
]
LAST_NAMES = [
    "Smith", "Lee", "Nair", "Whitfield", "Haddad", "Fernandez", "Brooks", "Rossi",
    "Kim", "Okafor", "Murphy", "Patel", "Marchetti", "Okafor", "Benitez", "Velasquez",
    "Rahman", "Cole", "Dubois", "Alvarez", "Wilson", "Khan", "Turner", "Ali",
    "Costa", "Bennett", "Novak", "Silva", "Park", "Hughes", "Foster", "Mendes",
    "Harper", "Moreno", "Iqbal", "Bauer",
]
OFFICES = [
    ("3300 Bee Caves Rd, Suite 210", "Austin", "TX", "78746"),
    ("901 Round Rock Ave", "Round Rock", "TX", "78681"),
    ("1200 Cedar Park Dr", "Cedar Park", "TX", "78613"),
    ("455 Congress Ave", "Austin", "TX", "78701"),
    ("77 Lakeline Blvd", "Austin", "TX", "78717"),
    ("2100 Slaughter Ln", "Austin", "TX", "78748"),
]
TITLES = [
    "Senior Agent", "Broker Associate", "Listing Specialist", "Buyer's Agent",
    "Team Lead", "Relocation Specialist", "Luxury Advisor", "Commercial Advisor",
]
PAGE_SIZE = 12


def _slug(first: str, last: str) -> str:
    return f"{first}-{last}".lower()


def agents() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for index in range(36):
        first = FIRST_NAMES[index % len(FIRST_NAMES)]
        last = LAST_NAMES[index % len(LAST_NAMES)]
        street, city, state, postal = OFFICES[index % len(OFFICES)]
        phone = f"(512) 555-{1000 + index:04d}"
        email = f"{first}.{last}{index if index >= len(FIRST_NAMES) else ''}@demo-brokerage.example".lower()
        out.append({
            "slug": _slug(first, last) + (f"-{index}" if index >= len(FIRST_NAMES) else ""),
            "name": f"{first} {last}",
            "brokerage": "Demo & Co. Realty",
            "phone": phone,
            "email": email,
            "street": street,
            "city": city,
            "state": state,
            "postal": postal,
            "title": TITLES[index % len(TITLES)],
            "license": f"TX-{100000 + index}",
        })
    return out


AGENTS = agents()
BY_SLUG = {agent["slug"]: agent for agent in AGENTS}
PAGE_COUNT = (len(AGENTS) + PAGE_SIZE - 1) // PAGE_SIZE

BASE_CSS = """
:root{--ink:#1f2440;--muted:#64748b;--line:#e5e7eb;--accent:#5b2fd6;--bg:#f6f5fb}
*{box-sizing:border-box}body{margin:0;font-family:Inter,"Segoe UI",system-ui,sans-serif;color:var(--ink);background:var(--bg)}
header{background:#fff;border-bottom:1px solid var(--line)}.wrap{max-width:1100px;margin:0 auto;padding:0 22px}
.brand{display:flex;align-items:center;gap:10px;padding:18px 0;font-weight:800}.mark{width:34px;height:34px;border-radius:10px;background:var(--accent);color:#fff;display:grid;place-items:center}
.hero{padding:38px 0 18px}h1{font-size:38px;margin:0 0 8px}p.lede{color:var(--muted);margin:0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:16px;padding:24px 0 40px}
.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 4px 18px rgba(31,36,64,.04)}
.card h3{margin:0 0 4px;font-size:18px}.card a{color:var(--ink);text-decoration:none}.card a:hover{color:var(--accent)}
.office{color:var(--muted);font-size:13px;margin:0 0 10px}.row{font-size:13px;margin:4px 0}.row a{color:var(--accent)}
.addr{font-size:12.5px;color:var(--muted);margin-top:10px;line-height:1.45}
.pill{display:inline-block;font-size:11px;font-weight:700;color:var(--accent);background:rgba(91,47,214,.09);border:1px solid rgba(91,47,214,.2);padding:3px 8px;border-radius:999px;margin-bottom:10px}
.pager{display:flex;gap:8px;justify-content:center;padding:0 0 50px}.pager a,.pager span{padding:8px 13px;border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--ink);text-decoration:none;font-size:13px}.pager a.next{background:var(--accent);color:#fff;border-color:var(--accent)}
.profile{background:#fff;border:1px solid var(--line);border-radius:16px;padding:28px;margin:26px 0 50px}
.profile h1{margin:0}.kv{display:grid;grid-template-columns:150px 1fr;gap:8px 18px;margin-top:20px;font-size:14px}.kv dt{color:var(--muted)}.kv dd{margin:0}
button{font:inherit;font-weight:700;font-size:13px;color:#fff;background:var(--accent);border:0;border-radius:8px;padding:9px 13px;cursor:pointer}
footer{color:var(--muted);font-size:12px;padding:25px 0 40px;border-top:1px solid var(--line)}
table.roster{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden;margin:10px 0 40px}table.roster th,table.roster td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;font-size:13px}table.roster th{background:#faf9ff;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
"""


def _shell(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} | Demo &amp; Co. Realty</title>
<style>{BASE_CSS}</style></head>
<body>
<header><div class="wrap"><div class="brand"><span class="mark">D</span>Demo &amp; Co. Realty</div></div></header>
<div class="wrap">{body}</div>
<footer><div class="wrap">Local demo directory · {len(AGENTS)} agents · for B2BProspects testing</div></footer>
</body></html>"""


def listing_html(page: int) -> str:
    start = (page - 1) * PAGE_SIZE
    chunk = AGENTS[start:start + PAGE_SIZE]
    cards = []
    for agent in chunk:
        cards.append(f"""
<article class="demo-card">
  <span class="pill">{agent['title']}</span>
  <h3><a class="demo-card__name demo-card__link" href="/agents/{agent['slug']}">{agent['name']}</a></h3>
  <p class="demo-card__office office">{agent['brokerage']}</p>
  <p class="row"><a class="demo-card__phone" href="tel:+1{agent['phone'].replace('(', '').replace(')', '').replace(' ', '').replace('-', '')}">{agent['phone']}</a></p>
  <p class="row"><a class="demo-card__email" href="mailto:{agent['email']}">{agent['email']}</a></p>
  <p class="demo-card__address addr">{agent['street']}<br>{agent['city']}, {agent['state']} {agent['postal']}</p>
  <span class="demo-card__title" hidden>{agent['title']}</span>
  <span class="demo-card__license" hidden>{agent['license']}</span>
</article>""")

    pager = []
    for number in range(1, PAGE_COUNT + 1):
        if number == page:
            pager.append(f"<span>{number}</span>")
        else:
            pager.append(f'<a href="/agents?page={number}">{number}</a>')
    if page < PAGE_COUNT:
        pager.append(f'<a class="demo-next" href="/agents?page={page + 1}">Next</a>')

    item_list = [
        {"@type": "ListItem", "position": index + 1,
         "url": f"http://127.0.0.1:{CURRENT_PORT}/agents/{agent['slug']}"}
        for index, agent in enumerate(chunk)
    ]
    jsonld = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "Demo & Co. agents",
        "itemListElement": item_list,
    }
    body = f"""
<div class="hero"><h1>Meet our agents</h1>
<p class="lede">{len(AGENTS)} local experts across Austin and Williamson County. Page {page} of {PAGE_COUNT}.</p></div>
<div class="grid">{''.join(cards)}</div>
<nav class="pager">{''.join(pager)}</nav>
<script type="application/ld+json">{json.dumps(jsonld)}</script>"""
    return _shell(f"Agents — page {page}", body)


def _cards_only(page: int, page_size: int = PAGE_SIZE) -> str:
    start = (page - 1) * page_size
    chunk = AGENTS[start:start + page_size]
    cards = []
    for agent in chunk:
        cards.append(
            f'<article class="demo-card"><h3><a class="demo-card__link" '
            f'href="/agents/{agent["slug"]}">{agent["name"]}</a></h3>'
            f'<p class="demo-card__office">{agent["brokerage"]}</p>'
            f'<p class="demo-card__phone">{agent["phone"]}</p>'
            f'<p class="demo-card__email">{agent["email"]}</p>'
            f'<p class="demo-card__address">{agent["street"]}, {agent["city"]}, '
            f'{agent["state"]} {agent["postal"]}</p></article>'
        )
    return "".join(cards)


def path_listing_html(page: int) -> str:
    """Path-paginated roster with no next link (tests URL-pattern pagination)."""
    body = (
        f'<div class="hero"><h1>Path-paginated agents</h1>'
        f'<p class="lede">Page {page}. Pagination lives at /path/2, /path/3, …</p></div>'
        f'<div class="grid">{_cards_only(page)}</div>'
    )
    return _shell(f"Path pagination — page {page}", body)


def table_listing_html() -> str:
    rows = []
    for agent in AGENTS[:10]:
        rows.append(
            f"<tr><td>{agent['name'].split()[0]}</td><td>{agent['name'].split()[-1]}</td>"
            f"<td>{agent['title']}</td><td>{agent['phone']}</td><td>{agent['email']}</td>"
            f"<td>{agent['brokerage']}</td><td>{agent['city']}</td><td>{agent['state']}</td></tr>"
        )
    body = (
        '<div class="hero"><h1>Table directory</h1><p class="lede">A plain table with '
        'first/last names, title, phone, email, office, city and state.</p></div>'
        '<table class="roster"><thead><tr><th>First Name</th><th>Last Name</th><th>Title</th>'
        '<th>Office Phone</th><th>Email</th><th>Office</th><th>City</th><th>State</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )
    return _shell("Table directory", body)


def embedded_listing_html() -> str:
    records = [
        {"name": agent["name"], "brokerage": agent["brokerage"], "phone": agent["phone"],
         "email": agent["email"], "street_address": agent["street"], "city": agent["city"],
         "state": agent["state"], "postal_code": agent["postal"],
         "profile_url": f"/agents/{agent['slug']}"}
        for agent in AGENTS[:10]
    ]
    payload = {"props": {"pageProps": {"agents": records}}}
    body = (
        '<div id="app-root"></div>'
        '<p class="lede">The records below exist only inside the embedded JSON payload; '
        "the DOM is an empty React root.</p>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
    )
    return _shell("Embedded JSON directory", body)


def irregular_listing_html() -> str:
    tags = ["section", "article", "div", "aside"]
    parts = []
    for index, agent in enumerate(AGENTS[:5]):
        tag = tags[index % len(tags)]
        parts.append(
            f'<{tag} class="irregular-{index}"><a href="/agents/{agent["slug"]}">'
            f'{agent["name"]}</a><p>{agent["phone"]}</p><p>{agent["brokerage"]}</p></{tag}>'
        )
    body = (
        '<div class="hero"><h1>Irregular cards</h1><p class="lede">Each record uses a '
        'different tag and class; only profile-link clustering can find them.</p></div>'
        f'<div id="roster">{"".join(parts)}</div>'
    )
    return _shell("Irregular cards", body)


def sitemap_only_html() -> str:
    body = (
        '<div class="hero"><h1>Agents</h1><p class="lede">'
        + ("This roster is intentionally link-free so sitemap discovery can be tested. " * 8)
        + "</p></div>"
    )
    return _shell("Sitemap-only directory", body)


def profile_html(agent: dict[str, str]) -> str:
    jsonld = {
        "@context": "https://schema.org",
        "@type": "RealEstateAgent",
        "name": agent["name"],
        "telephone": agent["phone"],
        "url": f"http://127.0.0.1:{CURRENT_PORT}/agents/{agent['slug']}",
        "jobTitle": agent["title"],
        "worksFor": {"@type": "Organization", "name": agent["brokerage"]},
        "address": {
            "@type": "PostalAddress",
            "streetAddress": agent["street"],
            "addressLocality": agent["city"],
            "addressRegion": agent["state"],
            "postalCode": agent["postal"],
        },
    }
    email = agent["email"]
    body = f"""
<div class="profile">
  <span class="pill">{agent['title']}</span>
  <h1 class="demo-profile__name">{agent['name']}</h1>
  <p class="demo-profile__office office">{agent['brokerage']}</p>
  <dl class="kv">
    <dt>Phone</dt><dd><a class="demo-profile__phone" href="tel:+1{agent['phone'].replace('(', '').replace(')', '').replace(' ', '').replace('-', '')}">{agent['phone']}</a></dd>
    <dt>Email</dt><dd><button type="button" class="demo-reveal" onclick="document.getElementById('email-slot').innerHTML='<a class=&quot;demo-profile__email&quot; href=&quot;mailto:{email}&quot;>{email}</a>';this.remove();">Show email</button><span id="email-slot"></span></dd>
    <dt>Office</dt><dd><address class="demo-profile__address">{agent['street']}<br>{agent['city']}, {agent['state']} {agent['postal']}</address></dd>
    <dt>Website</dt><dd><a class="demo-profile__website" href="https://demo-brokerage.example/agents/{agent['slug']}">demo-brokerage.example</a></dd>
    <dt>Title</dt><dd class="demo-profile__title">{agent['title']}</dd>
    <dt>Licence</dt><dd class="demo-profile__license">{agent['license']}</dd>
  </dl>
</div>
<script type="application/ld+json">{json.dumps(jsonld)}</script>"""
    return _shell(agent["name"], body)


def sitemap_xml() -> str:
    urls = "".join(
        f"<url><loc>http://127.0.0.1:{CURRENT_PORT}/agents/{agent['slug']}</loc></url>"
        for agent in AGENTS
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'


CURRENT_PORT = 8765


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "B2BProspectsDemo/1.0"

    def _send(self, status: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "all")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/agents")
            self.end_headers()
            return
        if path == "/robots.txt":
            self._send(200, "User-agent: *\nAllow: /\n", "text/plain; charset=utf-8")
            return
        if path == "/sitemap.xml":
            self._send(200, sitemap_xml(), "application/xml; charset=utf-8")
            return
        if path == "/favicon.ico":
            self._send(204, "", "image/x-icon")
            return
        if path == "/agents":
            try:
                page = int((query.get("page") or ["1"])[0])
            except ValueError:
                page = 1
            if not (1 <= page <= PAGE_COUNT):
                self._send(404, _shell("Not found", "<div class='hero'><h1>Page not found</h1></div>"))
                return
            self._send(200, listing_html(page))
            return
        if path.startswith("/path/"):
            try:
                page = int(path.rsplit("/", 1)[1])
            except ValueError:
                page = 0
            if not (1 <= page <= PAGE_COUNT):
                self._send(404, _shell("Not found", "<div class='hero'><h1>Page not found</h1></div>"))
                return
            self._send(200, path_listing_html(page))
            return
        if path == "/table":
            self._send(200, table_listing_html())
            return
        if path == "/embedded":
            self._send(200, embedded_listing_html())
            return
        if path == "/irregular":
            self._send(200, irregular_listing_html())
            return
        if path == "/sitemap-only":
            self._send(200, sitemap_only_html())
            return
        if path.startswith("/agents/"):
            slug = path.split("/", 2)[2]
            agent = BY_SLUG.get(slug)
            if not agent:
                self._send(404, _shell("Not found", "<div class='hero'><h1>Agent not found</h1></div>"))
                return
            self._send(200, profile_html(agent))
            return
        self._send(404, _shell("Not found", "<div class='hero'><h1>Page not found</h1></div>"))

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(port: int = 0, host: str = "127.0.0.1") -> tuple[DemoServer, str]:
    """Start the demo server on a background thread. Returns (server, base_url)."""
    global CURRENT_PORT
    server = DemoServer((host, port), DemoHandler)
    CURRENT_PORT = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, name="b2b-demo-server", daemon=True)
    thread.start()
    return server, f"http://{host}:{CURRENT_PORT}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve a local directory for B2BProspects testing.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    global CURRENT_PORT
    CURRENT_PORT = args.port
    server = DemoServer((args.host, args.port), DemoHandler)
    base = f"http://{args.host}:{args.port}"
    print("=" * 72)
    print("B2BProspects demo directory")
    print("=" * 72)
    print(f"Directory URL : {base}/agents")
    print(f"Agents        : {len(AGENTS)} across {PAGE_COUNT} pages")
    print(f"Profiles      : {base}/agents/jane-smith")
    print()
    print("Layout variants:")
    print(f"  {base}/path/1      path-based pagination, no next link")
    print(f"  {base}/table       table with first/last/title/phone/email headers")
    print(f"  {base}/embedded    JavaScript shell + __NEXT_DATA__ only")
    print(f"  {base}/irregular   cards with different tags/classes (link clustering)")
    print(f"  {base}/sitemap-only no links; profiles discoverable from /sitemap.xml")
    print()
    print("Start the dashboard with local URLs allowed:")
    print('  PowerShell : $env:B2B_ALLOW_LOCAL="true"; streamlit run app.py')
    print("  cmd        : set B2B_ALLOW_LOCAL=true && streamlit run app.py")
    print("  macOS/Linux: B2B_ALLOW_LOCAL=true streamlit run app.py")
    print()
    print("Or run the headless extractor:")
    print(f"  python scripts/extract_cli.py {base}/agents --allow-local --max-pages 3")
    print()
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping demo server.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
