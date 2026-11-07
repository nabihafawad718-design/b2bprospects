# Browser Engine — architecture and limits (v4)

This document describes how the browser extraction engine works, what it will and
will not do, and what remains unproven. Read it before promising a customer that a
particular site can be extracted.

---

## 1. Where it sits

```
ui/views/extract.py            Step 1: URL + mode selector
ui/views/browser_flow.py       Steps 2–5: analyse, visual picker, mapping, run
ui/visual_picker/              Streamlit custom component: click-to-select
ui/views/result_view.py        Steps 5–6: quality, cleaning, preview, export
        │
core/browser/engine.py         orchestration: inspect → walk → extract → merge → score
core/browser/driver.py         Playwright session (the only file that imports Playwright)
core/browser/pagination.py     next / numbered / buttons / query-param / load-more / infinite
core/browser/structure.py      repeated-region detection + column profiling
core/browser/extract.py        field mapping, row fallbacks, profile enrichment
core/browser/embedded.py       JSON layer: Next.js flight, __NEXT_DATA__, @graph, ItemList
core/browser/js.py             injected JavaScript (picker, scroll, overlays, signatures)
```

`core/` imports no Streamlit, so everything below `ui/` is testable without a server
and reusable from the command line.

## 2. The layered strategy

Layers are tried in reliability order and the first identity-bearing result wins. A
layer that produces nothing is recorded as `empty` and the next is attempted. Every
attempt appears in the run diagnosis and the workbook's Run Summary.

| Layer | When it wins | Output marker |
|---|---|---|
| 1. Site adapter | A configured domain with listing/profile rules | adapter id |
| 2. schema.org listing | Complete Person/RealEstateAgent nodes on the roster | `jsonld_listing` |
| 3. Embedded JSON | Next.js flight, `__NEXT_DATA__`, `@graph`, `application/json` | `embedded_json` |
| 4. Rendered DOM | Live cards, tables, grids, unique-class cards, link clusters | `browser_dom` |
| 5. Profile pages | The listing has no usable card data | `browser_profiles` |
| 6. Sitemap | The roster has no links but profiles are in `sitemap.xml` | `sitemap_profiles` |

Embedded/structured records are preferred when they are at least as rich as the DOM.
DOM fields are then merged into them, so a payload with names and phones gains the
card's address or website without duplicating people. If an explicit mapping was
chosen, the DOM layer runs first so the operator's choice is honoured.

A listing page that publishes complete records is a legitimate source on its own.
Static extraction uses those records when both phone and email are present and field
coverage is good; profile pages remain the fallback and enrichment source. This is
what makes a 500-agent roster practical without 500 requests.

Explicit refusals (HTTP 401/403/429, anti-bot challenge) stop the run immediately and
are never retried by another layer.

## 3. Detection: how a "row" is found

`structure.analyze_html` scans container elements whose direct children repeat a
shape. A group needs at least three children, at least two of which look like
records. Each row is flattened into **position-path cells**:

```
1.0.0   →  "Jane Smith"          (the anchor inside the name heading)
1.2.0   →  "(512) 555-0142"      (the phone list item)
1.3     →  "3300 Bee Caves Rd, Austin, TX 78746"
```

For elements with element children, only their own text nodes are read, so a wrapper
`<a>` around a whole card does not produce a catch-all column.

v3 additions:

- **Unique classes per card** (`card-0`, `card-1`, CSS-module hashes) are grouped by
  tag inside the container, and the row selector is generated as
  `container > tag` rather than a class that matches one row.
- **Job titles** ("Senior Agent", "Team Lead") are no longer classified as names.
- **Icon-only contact links** are read from `tel:` / `mailto:` hrefs.
- **Data attributes** such as `data-phone`, `data-email`, `data-address` are read
  when an element has no text.
- **State classes** (`odd`, `even`, `active`, `col-*`, numeric suffixes) do not split
  a group.

Columns are profiled: fill rate, href rate, sample values, and a guessed kind
(`name`, `phone`, `email`, `address`, `city`, `brokerage`, `website`, `url`, `text`).
The guess order matters: names are frequently links, so the name test runs before the
URL test, and a firm-shaped value is treated as the brokerage before it can be read
as a person.

## 4. Field mapping and row fallbacks

The mapping is `field → column index`. Any field key outside the catalogue becomes an
extra exported column (title, licence number, MLS ID, languages, …).

A mapped field that the chosen column leaves blank is filled from the row itself:

- phone from a `tel:` anchor, then from row text;
- email from a `mailto:` anchor, then from row text;
- address from a street-address pattern;
- website from an external anchor or domain-shaped link;
- profile URL by scoring every anchor (profile-path hints, same-host, label text)
  instead of taking the first, which is usually the logo;
- name from a heading, name-shaped anchor, or image alt text.

These fallbacks respect the mapping: if the operator chose "do not collect" for a
field, it is not silently added.

## 5. Pagination

| Strategy | Detection | Drive |
|---|---|---|
| `next_button` | `rel=next`, text `next`/`›`/`»`, aria-label | click, try selectors then text |
| `numbered` | two or more numeric links in one pager | open each URL directly |
| `numbered_buttons` | two or more numeric `<button>` controls | click by text |
| `query_param` | `page`/`p`/`offset` in links or current URL | construct and open each URL |
| `load_more` | button/link text matching *load/show/view more* | click until it disappears or stops adding |
| `infinite_scroll` | scroll sentinel, or a populated page with no controls | scroll until height stops growing |
| `none` | nothing found | single page |

Every walker is bounded by `max_pages` and `max_records`. Each iteration compares a
signature of the first twelve rows before and after the action. If content does not
change, the walk stops and reports that rather than looping.

## 6. Embedded payloads

`embedded.py` reads:

- `__NEXT_DATA__`, `__NUXT_DATA__`, `__APOLLO_STATE__`;
- `application/json` and `application/ld+json` blocks;
- `window.__X__ = {...}` assignments;
- Next.js App Router `self.__next_f.push([1, "…"])` flight chunks, via a balanced JSON
  scanner after string unescaping;
- schema.org `@graph`, `ItemList`, and ListItem wrappers containing Person objects.

It is conservative about what counts as a record. A `{name, url}` array is usually
navigation, so a record needs a contact detail or a name plus an address/company/
location signal. Menu, product and blog arrays are ignored, and the DOM path is used
instead. This distinction is tested.

## 7. Visual click-to-select

The captured rendered HTML is sanitised (scripts, frames, event handlers, CSP/meta
refresh removed), given a `<base>` so relative assets resolve, and shown inside a
Streamlit custom component. The component:

- highlights the element under the cursor;
- finds the closest ancestor that repeats with siblings of the same shape;
- outlines the repeated rows;
- on click, computes the element's **position path inside its row** using the same
  all-element-sibling index as Python;
- sends `{path, text, row_selector, group_count, field, seq}` back to Python.

Python maps the path to a detected column (`column_for_click`): exact path, then
nearest prefix, then sample-text match. That is why clicking an agent's name is
equivalent to choosing the column whose samples are names.

The component is implemented directly in `ui/visual_picker/index.html`; there is no
Node build step. If it cannot load or the snapshot exceeds
`B2B_MAX_SNAPSHOT_BYTES`, the dashboard falls back to column dropdowns.

## 8. Failure behaviour

| Condition | Behaviour |
|---|---|
| HTTP 401 / 403 / 429 | `BrowserBlocked`, run stops, no retry loop |
| Challenge page (Cloudflare, "verify you are human") | `BrowserBlocked` |
| HTTP 404 | `BrowserError` with the URL |
| Navigation timeout | `BrowserTimeout` with the page URL |
| Pagination does not advance | Stops, warning recorded |
| No structure and no payload | Zero records with an explanation, never a fake success |
| Structure found but zero mapped records | Marked broken, mapping named as likely cause |
| robots.txt disallowed | Reported by default; enforced only when configured |

Warnings are stored on the run and written into the exported workbook. Screenshots
are saved for browser errors when enabled.

## 9. Responsible-use posture

Enforced in code, not just documented:

- robots.txt is checked and reported before navigation. Default policy is `warn`;
  `enforce` restores hard blocking. A warn notice is not permission.
- 401/403/429 and challenge pages stop the run.
- No CAPTCHA solving, stealth patches, `AutomationControlled` hiding, proxy rotation
  or login handling.
- Only ordinary public interactions are used: cookie dismissal, scrolling, and
  "show phone / show email / contact details" controls that a visitor can operate.
- Requests between profile pages are spaced by `B2B_REQUEST_DELAY`.

The tool cannot determine whether you have permission to collect or redistribute a
site's data. `scripts/assess_site.py` ends with the checklist that decision requires.

## 10. What is proven, and what is not

Proven in this build environment:

- detection on card, table, unique-class, icon-contact and nested layouts;
- mapping, extra columns, row fallbacks and URL scoring;
- all pagination strategies, including stalled-pagination handling;
- embedded JSON, Next.js flight, `@graph` and `ItemList` extraction;
- menu/product false-positive rejection;
- Playwright session control flow against a fake Playwright API;
- **real HTTP** extraction against the local demo server, including browser-engine
  navigation and pagination through an HTTP session implementing the same protocol;
- normalization, deduplication, cleaning, filtering, issue review, CSV/Excel/JSON;
- Streamlit render paths, including embedded-only extraction and picker fallback;
- JavaScript syntax of every injected snippet and the picker UI.

Not proven here:

- a real Chromium executing a real site's JavaScript (no browser or external network
  in the build environment);
- the visual picker rendered inside a live Streamlit server;
- detection accuracy across arbitrary brokerages;
- behaviour against a site's actual anti-bot posture.

`scripts/verify_live.py` closes the browser gap on your machine. The demo server
closes the end-to-end gap without touching a third-party site.
