# v3 — implementation and testing record

Date: 2026-11-08
Baseline: `b2bprospects-v2.zip` (Phase 2)
Result: 136 offline/local tests pass; no external live site was contacted.

---

## 1. What was implemented

### Extraction engine

- **Row-level fallbacks** for mapped fields: `tel:`/`mailto:` anchors, text patterns,
  `data-*` attributes, icon links, street-address regex, website and profile-link
  scoring. Fields the operator chose not to collect stay uncollected.
- **Unique-class card layouts**: cards whose class differs per record (`card-0`,
  `card-1`, CSS-module hashes) are grouped by tag and addressed with a scoped
  `container > tag` selector. Previously only one row matched.
- **Job-title disambiguation**: "Senior Agent" and "Team Lead" no longer win the name
  column over the actual person name.
- **Embedded JSON breadth**: `__NEXT_DATA__`, `__NUXT_DATA__`, `__APOLLO_STATE__`,
  `application/json`, `application/ld+json`, `window.__X__` assignments, Next.js
  App Router `self.__next_f` flight chunks, schema.org `@graph` and `ItemList`.
- **Embedded false-positive guard**: `{name, url}` menus, products and blog lists are
  rejected; a record needs contact or a name plus address/company/location.
- **Embedded/DOM merging**: richer embedded records are preferred; DOM fields fill
  gaps without creating blank records.
- **Listing-page records**: schema.org listing records and config `record_fields` can
  be used directly. Contact coverage ≥60% avoids the per-profile fetch.
- **Pagination breadth**: next button, numbered links, numbered buttons, query-param
  pages, load-more and infinite scroll. Query params are also detected from the
  current URL. `next` failures fall back to text clicks.
- **Driver additions**: `click_text`, `auto_scroll`, `reveal_contacts`, `count_rows`,
  `wait_for_rows`, screenshot-on-error, media blocking, dialog auto-dismissal,
  Accept-Language header, 404 handling, and a narrower anti-bot false-positive test.
- **Profile enrichment** can click ordinary "Show phone / Show email / Contact
  details" controls before parsing, without bypassing access controls.

### Data model and processing

- **Arbitrary extra columns** end to end: mapping → record → export, with readable
  headers.
- **Country** field and improved international address parsing (US, CA, UK, AU).
- **Phone extensions**, obfuscated emails, ALL-CAPS names, and collapsed `<br>`
  addresses (`901 Round Rock Ave Round Rock, TX`).
- **Cleaning module** (`core/cleaning.py`): filters, seven duplicate strategies,
  sorting, coverage, issue rows and filter summaries.
- **Export**: filtered records, selected columns, optional Issues sheet, JSON export,
  and a Run Summary that records filters and cleaning decisions.

### Site adapters

- **JSON adapter loader** (`core/adapters/config_adapter.py`): selectors, attributes,
  fallback selectors, profile regex, listing record fields, pagination.
- **Three site files** in `core/adapters/sites/`: a template, a demo adapter, and a
  README with syntax.
- **Generic HTML adapter listing records**: uses the same detector as the browser on
  server-rendered cards, avoiding profile fetches when the roster is complete.
- **Generic JSON-LD adapter listing records**: complete Person nodes on a listing are
  used directly.

### Visual selection

- **`ui/visual_picker/index.html`**: direct Streamlit component protocol
  implementation. Hover highlight, repeated-row detection, sibling outline, click
  position path, reset/pause controls, overlay hiding, snapshot rendering in a
  scriptless iframe.
- **`ui/visual_picker.py`**: sanitizer (removes scripts/frames/handlers/CSP/refresh),
  `<base>` handling, size cap, component wrapper, column mapping and description.
- **Dashboard integration**: visual picker card, canonical fields + custom fields,
  automatic mapping update, extra-column registration, and fallback to dropdowns.
- **Embedded-only screen**: when a page has records only in JSON, the dashboard offers
  an extraction action instead of silently returning.

### UI and operations

- **Clean/review/export UI**: search, filters, duplicate strategy, sort, issue review,
  coverage, export column selection, CSV/Excel/JSON.
- **Adapter Health** lists supported/config adapters and configuration errors.
- **Robots policy** default changed to `warn`: report, do not automatically reject.
  `enforce` and `ignore` remain available.
- **CLI** (`scripts/extract_cli.py`): static, browser, auto; inspect; fields; formats;
  local-demo support.
- **Demo server** (`scripts/serve_demo.py`): 36 agents, three pages, profiles,
  schema.org, JSON adapter, hidden-email reveal.
- **Docs**: README, operator guide, browser engine, this record, site-adapter README.

### Deliberately preserved

- `ui/theme.py` design system is unchanged; new screens use the existing card classes.
- Local Discovery, Run History and the original OSM pipeline remain.
- The $49 paywall component remains, still off by default.
- Existing adapter contract, quality/health model and export provenance remain.

---

## 2. Bugs found and fixed during this phase

1. **Unique-class cards matched one row.** The generated class selector addressed
   `div.card-0` only. Fixed with tag-scoped fallback selectors.
2. **Job titles won the name column.** "Senior Agent" was classified as a person.
   Added title-word disambiguation before the name check.
3. **Embedded records collapsed into one.** Records without a profile URL inherited
   the listing URL as `profile_url`, so dedupe merged them. `profile_url` no longer
   falls back to `source_url`; the listing URL stays in `source_url`.
4. **Blank DOM records polluted embedded results.** Unmatched DOM rows with only a
   link were appended. Merge now skips rows with no identity/contact fields.
5. **Profile cap stopped listing pagination.** With seeds, `max_profiles=10` stopped
   after page 1. The cap now applies to profile fetches, not listing pages.
6. **`<br>`-collapsed addresses split wrongly.** "901 Round Rock Ave Round Rock, TX"
   became street "901", city "Round Rock Ave Round Rock". Added street-suffix
   splitting with a St. Louis guard.
7. **Query-param pagination was tested against a next button.** Test corrected to use
   a page with no controls; the walker now respects `max_pages`.
8. **Menu arrays looked like records.** Tightened embedded identity rules and added a
   regression test.
9. **`allow_local` allowed any private IP.** Restricted the demo exception to
   loopback only.

---

## 3. Test coverage

Run:

```bash
python -m unittest discover -s tests -t .
```

Result at delivery: **139 tests, OK** (about 10–20 seconds).

New suites:

| Suite | What it proves |
|---|---|
| `test_v3_features.py` (33) | normalization, extra fields, flight/JSON-LD payloads, cleaning, query pagination, visual mapping/sanitizer, config adapters, issues export, robots policy, driver additions, real-HTTP demo extraction, CLI |
| `test_detection_breadth.py` (9) | unique classes, icon contacts, data attributes, embedded-vs-DOM preference, menu rejection, browser extras |
| existing suites (97) | the Phase 1/2 static pipeline, browser engine, driver, quality, export, Streamlit smoke |

The real-HTTP tests start `scripts/serve_demo.py` on loopback and exercise:

- static extraction: 36 records, three pages, zero profile fetches, correct address and
  extras;
- browser engine: inspect, next-button navigation, 36 records across three pages;
- CLI: a 36-row CSV with the licence column;
- robots: warn reports, enforce blocks.

They do not require external network or Playwright.

---

## 4. Implemented features that still need live testing on your machine

These are built and locally tested but cannot be fully certified in this build
environment:

1. **Real Chromium execution** of a third-party site's JavaScript. Run
   `scripts/verify_live.py` against a permitted directory.
2. **The visual picker inside a real Streamlit server.** The component protocol,
   sanitizer and mapping are tested; the rendered iframe experience is not. Confirm
   hover, click, row highlighting and mapping update.
3. **Site-specific anti-bot behaviour.** The engine stops on 401/403/429 and
   challenge pages; verify a real site's response.
4. **Pagination against live sites** for query-param, numbered-button, load-more and
   infinite-scroll variants. Fixtures cover them, but real markup varies.
5. **Profile "show contact" interactions** on a site that hides phone/email behind an
   ordinary button.
6. **Config adapters for your real target sites.** The demo proves the mechanism; add
   one JSON file per site and verify selectors.
7. **Excel/CSV opening in your customer's software** (encoding, column widths, Issues
   sheet) — locally generated and opened by openpyxl, but not by Excel itself here.
8. **The OSM discovery workspace** against current OSM services (unchanged code).

Recommended acceptance order:

```bash
python scripts/serve_demo.py --port 8765
# second terminal
B2B_ALLOW_LOCAL=true streamlit run app.py
```

Then:

```bash
python scripts/verify_live.py http://127.0.0.1:8765/agents --allow-local
python scripts/verify_live.py https://your-permitted-directory.example/agents --max-pages 2 --max-records 10
```

---

## 5. Where to add the next site

- No code: `core/adapters/sites/<site>.json`.
- Python, unusual structure: copy `core/adapters/custom_site.py`, set domains and
  selectors, register in `core/adapters/registry.py`.
- Browser-only, no stable selectors: use the visual picker and save the mapping notes;
  if the site recurs, add a JSON adapter for its row/profile URLs.

Keep a fixture for every supported site and add it to `tests/`. A new layout is a
chance to find the next detector weakness; the breadth suite is the place to encode
it.
