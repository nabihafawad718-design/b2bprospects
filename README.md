# B2BProspects Data Extraction

An internal operator tool for delivering paid business-directory extraction projects.
It opens a directory in a real browser, finds the repeating records, lets you confirm
which column is the name or the phone, extracts across pages, and exports a clean CSV
or Excel file with a full source trail.

The interface is the existing B2BProspects design. The original OpenStreetMap
discovery dashboard is preserved as a second workspace.

---

## Quick start (Windows)

```bat
cd b2bprospects-v2
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements.txt
py -m playwright install chromium
py -m streamlit run app.py
```

macOS / Linux:

```bash
cd b2bprospects-v2
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
streamlit run app.py
```

Python 3.10+ and Streamlit 1.39+ are required. Playwright is needed only for the
Browser extraction mode; the app still runs without it and tells you what to install.

Run the tests:

```bash
python -m unittest discover -s tests -t .
```

Verify the browser engine against a real, permitted directory:

```bash
python scripts/verify_live.py https://permitted-directory.example/agents --max-pages 2 --max-records 10
```

Assess a site before quoting a customer:

```bash
python scripts/assess_site.py https://example.com/agents
```

---

## Two extraction modes

**Automatic** reads the HTML the server sends, using the original pipeline plus a
structured-data adapter. Fast, no browser, works on most server-rendered directories.
If it finds nothing, it says so instead of returning an empty list.

**Browser** launches Chromium, waits for the page to render, detects the repeated
records in the live DOM, and can drive pagination. Use it when the automatic method
reports that a page needs JavaScript.

Both modes end in the same quality screen and the same export.

## The workflow

1. **URL.** Paste the roster page — the page listing agents, not one profile.
2. **Read.** Automatic inspects the HTML; Browser opens the page and reports rows,
   pagination style, structured data and any embedded JSON payload.
3. **Choose fields.** Each field is filled from one detected column. The tool shows
   example values per column and proposes a mapping; you confirm or change it.
4. **Preview.** The first records are shown before a full run.
5. **Extract.** Pagination is followed automatically (next button, numbered pages,
   load-more, infinite scroll), bounded by page and record limits.
6. **Review and export.** Duplicates removed, per-field coverage, warnings, searchable
   table, CSV and a two-sheet Excel workbook.

## How it decides what to extract

Layers, most reliable first: embedded JSON payload → rendered DOM with a detected
repeated structure → the same structure across pages → per-profile enrichment →
a site-specific adapter. The method used is recorded on every record.

See `docs/BROWSER_ENGINE.md` for the detection algorithm, pagination strategies and
failure behaviour.

## How maintenance works

Every site-specific adapter is one file in `core/adapters/` implementing four methods:

```
matches(url)            can this adapter handle the site?
profile_links(page)     which URLs on this listing page are profiles?
next_page_urls(page)    how do we get the next page?
parse_profile(page)     turn one profile into a record
```

Politeness, normalization, deduplication, quality scoring, health tracking and export
live in the engine and never change when a site does.

## Run history and health

Runs are appended to `data/runs/history.jsonl`. For each site and adapter the tool
keeps a baseline (median of the last five successful runs) and classifies each run as
healthy, needs-review or broken — zero records, a large drop in volume, a field's
coverage collapsing, or a change in the listing markup. Warnings appear in the app and
inside the exported workbook.

## Configuration

All knobs live in `core/settings.py` and can be overridden with environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `B2B_REQUEST_DELAY` | `1.0` | Seconds between requests to the same host |
| `B2B_MAX_LIST_PAGES` | `20` | Default page cap |
| `B2B_MAX_PROFILES` | `500` | Default record/profile cap |
| `B2B_RESPECT_ROBOTS` | `true` | Enforce robots.txt (browser mode too) |
| `B2B_MAX_RETRIES` | `2` | Retries on 5xx / 429 (static mode) |
| `B2B_LICENSE_ENABLED` | `false` | Re-enable the original $49 export paywall |
| `B2B_DATA_DIR` | `./data` | Run history location |

## Tests

93 offline tests. They cover:

- structure detection on card and table layouts, and on a DOM produced by client-side
  rendering;
- column profiling, auto-mapping and manual remapping;
- all four pagination strategies, plus stalled-pagination handling;
- Playwright session control flow against a fake Playwright API;
- blocked sites, timeouts and challenge pages;
- normalization, deduplication, quality scoring, health, CSV and Excel;
- the Streamlit render path for both modes, using a strict stub;
- Node syntax-checking of every injected JavaScript snippet.

## Known limitations

- **No live browser run was possible in the build environment** (no network, no
  Playwright, no Chromium). Everything above the browser boundary is tested; the
  browser itself is not. Run `scripts/verify_live.py` once on your machine before
  quoting a customer. See `docs/PHASE2_AUDIT.md` for the exact list.
- **Click-to-select on the page is not wired into Streamlit.** The element picker
  JavaScript is written and syntax-tested, but returning the click to Python needs a
  bidirectional custom component that could not be built or tested here. Field
  selection works by choosing a column from example values instead.
- **Detection is exercised on two layouts** (cards and a table). Unusual designs may
  need a dedicated adapter; the tool reports low confidence rather than guessing.
- **Contact extraction on generic sites is heuristic.** Structured data is reliable;
  regex over rendered text can produce false positives. The `Quality flags` column
  says which rows lack contact details.
- **US-style addresses** (`City, ST ZIP`) parse reliably; other formats degrade.
- **No authentication.** One operator, local machine. Do not expose it publicly as-is.
- **OpenStreetMap services** are shared infrastructure; the discovery workspace is for
  light research, not bulk harvesting.

## Responsible use

Collect only publicly accessible, permitted business directory information. Respect
robots.txt, site terms and applicable law. Do not bypass logins, paywalls, CAPTCHAs or
other access controls — the tool stops on 401/403/429 and on challenge pages, and does
not attempt to evade bot detection. Public accessibility does not by itself establish
permission for commercial collection or redistribution.

See `OPERATOR_GUIDE.md` for the pre-delivery checklist.
