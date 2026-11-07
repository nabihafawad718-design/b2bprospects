# Browser Engine — architecture and limits

This document describes how the browser extraction engine works, what it will and
will not do, and what remains unproven. It is written to be read before you promise
a customer that a particular site can be extracted.

---

## 1. Where it sits

```
ui/views/extract.py            Step 1: URL + mode selector
ui/views/browser_flow.py       Steps 2–5 in browser mode (mapping, run)
ui/views/result_view.py        Steps 5–6 shared by both modes (quality, preview, export)
        │
core/browser/engine.py         orchestration: inspect → walk → extract → score → persist
core/browser/driver.py         Playwright session (the only file that imports Playwright)
core/browser/pagination.py     next / numbered / load-more / infinite-scroll walkers
core/browser/structure.py      repeated-region detection + column profiling
core/browser/extract.py        field mapping and record extraction
core/browser/embedded.py       JSON payload layer (__NEXT_DATA__ and friends)
core/browser/js.py             injected JavaScript (picker, scroll, overlays, signatures)
```

`core/` still imports no Streamlit, so everything below `ui/` is testable without a
server and reusable from the command line.

## 2. The layered strategy

Cheapest and most reliable first. The method actually used is recorded on every
record (`Extraction method` column) and in the workbook's Run Summary.

| Layer | When it wins | Output marker |
|---|---|---|
| 1. Embedded JSON | The site ships records in `__NEXT_DATA__`, `__INITIAL_STATE__` or an `application/json` block | `embedded_json` |
| 2. Rendered DOM | The browser has produced real repeating cards or table rows | `browser_dom` |
| 3. Pagination | Layer 2 repeated across pages | same, with page count |
| 4. Profile enrichment | Contact details live on the individual profile | merged fields |
| 5. Site adapter | Layout defeats generic detection | `custom-selectors` |

Layer 5 is the existing static adapter mechanism. A new site is still one file in
`core/adapters/`.

## 3. Detection: how a "row" is found

`structure.analyze_html` scans container elements whose direct children repeat a
shape (tag + class signature). A group needs at least three children, at least two
of which look like records. Each row is flattened into **position-path cells**:

```
1.0.0   →  "Jane Smith"          (the anchor inside the name heading)
1.2.0   →  "(512) 555-0142"      (the phone list item)
1.3     →  "3300 Bee Caves Rd, Austin, TX 78746"
```

For elements with element children, only their own text nodes are read, so a
wrapper `<a>` around a whole card does not produce a catch-all column that
duplicates every field.

Columns are then profiled: fill rate, href rate, sample values, and a guessed kind
(`name`, `phone`, `email`, `address`, `city`, `brokerage`, `url`). The guess order
matters — names are frequently links, so the name test runs before the URL test,
and a constant firm-shaped value is treated as the brokerage before it can be read
as a person's name.

## 4. Field mapping without selectors

The mapping is `field → column index`. The UI lists each column with example
values; the operator accepts the suggestion or picks a different column. No CSS is
ever shown.

The auto-mapping is a guess and is presented as one. The preview table under the
mapping is generated from the same mapping, so a wrong guess is visible before a
full run.

## 5. Pagination

| Strategy | Detection | Drive |
|---|---|---|
| `next_button` | `rel=next`, or text `next` / `›` / `»` | click, wait for row signature to change |
| `numbered` | two or more numeric links sharing a path | open each collected URL directly |
| `load_more` | button/link text matching *load/show/view more* | click until it disappears or stops adding |
| `infinite_scroll` | scroll sentinel, or a populated page with no controls | scroll to bottom until height stops growing |
| `none` | nothing found | single page |

Every walker is bounded by `max_pages` and `max_records`. Each iteration compares a
signature of the first twelve rows before and after the action. If the content does
not change, the walk stops and reports that rather than looping or silently
returning the same page twice.

## 6. Failure behaviour

| Condition | Behaviour |
|---|---|
| HTTP 401 / 403 / 429 | `BrowserBlocked`, run stops, no retry loop |
| Challenge page (Cloudflare, "verify you are human") | `BrowserBlocked` |
| Navigation timeout | `BrowserTimeout` with the page URL |
| Pagination does not advance | Stops, warning recorded |
| No structure detected | Run returns zero records with an explanation, never a fake success |
| Zero records but structure found | Marked broken, with the mapping named as the likely cause |

Warnings are stored on the run and written into the exported workbook.

## 7. Visual selection: what is built and what is not

Built and tested (`core/browser/js.py`):

- `PICKER_JS` — a real element picker. Hover highlights the element, click resolves
  a stable CSS path, walks up to find the repeated row group, and stores
  `{selector, groupSelector, groupCount, rowSelector, text, tag}` on
  `window.__b2bPicker.result`.
- `READ_PICKER_JS`, `STOP_PICKER_JS` — transport and teardown hooks.
- `DISMISS_OVERLAYS_JS` — best-effort cookie/consent dismissal with a small,
  label-based allowlist.
- `CONTENT_SIGNATURE_JS` — change detection for pagination.
- `SCROLL_BOTTOM_JS`, `PAGE_METRICS_JS`.

All snippets are syntax-checked with Node in the test suite.

Not built: the transport that carries the click from the page back into Streamlit.
Streamlit's built-in iframe component cannot return a value to Python; that needs a
bidirectional custom component, which requires Node tooling and a browser to build
and test. Rather than ship an untested component, this phase ships the picker
itself plus the column-based mapping UI, which achieves the same outcome (choose by
example, not by CSS) without it.

When the component is added, `PICKER_JS` is the only browser-side piece it needs.

## 8. Responsible-use posture

Enforced in code, not just documented:

- robots.txt is checked before every navigation, through the same policy the static
  fetcher uses.
- 401/403/429 and challenge pages stop the run.
- No CAPTCHA solving, no stealth patches, no `AutomationControlled` hiding, no
  proxy rotation, no login handling.
- A realistic desktop user-agent and viewport are used, nothing more.
- Requests between profile pages are spaced by `B2B_REQUEST_DELAY`.

The tool cannot determine whether you have permission to collect or redistribute a
site's data. `scripts/assess_site.py` ends with the checklist that decision requires.

## 9. What is proven, and what is not

Proven offline (93 tests):

- Detection on card and table layouts, including a DOM produced by client-side
  rendering.
- Column profiling and auto-mapping.
- All four pagination strategies, including stalled-pagination handling.
- Blocked-site handling at the driver and engine level.
- Playwright session control flow against a fake Playwright API: navigation, status
  codes, challenge detection, timeouts, clicks, cleanup.
- Normalization, deduplication, quality scoring, health, CSV and Excel export of
  browser-extracted records.
- The Streamlit render path for browser mode, including the install-help path.
- Syntax of all injected JavaScript.

Not proven here:

- A real Chromium executing a real site's JavaScript (no browser or network in the
  build environment).
- Detection accuracy across arbitrary brokerages — only two layouts were exercised.
- Behaviour against a site's actual anti-bot posture.

`scripts/verify_live.py` closes the first gap in one command on a machine with
network access.
