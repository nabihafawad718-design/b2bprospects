# Implemented features and testing status — v4

This is the delivery checklist. The detailed engineering record is
`docs/V4_CHANGES.md`; the live acceptance procedure is `docs/LIVE_ACCEPTANCE.md`;
the operational guide is `OPERATOR_GUIDE.md`.

---

## 1. Implemented and locally tested

### Layered extraction and diagnosis

- Every browser run tries supported layers in reliability order: configured site
  adapter, schema.org listing records, embedded JSON, rendered DOM cards,
  profile-link clusters, profile pages, sitemap-listed profiles.
- A layer that produces no usable records is recorded and the next layer is tried.
  The chosen layer, its record count and every failed/empty layer are shown in the
  UI and written into the Excel Run Summary.
- Static mode also layers adapter records, generic cards, schema.org, embedded JSON
  and sitemap discovery, merging records by name and contact identity.
- Failure codes distinguish access refusal, 404, server error, JavaScript-only page,
  single profile, not a listing, empty mapping, no records and stalled pagination.
- HTTP 401/403/429 and anti-bot challenges stop the run; they are never retried or
  bypassed.

### Website understanding and fields

- Repeated-region detection for cards, grids, tables, nested wrappers, cards with
  unique per-record classes, and irregular rows recovered by profile-link clustering.
- Table headings map first/last name, full name, title, phone, mobile, email, office,
  address, city, state/province, postal code and country.
- Separate first/last columns are joined into the full name; the last-name column is
  kept as an extra. Job titles are exported as an extra column, not mistaken for a
  person's name.
- `data-*` attributes, microdata, `tel:`/`mailto:` anchors, icon-only contacts and
  combined addresses are read. If a selected field has no mapped column, row-level
  fallbacks still try to fill it in static mode.
- Embedded payload discovery: `__NEXT_DATA__`, `__NUXT_DATA__`, `__APOLLO_STATE__`,
  `application/json`, `application/ld+json`, `window.__X__`, Next.js flight chunks,
  schema.org `@graph` and `ItemList`.
- Relative profile URLs in embedded JSON resolve against the listing URL.
- Listing Person nodes with only a name and URL are kept; profile pages can complete
  them rather than the roster being rejected.
- False-positive guards for menus, products and blog JSON arrays.

### Extraction

- Static and browser extraction share one quality/export pipeline.
- Complete listing cards are used directly when they carry both phone and email and
  enough of the requested fields; otherwise profile pages remain the better source.
  A site adapter with complete cards is not limited by the profile cap.
- Profile pages are parsed with JSON-LD first (even through the generic HTML
  adapter), then microdata/heuristics; ordinary "show phone/email" controls are
  clicked when present.
- Ordinary per-card "show phone/email" controls on the listing are revealed before
  analysis.
- Extra columns: titles, licence numbers, MLS IDs, languages or any detected value.
- Screenshot on error when Playwright is available.
- Clear refusal handling for 401/403/429 and challenge pages.

### Pagination

- Next links/buttons including `rel=next`, text, aria-label and attribute-only
  controls (`data-testid`, class, id, title).
- Numbered links and buttons; path pages (`/agents/page/2`, `/p/2`); numeric URL tails
  (`/agents/2`) when a row structure was detected; query parameters with offset step
  inference.
- Load-more and infinite scroll, with row-count/signature checks.
- Visited-URL tracking, duplicate-page and duplicate-record prevention, two-empty-page
  tolerance, and an explicit stop reason.

### Visual selection

- Rendered page snapshot shown inside the dashboard.
- Hover highlight, repeated-row detection, sibling outline, table-row awareness.
- Click returns a position path plus a CSS selector; Python resolves the click against
  the snapshot DOM and maps it to the detected column. Snapshot sanitization removes
  the same non-content tags as the detector, so paths remain aligned.
- Canonical fields, custom fields, and automatically detected extra columns.
- Fallback to column dropdowns when the component or snapshot is unavailable.

### Data processing and delivery

- Normalization: phones (extensions), emails, names (credentials, ALL CAPS,
  prefixes), US/CA/UK/AU addresses, URLs.
- Extraction-time phone/email normalization for mapped and data-attribute columns.
- Deduplication with seven strategies plus the smart default; shared office phones do
  not merge different people.
- Search, filters, sorting, issue review, per-record quality flags, field coverage.
- CSV, Excel (Records + Run Summary + optional Issues), JSON.
- Source URL and collection timestamp per record; extraction methods and diagnosis in
  the Run Summary; formula-injection protection in spreadsheets.
- Run history and adapter-health baselines.
- Paywall preserved and off by default; theme and OSM discovery workspace preserved.

### Operations

- Headless CLI: static/browser/auto, inspect, fields, formats, local demo.
- Live verification script for browser, static and inspect-only modes, with optional
  JSON evidence.
- Demo server variants: query pagination, path pagination, table with headers,
  embedded-JSON-only, irregular cards, sitemap-only roster, profiles and hidden email.
- Config-driven JSON site adapters; Python adapter path remains.
- SSRF guard, loopback-only demo exception, redirect-final-hop check.
- Robots policy: `warn` (default), `enforce`, `ignore`.

---

## 2. Proven by the test suite

`python -m unittest discover -s tests -t .` → **163 tests, OK** (~12–20s).

Highlights:

- **Real HTTP** against a loopback server, not mocks:
  - static extraction from embedded JSON with no DOM records;
  - path pagination (`/paged/page/1` → `/paged/page/2`);
  - sitemap-only roster discovered through `/sitemap.xml`;
  - HTTP 403 refusal raises and stops;
  - existing demo: 36 records over 3 pages, 0 profile fetches, extras, browser
    navigation over real HTTP, CLI CSV.
- Browser layered fallback: an empty DOM mapping falls through to embedded records;
  method attempts and failure codes are asserted.
- Table headers, first/last joining, title extras, data attributes, link clusters,
  job-title disambiguation.
- Path pagination, numeric tails, attribute next controls, disabled controls,
  path walking and empty-page stops.
- Snapshot path parity and selector-based visual mapping.
- Embedded relative URLs and JSON-LD name+URL listings.
- Formula-injection escaping and diagnosis rows in Excel.
- Cleaning, filters, duplicate strategies, issue sheet, config adapters.
- Node syntax checks for every injected JavaScript snippet.

---

## 3. Built but requiring live acceptance on your machine

The build environment has **no outbound network** (external hosts return HTTP 403
through the proxy), **no Playwright/Chromium** and **no Streamlit**. Therefore these
are implemented and locally exercised, but not certified live:

1. **Real Chromium rendering** of a third-party site's JavaScript.
2. **Visual picker inside a live Streamlit server** — hover, click, highlight and
   mapping update with the real component iframe.
3. **Live pagination variants** on third-party sites.
4. **Live profile "show contact" interactions.**
5. **Real anti-bot responses** (the tool is designed to stop, not bypass).
6. **Config adapters for your real target sites** — add JSON, verify selectors.
7. **Excel opening in Microsoft Excel** (generated and read back by openpyxl here).
8. **OSM Local Discovery against current public services** (code unchanged).

### Acceptance sequence

```bash
python scripts/serve_demo.py --port 8765
# second terminal
B2B_ALLOW_LOCAL=true streamlit run app.py
```

Enter `http://127.0.0.1:8765/agents`, run Browser mode, use the visual picker,
extract 3 pages, clean, export. Then try `/path/1`, `/table`, `/embedded`,
`/irregular` and `/sitemap-only`.

Scripted:

```bash
python scripts/verify_live.py http://127.0.0.1:8765/agents --allow-local
python scripts/verify_live.py http://127.0.0.1:8765/path/1 --allow-local --engine static
python scripts/verify_live.py https://your-permitted-directory.example/agents --max-pages 2 --max-records 10 --json-out evidence.json
```

See `docs/LIVE_ACCEPTANCE.md` for the per-target evidence checklist.

---

## 4. Deliberate limits

- No CAPTCHA solving, stealth/anti-bot evasion, proxy rotation, login handling, or
  paywall bypass.
- No claim that any specific live site works until it is tested and evidence recorded.
- robots.txt is reported, not treated as automatic permission or automatic rejection;
  `enforce` is available.
- Structured data and explicit anchors are preferred over regex; regex contacts can
  still produce false positives and are flagged for review.
- Address parsing is strongest for US formats.
- Sitemap discovery depends on readable sitemap URLs and profile-shaped paths.
- One operator, local machine; do not expose the app publicly as-is.
