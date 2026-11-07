# v4 — implementation and testing record

Date: 2026-11-08
Baseline: `b2bprospects-v3.zip` (the supplied Phase 2 project)
Result: 160 offline/local tests pass. No third-party site was contacted.

---

## 1. Environment reality (read this first)

The build sandbox has:

- **no outbound network** — `curl` and the web-fetch tool both fail; the proxy
  returns HTTP 403 for external hosts and the egress allowlist is empty;
- **no Playwright or Chromium** (`import playwright` fails);
- **no Streamlit** (`import streamlit` fails);
- Node 22 (used only to syntax-check injected JavaScript).

Therefore **no real estate brokerage or third-party directory was tested live**. I did
not fabricate live results. The engine was exercised against real HTTP on loopback
with several synthetic site architectures, which proves the transport, navigation,
detection, extraction, diagnosis and export code paths. It does not prove how any
particular third-party site renders in a real browser.

`docs/LIVE_ACCEPTANCE.md` contains the exact commands to close that gap on your
machine.

---

## 2. What changed from v3

### A. Layered extraction with real fallback

The v3 browser engine chose either embedded JSON or DOM before walking, and returned
an empty result if that choice produced nothing. v4 builds an ordered layer list and
runs each until one produces identity-bearing records:

1. configured site adapter (`core/adapters/sites/*.json` or Python),
2. schema.org listing records,
3. embedded JSON (`__NEXT_DATA__`, flight chunks, `__INITIAL_STATE__`, ...),
4. rendered DOM cards / link clusters,
5. profile pages,
6. sitemap-listed profiles.

Between attempts the first listing URL is reopened. A failed layer is recorded, not
silently swallowed. Explicit refusals still raise immediately.

The static engine gained the same idea: adapter cards + generic cards + schema.org +
embedded JSON + sitemap discovery are merged by name/email/phone identity before the
profile fallback.

### B. Diagnosis instead of generic errors

`core/diagnostics.py` classifies failures into stable codes:

`blocked`, `not_found`, `server_error`, `javascript_only`, `not_a_listing`,
`single_profile`, `mapping_empty`, `no_records`, `pagination_stalled`, `partial`, `ok`.

`ExtractionDiagnosis` / `MethodAttempt` are attached to every `RunResult` and copied
into `summary()` and the Excel Run Summary. The Streamlit result screen shows a table
of every method attempted, the chosen method, the failure reason and next steps.

### C. Field inference

- Table `<th>` headings now drive kinds: first name, last name, full name, title,
  phone/mobile, email, office/brokerage, address, city, state/province, ZIP/postal,
  country, website, profile URL.
- Separate first/last columns are joined into `agent_name`; `last_name` is kept as an
  extra.
- Job titles get their own `title` kind and are auto-mapped as an extra column instead
  of competing for the name.
- Row-level `data-name`, `data-phone`, `data-email`, `data-address`, `data-city`,
  `data-state`, `data-zip`, `data-brokerage` etc. become addressable cells.
- `data-*` and microdata are read on static profile pages too.
- Mapped and data-attribute phone/email values are normalized during extraction.
- Country/state/postal kinds were missing from the kind→field map; fixed.

### D. Link-cluster detection

When no clean sibling card group exists (different tags/classes per record), profile
links are grouped by URL shape and the nearest record-sized ancestor of each link is
used as a row. The row selector falls back to `container > *` or `:is(tag, ...)` so
all rows are addressable. This detector is only used when the normal card detector
found no name-bearing roster, so it cannot outrank a clean layout.

### E. Pagination

- Path pagination: `/agents/page/2`, `/pages/2`, `/p/2`, `/pg/2`.
- Numeric tails: `/agents/2`, but only when a row structure was detected.
- Attribute-only next controls: `data-testid`, `data-test`, class, id, title,
  aria-label; disabled controls are skipped.
- Load-more text accepts “Load 20 more”, “Show more results”, etc.
- Walkers track visited URLs, canonical page identities, two-empty-page tolerance and
  row-count growth for infinite scroll.
- Change detection now also considers URL and content-length change, not only the row
  signature.
- Record keys ignore per-page provenance, so the same record on page 2 is deduped.

### F. Static mode

- Uses embedded JSON records even when the DOM is an empty React root; a JS-only page
  with a payload no longer requires the browser.
- Uses schema.org Person/ItemList records, including name+URL listings, and resolves
  relative URLs.
- Merges adapter and generic cards by email, phone (only when a name is missing or
  agrees) and name, so a stale adapter cannot leave blank-name rows or discard the
  generic detector's name.
- `GenericHtmlAdapter.parse_profile` now tries JSON-LD first, then heuristics.
- Sitemap fallback discovers profile-shaped URLs from `sitemap.xml`/sitemap indexes.
- “Complete cards” means both phone and email plus enough requested fields; a phone-only
  card no longer suppresses profile pages that hold the email/address.
- A site adapter with complete cards is not limited by the profile cap; generic and
  embedded seeds are.

### G. Visual picker correctness

The v3 sanitizer deleted `<script>`/`<style>` elements from the snapshot while the
detector and extractor deleted only some of them. A script inside a card shifted
position paths, so a click could map to the wrong column. v4:

- defines one `NON_CONTENT_TAGS` list used by analysis, extraction and sanitization;
- keeps head styles for appearance but removes body non-content tags consistently;
- maps a click by resolving its CSS selector inside the sanitized snapshot and
  computing the path relative to the real structure row, then falls back to the old
  path/text heuristics;
- surfaces automatically detected extras (title, second phone/email, last name) in the
  mapping UI.

### H. Data delivery

- Spreadsheet formula injection is neutralised (`=`, `@`, tab, CR, and non-numeric
  leading `+`/`-`) without prefixing real phone numbers.
- Method attempts, selected method, failure code and next steps are written to the
  Excel Run Summary.
- Inspection now reports sample records, detected layers and suggested fields for both
  modes.
- `verify_live.py` supports browser/static/auto/inspect-only, JSON evidence and prints
  the method attempts. `assess_site.py` reports embedded records, sitemap and layers.
- Demo server adds `/path/1`, `/table`, `/embedded`, `/irregular` and `/sitemap-only`.

---

## 3. Bugs found and fixed during this phase

1. **Sanitizer/detector path mismatch.** Scripts and styles inside a card shifted
   visual-picker paths relative to extraction. Unified removal; regression test asserts
   identical `(path, kind)` pairs before and after sanitization.
2. **Selected-but-unmapped fields were never attempted.** Static extraction now passes
   `selected_fields` into `extract_page`, so phone/email/address row fallbacks run even
   when no column was detected.
3. **Stale config adapter masked generic detection.** A config adapter whose row
   selector matched but whose name selector did not produced blank-name records and
   blocked the generic detector. Both are now merged by identity; blank names are
   filled.
4. **Same phone across pages merged different people.** The first identity-merge rule
   used phone alone. It now requires email match, or phone match with a missing/equal
   name.
5. **Phone-only listing cards suppressed richer profiles.** “Contact share” alone was
   not a completeness test. Both phone and email, plus field coverage, are required
   before cards outrank profiles.
6. **JSON-LD name+URL rosters were discarded.** `_person_fields` did not carry
   `profile_url`, so the completeness guard rejected them. Profile URL is now a field
   and the records can be completed from profiles.
7. **Relative embedded URLs resolved wrongly.** `agents/jane` became a host-level URL;
   now resolved with `urljoin` against the listing.
8. **Attribute-only next controls were invisible.** `_css_for` now includes
   `data-testid`/`data-test`/`aria-label`/`title`/`name`.
9. **Path/numeric pagination was not detected by static inspection.** Both browser and
   static detectors now recognise it; the UI reports `path_pages`.
10. **Record keys included page provenance.** The same record on another page could be
    counted as new; keys now ignore `_source_url`/`_payload`.
11. **Missing state/postal/country mapping.** The kind→field map lacked them; added.
12. **Page-2 listings were not merged generically.** Fixed by collecting both adapter
    and generic seeds on every listing page.

---

## 4. Test coverage

Run:

```bash
python -m unittest discover -s tests -t .
```

Result at delivery: **163 tests, OK** (about 12–20 seconds).

New `tests/test_v4_features.py` (24 tests) covers:

- table headings, first/last joining, title and location fields;
- data-attribute-only cards;
- link-cluster rows;
- job-title disambiguation;
- embedded relative URLs and JSON-LD name+URL listings;
- path pagination, numeric tails, attribute/disabled next controls, path walking;
- sanitizer path parity and selector-based visual mapping;
- layered browser fallback (empty DOM mapping → embedded records);
- JS-shell failure diagnosis;
- title extras in CSV;
- formula-injection escaping;
- diagnosis rows in Excel;
- real-HTTP static extraction from embedded JSON, path pagination, sitemap fallback and
  HTTP 403.

The existing 139 tests still pass unchanged.

---

## 5. Live testing status and what remains unverified

No live third-party test was possible. The honest status is:

- **Verified here:** real HTTP transport on loopback; detection, mapping, layered
  fallback, pagination walking, diagnosis, normalization, dedupe, export; injected JS
  syntax; Streamlit render paths through a stub.
- **Not verified here:** Chromium executing a live site's JS; the real Streamlit
  component iframe; third-party anti-bot/403 behaviour; live sitemap shape; live
  “show contact” interactions; Excel opening in Microsoft Excel; OSM services.

Use `docs/LIVE_ACCEPTANCE.md` and record the JSON evidence file for each target. Do not
describe a target as supported until that evidence exists.

---

## 6. Where to add the next site

- No code: `core/adapters/sites/<site>.json`.
- Python, unusual structure: copy `core/adapters/custom_site.py`, set domains and
  selectors, register in `core/adapters/registry.py`.
- Browser-only, no stable selectors: analyse, map visually, and if it recurs add a JSON
  adapter for the row/profile URL pattern.
- Keep a fixture for every supported site and add it to `tests/`. A new layout is a
  chance to encode the next detector weakness.
