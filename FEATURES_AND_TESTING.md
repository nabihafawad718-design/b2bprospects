# Implemented features and testing status

This is the delivery checklist for v3. The detailed engineering record is
`docs/V3_CHANGES.md`; the operational guide is `OPERATOR_GUIDE.md`.

---

## 1. Implemented and locally tested

### Website understanding

- One-click browser inspection: title, final URL, robots status, repeated rows,
  candidate structures, pagination style, embedded payloads, available fields.
- Repeated-region detection for cards, grids, tables, nested wrappers, and cards
  with unique per-record classes.
- Column profiling: sample values, fill rate, href rate, and kind guessing for
  names, phones, emails, addresses, cities, brokerages, websites and profile URLs.
- Job-title disambiguation and person/brokerage separation.
- Static inspection fallback that understands unique-class rosters.
- Embedded payload discovery: `__NEXT_DATA__`, `__NUXT_DATA__`, `__APOLLO_STATE__`,
  `application/json`, `application/ld+json`, `window.__X__`, Next.js flight chunks.
- schema.org `@graph`, `ItemList` and full Person nodes.
- False-positive guards for menu/product/blog JSON arrays.

### Extraction

- Static and browser extraction modes sharing one quality/export pipeline.
- Listing-page records used directly when they carry contact/company data, avoiding
  hundreds of profile requests; profile pages remain fallback/enrichment.
- Profile-page extraction and optional enrichment, including configured site fields.
- Ordinary "show phone / show email / contact details" interactions.
- Row-level fallbacks from `tel:`/`mailto:` anchors, text patterns, `data-*`
  attributes, icon links, and headings.
- Profile-link scoring instead of taking the first anchor.
- Extra columns: titles, licence numbers, MLS IDs, languages, or any detected value.
- Pagination: next button, numbered links, numbered buttons, query-param pages,
  load-more, infinite scroll, with stall detection and page/record caps.
- Embedded-vs-DOM preference and gap merging.
- Config-driven site adapters from JSON, no Python required; Python adapter path
  remains.
- Clear failure reporting for 401/403/429, challenges, 404, timeouts, no structure,
  no records, stalled pagination.
- Browser screenshot on error (when Playwright is present).

### Visual selection

- Captured rendered page shown inside the dashboard.
- Hover highlight, repeated-row detection, sibling outline, table-row awareness.
- Click returns a position path; Python maps it to a detected column.
- Canonical fields and custom (extra) fields.
- Snapshot sanitizer removes scripts/frames/event handlers/CSP/refresh.
- Fallback to column dropdowns when the component or snapshot is unavailable.

### Data processing and delivery

- Normalization: phones (including extensions), emails (including `[at]` forms),
  names (credentials, ALL CAPS, prefixes), US/CA/UK/AU addresses, URLs.
- Deduplication with seven strategies plus the smart default.
- Search, filters (name/phone/email/contact/city/state/flag/missing field), sorting.
- Issue review and per-record quality flags.
- Field coverage per export.
- Import/export of arbitrary extra columns.
- CSV, Excel (Records + Run Summary + optional Issues), JSON.
- Source URL per record and collection timestamp.
- Run history, health baselines, adapter-health dashboard, supported-adapter list.

### Operations

- Preserved original B2BProspects theme and OSM Local Discovery workspace.
- Paywall still present and off by default.
- Headless CLI: static/browser/auto, inspect, fields, formats, local demo.
- Local demo directory with 36 agents, profiles, schema.org, JSON adapter, extra
  columns and a hidden-email reveal.
- Robots policy: `warn` (default), `enforce`, `ignore`.
- SSRF guard, loopback-only demo exception, redirect-final-hop check.
- Documentation: README, operator guide, browser engine, v3 record, adapter README.

---

## 2. Proven by the test suite

`python -m unittest discover -s tests -t .` → **139 tests, OK** (~10–20s).

Highlights:

- **Real HTTP**, not mocks, against `scripts/serve_demo.py` on loopback:
  - static extraction: 36 records, 3 pages, 0 profile fetches, correct address and
    extras;
  - browser engine: inspect, next-button navigation, 36 records across 3 pages;
  - CLI: 36-row CSV with licence column;
  - robots warn vs enforce.
- Detection across card, table, unique-class, icon-contact, data-attribute and
  nested layouts.
- Embedded JSON, Next flight, `@graph`, `ItemList`, and menu/blog rejection.
- Extra columns through mapping → record → CSV/Excel.
- Cleaning, filters, duplicate strategies, issue sheet.
- Visual picker sanitizer, path-to-column mapping, and JavaScript syntax (Node).
- Streamlit render paths for every view, including visual selection update,
  embedded-only extraction, and no-records messaging.
- Playwright control flow against a fake Playwright API.

---

## 3. Built but requiring live acceptance on your machine

These are implemented and locally exercised, but cannot be certified from this
build environment (no external network, no Playwright/Chromium, no Streamlit):

1. **Real Chromium rendering** of a third-party site's JavaScript.
2. **Visual picker inside a live Streamlit server** — hover, click, highlight,
   mapping update with the real component iframe.
3. **Live pagination variants** on real sites (query-param, numbered-button,
   load-more, infinite scroll).
4. **Live profile "show contact" interactions**.
5. **Real anti-bot responses** (the tool is designed to stop, not bypass).
6. **Config adapters for your real target sites** — add JSON, verify selectors.
7. **Excel opening in Microsoft Excel** (the file is generated and read back by
   openpyxl here).
8. **OSM Local Discovery against current public services** (code unchanged).

### Acceptance sequence

```bash
python scripts/serve_demo.py --port 8765
# second terminal
B2B_ALLOW_LOCAL=true streamlit run app.py
```

Enter `http://127.0.0.1:8765/agents`, run Browser mode, use the visual picker,
extract 3 pages, clean, export.

Then scripted:

```bash
python scripts/verify_live.py http://127.0.0.1:8765/agents --allow-local
python scripts/verify_live.py https://your-permitted-directory.example/agents --max-pages 2 --max-records 10
```

---

## 4. Deliberate limits

- No CAPTCHA solving, stealth/anti-bot evasion, proxy rotation, login handling, or
  paywall bypass.
- No claim that any specific live site works until it is tested.
- robots.txt is reported, not treated as automatic permission or automatic
  rejection; `enforce` is available.
- Structured data and explicit anchors are preferred over regex; regex contact
  extraction can still produce false positives and is flagged for review.
- Address parsing is strongest for US formats.
- One operator, local machine; do not expose the app publicly as-is.
