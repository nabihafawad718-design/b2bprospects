> **Historical note:** this is the Phase 1 audit and plan. The browser engine is
> documented in `docs/BROWSER_ENGINE.md`; the v3 implementation and testing record
> is `docs/V3_CHANGES.md`.

# B2BProspects — Stage 1 Audit & Stage 2 Technical Plan

Audited from the supplied `b2bprospects-main (3).zip`. Read in full: `app.py` (1,036 lines), `README.md`,
`requirements.txt`, `config.toml`, and the asset files. Your original ZIP and extracted read-only copy were not modified.

---

## 1. What the application actually is

The existing app is **not** a directory scraper. It is a single-file Streamlit "prospecting portal" that:

1. Geocodes a city name via **Nominatim** (OpenStreetMap).
2. Builds an **Overpass API** query for a business category (e.g. `office=estate_agent`) inside that city.
3. Parses returned OSM elements into a table: name, category, website, phone, email, address, map link.
4. Runs a **bounded best-effort enrichment pass**: for up to 20 businesses missing a phone or email, it fetches
   the homepage and one Contact/About page and regex-scrapes an email and phone from the raw HTML.
5. Displays the result with filters ("no website", "social-media-only", "incomplete listing"), a data grid,
   and CSV/Excel export gated behind a license key (hardcoded `B2BPROSPECTS2026`, `$49` checkout copy).

So it is a **local-business discovery tool keyed on city + industry**, with a light contact-enrichment step.
Your stated product is **"enter a brokerage directory URL and extract agent profiles"**. Those are different
workflows. The extraction workflow does not exist anywhere in this code.

## 2. Architecture map

| Lines | Section | Notes |
|---|---|---|
| 1–37 | Config, constants | 3 Overpass endpoints, global HTTP headers |
| 40–47 | License resolution | `st.secrets["LICENSE_KEY"]` fallback to constant |
| 52–299 | CSS design system | ~245 lines, custom properties, card/chip/metric/paywall classes |
| 303–306 | CSS injection | optionally hides the dataframe toolbar |
| 325–386 | Category rules | ~55 keyword → OSM tag mappings |
| 388–439 | Query builder | stemming, tag selectors, Overpass QL assembly |
| 442–519 | Geocode + Overpass transport | retry/failover across 3 endpoints |
| 522–586 | OSM parsing | tags → DataFrame |
| 589–649 | Contact enrichment | regex over raw HTML, max 20 sites |
| 652–671 | `fetch_leads` | cached 3,600s |
| 677–714 | Filter logic | opportunity masks, ANY/ALL combining |
| 717–724 | Excel export | openpyxl, silently returns `None` on failure |
| 730–754 | UI helpers | metric card, section header, footer |
| 760–797 | Session state + sidebar | |
| 801–905 | Hero, search dashboard, metrics | |
| 910–975 | Filters + data grid | |
| 980–1036 | Paywall / export | |

Single module, no package structure, no tests, no logging, no persistence. Everything is recomputed in-process.

## 3. What genuinely works and is worth reusing

- **The entire visual design system.** The CSS is well-built: consistent tokens, card/chip/metric patterns,
  paywall treatment, responsive grid. This is your strongest existing asset and is preserved verbatim.
- **`section_header`, `metric_card`, footer, card containers** — clean, reusable primitives.
- **Filters + data grid + export flow** — the interaction pattern is sound and is reused for extracted records.
- **Resilient HTTP thinking** — multiple endpoints, retry, typed exceptions, user-facing error messages.
- **Normalization of phone/email basics** — `_first_value`, `mailto:` stripping, `clean_website`.
- **Category keyword mapping** — useful for the retained discovery mode.

## 4. Defects and limitations found in the existing code

These are real, not stylistic:

1. **`config.toml` is in the wrong place.** Streamlit only reads `.streamlit/config.toml`. A root-level
   `config.toml` is ignored, so `enableStaticServing = true` never applies.
2. **`logo.png`, `static/logo.png`, `favicon.ico` are shipped but never referenced.** `page_icon` is an emoji.
   Dead weight, and the brand is inconsistent with the assets.
3. **No `robots.txt` check anywhere**, despite the README claiming respect for site terms. `enrich_public_websites`
   fetches arbitrary business sites with no permission check.
4. **High false-positive contact extraction.** `_extract_public_contacts` regexes the entire document including
   `<script>` blocks and inline JSON. It will match analytics IDs, CDN emails, `noreply@`, hashed asset names.
   There is no validation beyond a file-extension blacklist.
5. **Blocking, slow enrichment.** Up to 20 sites × (4–6 s homepage + 3–5 s contact page), serial, inside the
   Streamlit run thread. A single search can block the UI for over two minutes with no progress feedback.
6. **No rate limiting or delay between outbound requests** — one connection immediately after another.
7. **No SSRF guard** on URLs taken from OSM tags before server-side fetching.
8. **Empty results are indistinguishable from failures.** If Overpass returns elements but the parser yields zero
   named rows, the UI says "no businesses found" rather than "extraction failed".
9. **Silent Excel failure.** `to_excel_bytes` swallows every exception and returns `None`; the download button
   simply disappears with no explanation.
10. **`sanitize_term` strips all non-ASCII**, so non-English keywords silently become empty or truncated.
11. **Paywall is not security.** Export is blocked by hiding the dataframe toolbar with CSS and a client-side
    session flag; the key sits in source. Fine for a prototype, not for billed work.
12. **`unlocked` resets on page refresh**, so a paying operator re-enters the key every session.
13. **No persistence.** Closing the tab loses the dataset; there is no run history, no audit trail, no provenance.
14. **No duplicate detection.** OSM unions usually dedupe by ID, but nothing guards the final table.
15. **Global `HTTP_HEADERS` has no `Accept-Language`**, which causes 403s or wrong-language pages on some sites.
16. **`opportunity_masks` treats `NaN` inconsistently** across columns built from `None`.

## 5. The core gap

Requested workflow (Steps 1–6 of your brief) vs. what exists:

| Requested | Exists today |
|---|---|
| Enter a directory URL | ✗ (city + keyword only) |
| Inspect page structure, structured data, profile links, pagination, load-more | ✗ |
| Select extraction fields | ✗ (fixed columns) |
| Adapter-based extraction incl. JS pages | ✗ |
| Duplicate/missing-field detection, success rate, timestamps, warnings | ✗ |
| Detect that a site changed and an adapter broke | ✗ |
| Preview + CSV/Excel | ✓ (exists, reused) |

## 6. Decision: dual-mode, not replacement

I will **add the directory-extraction workflow as the primary mode** and **keep OSM discovery intact as a second
mode**, sharing the same design system. Rationale: your brief forbids removing working functionality, and OSM
discovery is genuinely useful for finding brokerages to target. The URL-extraction engine is new code; the OSM
path is moved, not rewritten, so its behaviour is preserved.

Where I deliberately diverge from the original, with reasons:

- **Paywall becomes configurable** (`LICENSE_ENABLED`, default **off**). You said this is an internal tool for
  delivering paid projects; a paywall that locks *your own* exports is a liability. The component and all its
  styling remain, and flipping one flag restores the exact current behaviour.
- **Contact heuristics are hardened** (robots.txt, delays, script-block stripping, validation) because shipping
  the current version to paying clients carries real legal and quality risk.
- **Persistence added** (`data/runs/`) because "warnings when results are incomplete" and "detect when an adapter
  stops working" are impossible without remembering previous runs.

---

## Stage 2 — Technical plan

### New files

| File | Purpose |
|---|---|
| `core/settings.py` | Central config: politeness, caps, thresholds, feature flags, paths |
| `core/models.py` | `FieldSpec`, `RawRecord`, `Record`, `InspectionReport`, `QualityReport`, `RunResult` |
| `core/net.py` | Polite fetcher: robots.txt, per-host rate limit, SSRF guard, retry/backoff, typed errors |
| `core/inspect_site.py` | URL inspection: JSON-LD inventory, repeated-block detection, pagination, load-more, sitemaps |
| `core/normalize.py` | Phone → display + digits, email validation, name/address cleanup, URL canonicalisation |
| `core/quality.py` | Duplicate detection, missing-field rates, success rate, threshold warnings |
| `core/exporters.py` | CSV + multi-sheet Excel with provenance and run summary |
| `core/runs.py` | Run history JSONL, per-adapter baselines, structure-signature comparison |
| `core/engine.py` | Orchestrates inspect → extract → normalize → validate → persist |
| `core/adapters/base.py` | `DirectoryAdapter` ABC + `AdapterContext` |
| `core/adapters/registry.py` | Domain → adapter resolution, generic fallbacks |
| `core/adapters/generic_jsonld.py` | schema.org `RealEstateAgent`/`Person`/`Organization` extraction |
| `core/adapters/generic_html.py` | Heuristic container + profile extraction for unknown sites |
| `core/adapters/example_brokerage.py` | Reference dedicated adapter, the template for site-specific fixes |
| `ui/theme.py` | Existing CSS moved **verbatim** |
| `ui/components.py` | Existing helpers moved verbatim + new status/health components |
| `ui/views/extract.py` | Steps 1–6 workflow UI |
| `ui/views/discover.py` | Existing OSM UI, preserved |
| `ui/views/health.py` | Adapter health dashboard |
| `ui/views/history.py` | Past runs |
| `tests/` | Fixture HTML + pytest suite |
| `OPERATOR_GUIDE.md` | Runbook for delivering a customer project |
| `.streamlit/config.toml` | Correct config location |

### Modified files

| File | Change |
|---|---|
| `app.py` | Becomes the orchestrator: same `set_page_config`, CSS injection, sidebar, hero, footer; adds a mode switch; delegates to views |
| `requirements.txt` | Add `beautifulsoup4`, `lxml`, `pytest` |
| `README.md` | Rewritten to match reality (current README describes a prototype that no longer matches) |

### Preserved exactly

CSS text, class names, card keys, hero markup, metric/filter/table/paywall styling, OSM query + parsing logic,
filter semantics, export column names.

### Adapter contract

```python
class DirectoryAdapter:
    id: str; label: str; domains: tuple[str, ...]
    expected_fields: tuple[str, ...]
    def matches(url, html) -> bool
    def inspect(page) -> InspectionReport
    def listing_page_urls(page, ctx) -> list[str]      # pagination / load-more
    def profile_links(page, ctx) -> list[str]
    def parse_profile(page, ctx) -> RawRecord | None
```

A site-specific fix is one file. The engine, validation, export and UI never change.

### Health / regression detection

Per adapter + domain, persist: record count, per-field fill rate, list-container DOM signature, pagination pages
walked, error count. Compare each run to the rolling baseline and classify:

- `OK` — within tolerance
- `WATCH` — a field fill rate dropped > 25 points, or record count down 20–50%
- `BROKEN` — zero records, missing names, or record count down > 50%, or HTTP 401/403/429

Every warning is surfaced in the UI and written into the exported workbook, so an incomplete dataset can never be
mistaken for a complete one.

### Testing strategy in this environment

This sandbox has **no outbound network and no Streamlit installed**. Therefore:

- Extraction, inspection, normalization, validation and export are tested against **saved HTML fixtures** with pytest.
- A **Streamlit stub harness** exercises the render path of `app.py` to catch runtime errors in view code.
- Live-site verification must run on your machine; `OPERATOR_GUIDE.md` gives a 10-minute acceptance checklist.

I will not claim a site works unless it was exercised.
