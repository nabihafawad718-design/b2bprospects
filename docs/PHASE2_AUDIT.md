# Phase 2 — Technical audit

Date: 2026-11-07
Scope: turn the Phase 1 MVP into a working browser-based extraction platform.

---

## 1. Constraint that shaped this phase

The build environment has **no outbound network access**, **no Playwright package**,
**no Chromium binary**, and **no Streamlit installation**. Every fetch attempt is
blocked by the egress proxy; `pip install playwright` fails; the browser cache is empty.

Consequences, stated plainly:

- A real Chromium session against a real directory **could not be performed**.
- No live site was contacted, and no result in this document is a live result.
- Everything above the browser boundary was built and tested offline.
- `scripts/verify_live.py` exists to close that gap in one command on a machine with
  network access.

Nothing in this document should be read as evidence that a specific live website works.

## 2. What existed at the start

Phase 1 delivered: a four-workspace Streamlit app (Directory Extraction, Local
Discovery, Adapter Health, Run History), a static extraction pipeline with JSON-LD and
heuristic adapters, normalization, duplicate detection, quality scoring, health
baselines, CSV/Excel export, 46 offline tests and an operator guide.

Known gap, recorded in Phase 1: JavaScript-rendered directories were **detected and
reported, not extracted**. That is the gap this phase closes.

## 3. What changed

### New files

| File | Purpose |
|---|---|
| `core/browser/__init__.py` | Public surface of the engine |
| `core/browser/driver.py` | Playwright session: launch, navigate, wait, block detection, click, scroll, cleanup |
| `core/browser/js.py` | Injected JavaScript: element picker, scroll, overlay dismissal, content signatures |
| `core/browser/structure.py` | Repeated-region detection, position-path cell model, column profiling, field guessing |
| `core/browser/pagination.py` | Next / numbered / load-more / infinite-scroll walkers with stall detection |
| `core/browser/extract.py` | Field mapping, per-page extraction, address splitting, profile enrichment |
| `core/browser/embedded.py` | JSON payload layer (`__NEXT_DATA__`, `__INITIAL_STATE__`, `application/json`) |
| `core/browser/engine.py` | Orchestration: inspect → walk → extract → score → persist |
| `ui/views/browser_flow.py` | Browser mode UI: analyse, mapping with samples, preview, run |
| `ui/views/result_view.py` | Quality + preview + export, shared by both modes |
| `scripts/assess_site.py` | Pre-engagement assessment and permission checklist |
| `scripts/verify_live.py` | One-command live acceptance test |
| `docs/BROWSER_ENGINE.md` | Engine architecture and failure behaviour |
| `docs/PHASE2_AUDIT.md` | This document |
| `tests/fake_browser.py` | Scripted browser session for offline testing |
| `tests/fake_playwright.py` | Fake Playwright API for driver control-flow tests |
| `tests/test_browser_engine.py` | 26 detection, pagination, engine and export tests |
| `tests/test_driver.py` | 12 driver tests |
| `tests/test_tooling.py` | 5 JavaScript and script tests |
| `tests/fixtures/browser/*.html` | 12 generated fixtures (cards, table, load-more, infinite, embedded, JS shell and rendered) |

### Modified files

| File | Change |
|---|---|
| `core/models.py` | Added `ColumnProfile`, `StructureModel`, `BrowserInspection`; added a `website` field to the catalogue and to `Record` |
| `ui/views/extract.py` | Added the Automatic/Browser mode selector; automatic path unchanged; result rendering moved to `result_view.py` |
| `tests/streamlit_stub.py` | Added `st.code` |
| `tests/test_app_smoke.py` | Added three browser-mode render tests |
| `requirements.txt` | Added `playwright>=1.40` |
| `README.md`, `OPERATOR_GUIDE.md` | Rewritten for two extraction modes and browser setup |

### Deliberately untouched

The design system (`ui/theme.py`) is byte-identical to Phase 1. Local Discovery,
Adapter Health, Run History and the export format are unchanged. The static pipeline
and its 46 tests still pass unmodified.

## 4. How the browser engine works

Five layers, tried in order of reliability: embedded JSON → rendered DOM structure →
pagination across pages → per-profile enrichment → site adapter.

Detection finds container elements whose direct children repeat a shape, then
flattens each row into **position-path cells** (`1.2.0` = third child of the third
child of the second child). Columns are profiled with sample values, fill rate, href
rate and a guessed kind. The UI maps `field → column index`, which is why the operator
never sees a selector.

Pagination is detected, not assumed, and every walker confirms the content actually
changed before accepting a new page. Details and failure modes are in
`docs/BROWSER_ENGINE.md`.

## 5. Bugs found and fixed during this phase

Recorded because they show what the tests are for:

1. **Path collapse.** The first implementation indexed cells by same-tag sibling
   position, so a `<div>` and a `<p>` both became index `0`. Fields from different
   parts of a card overwrote each other. Fixed by indexing over all element siblings.
2. **Names classified as URLs.** Name anchors have a 100% href rate, so the URL rule
   won and no name was mapped. Fixed by testing for name-shaped text before link shape,
   with a brokerage check ahead of it so "Harborline Properties" is not read as a person.
3. **Catch-all wrapper column.** An `<a>` wrapping a whole card produced one column
   containing every field, which out-scored the real columns. Fixed by reading only an
   element's own text nodes when it has element children.
4. **City receiving the full address.** Auto-mapping assigned `city` to the address
   column, so city held "3300 Bee Caves Rd, Austin, TX 78746". Fixed by letting the
   address splitter fill city/state/postal and guarding against direct assignment.
5. **Duplicate fixtures masking a bug.** The first generated roster reused agent
   identities across pages, so a pagination test appeared to lose a page. The engine
   was correct; the fixture was wrong. Fixtures now assert 36 unique slugs at
   generation time.
6. **Infinite-scroll never detected.** The heuristic required 12 rows on a page; real
   first pages often show fewer. Lowered to 5, accepting one wasted scroll on a static
   page in exchange for supporting scroll-loaded listings.

## 6. Test results

```
python -m unittest discover -s tests -t .
Ran 93 tests in ~3s
OK
```

| Suite | Tests | What it actually proves |
|---|---|---|
| `test_extraction` (Phase 1) | 15 | Static pipeline, adapters, robots, health regression |
| `test_quality_export` (Phase 1) | 19 | Normalization, dedupe, quality, CSV/Excel |
| `test_app_smoke` | 9 | Streamlit render path for all views, including browser mode |
| `test_browser_engine` | 26 | Detection, mapping, four pagination styles, engine, export |
| `test_driver` | 12 | Playwright control flow: status, challenges, timeouts, clicks, cleanup |
| `test_tooling` | 5 | JS syntax via Node, script imports, missing-browser behaviour |

What these tests do **not** prove: that Chromium executes a real site's JavaScript, that
detection generalises beyond the two layouts used, or that any specific site will not
block the browser.

## 7. The required real-directory test

**Not performed.** The environment cannot reach any host, and Playwright cannot be
installed. Reporting a live result here would be fabrication.

A target was also deliberately not hard-coded. Choosing a directory for commercial
collection is a permissions decision, not a technical one, and it cannot be made
honestly without reading that site's current terms — which requires network access.

To complete this requirement on your machine:

```bash
pip install -r requirements.txt
python -m playwright install chromium
python scripts/assess_site.py https://your-permitted-directory.example/agents
python scripts/verify_live.py https://your-permitted-directory.example/agents --max-pages 2 --max-records 10
```

`assess_site.py` prints robots status, structured data, embedded payloads and the
permission checklist. `verify_live.py` performs the end-to-end extraction and prints
PASS/FAIL per check, then writes CSV and Excel to `data/exports/`.

## 8. Known gaps and recommended next steps

1. **Live verification** — the only thing standing between this and a demonstrated
   working product. One command; see §7.
2. **Click-to-select transport.** The picker JavaScript is complete and syntax-tested.
   Wiring it into Streamlit needs a bidirectional custom component (Node build +
   browser test). Until then, field selection uses column samples.
3. **Detection breadth.** Two layouts are covered. Every new brokerage is a chance to
   find a third. Keep a fixture for each site you support and add it to the test suite.
4. **Browser-side throttling** is a fixed delay between profile pages; a per-host
   token bucket shared with the static fetcher would be cleaner.
5. **No screenshot capture** for failed runs. Useful when a customer disputes a result;
   would also make adapter debugging faster.
