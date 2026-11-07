# B2BProspects Data Extraction — v3

A commercial-grade operator workspace for turning public business directories into
clean, deduplicated, source-traced datasets. It opens a directory in a real browser,
finds the repeating records, lets you map fields by clicking them, follows
pagination, cleans the data, and exports CSV, Excel or JSON.

The existing B2BProspects design and the original OpenStreetMap discovery workspace
are preserved. Directory Extraction is the primary workspace.

The feature list and the exact tested/unverified status are in
[`FEATURES_AND_TESTING.md`](FEATURES_AND_TESTING.md).

---

## What is new in v3

- **Visual click-to-select.** The rendered page is shown inside the dashboard. Choose
  a field, click one example value, and the picker recognises the repeating row and
  highlights its siblings. No CSS or selectors are shown to the operator.
- **Stronger detection.** Handles cards with unique per-record classes, tables,
  icon-only `tel:`/`mailto:` links, `data-phone` attributes, nested wrappers, and
  job-title columns that previously looked like names.
- **More modern-site coverage.** Reads `__NEXT_DATA__`, `__INITIAL_STATE__`,
  `application/json`, schema.org `@graph` / `ItemList`, and Next.js App Router
  `self.__next_f` flight chunks. Embedded records are used when they are the better
  source, with DOM fields merged in.
- **Arbitrary extra columns.** Keep titles, licence numbers, MLS IDs, languages or
  any other detected value. Extra columns are exported alongside the standard fields.
- **Real cleaning workflow.** Search, filter by contact/location/flag/missing field,
  choose a duplicate strategy, review issues, select export columns, and export the
  filtered dataset. Excel includes an Issues sheet when any row needs review.
- **Config-driven site adapters.** Add a JSON file in `core/adapters/sites/`; no
  Python required. The bundled demo directory is supported this way.
- **Pagination breadth.** Next button, numbered links, numbered buttons, query-param
  pages (`?page=`, `?offset=`), load-more and infinite scroll, each with stall
  detection.
- **Listing-page extraction.** If a roster already publishes complete records, the
  engine uses them and avoids hundreds of profile requests. Profile pages remain the
  fallback and enrichment source.
- **Robots policy is explicit.** `robots.txt` status is reported by default rather
  than automatically rejecting a site. Set `B2B_ROBOTS_POLICY=enforce` to hard-block.
- **Headless CLI and local demo server** for repeatable testing and delivery.

---

## Quick start (Windows)

See [`INSTALL.md`](INSTALL.md) for the full platform-by-platform setup, browser
installation, launch and troubleshooting.

```bat
cd b2bprospects-v3
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements.txt
py -m playwright install chromium
py -m streamlit run app.py
```

macOS / Linux:

```bash
cd b2bprospects-v3
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
streamlit run app.py
```

Python 3.10+ and Streamlit 1.39+ are required. Playwright is only needed for Browser
mode; Automatic mode still runs without it and tells you what to install.

### Try it without touching a real website

Terminal 1:

```bash
python scripts/serve_demo.py --port 8765
```

Terminal 2:

```bash
# PowerShell
$env:B2B_ALLOW_LOCAL="true"; streamlit run app.py
# cmd
set B2B_ALLOW_LOCAL=true && streamlit run app.py
# macOS/Linux
B2B_ALLOW_LOCAL=true streamlit run app.py
```

Enter `http://127.0.0.1:8765/agents`. The demo has 36 agents over three pages,
profiles, schema.org data, a JSON site adapter, extra title/licence columns, and a
profile email revealed by an ordinary "Show email" click.

Or run the headless extractor:

```bash
python scripts/extract_cli.py http://127.0.0.1:8765/agents --allow-local --max-pages 3
```

---

## The workflow

1. **URL.** Paste the roster page that lists the people or businesses.
2. **Read.** Automatic reads the server HTML; Browser renders the page and reports
   rows, pagination, embedded payloads and robots status.
3. **Map.** Click values in the visual picker, or accept/change the column mapping.
   Add extra columns by name.
4. **Preview.** The first records are shown before a full run.
5. **Extract.** Pagination is followed automatically, bounded by page and record
   limits. Every warning is recorded.
6. **Clean and export.** Filter, choose a duplicate strategy, review issues, select
   columns, then download CSV, Excel or JSON.

The visual picker is a real interactive feature: it does not merely highlight an
overlay. It returns the clicked element's position path to Python, which maps it to
the same column model used by extraction.

---

## Two extraction modes

**Automatic** reads the HTML the server sends. It uses schema.org, embedded JSON and
the same structure detector as the browser, then follows profile links or uses rich
listing cards directly. Fast and browser-free.

**Browser** launches Chromium, waits for rendering, scrolls lazy content, detects the
live DOM, and drives pagination. It prefers embedded JSON when that is the better
source, then merges DOM fields into it.

Both modes end in the same quality, cleaning and export screen.

### Engine capabilities

Static and browser paths support:

- static HTML, React/Next.js/Nuxt-style pages, JavaScript-rendered lists;
- schema.org JSON-LD, `@graph`, `ItemList`, and Next.js flight payloads;
- repeating cards, tables, grids, and cards with unique per-record classes;
- next-page links, numbered links/buttons, query parameters, load-more, infinite
  scroll;
- listing-level records, individual profile pages, and profile enrichment;
- ordinary "show phone / show email / contact details" interactions, without
  bypassing logins, paywalls or CAPTCHAs;
- arbitrary extra fields, not just the fixed catalogue.

---

## Adding a site adapter without code

Drop a JSON file into `core/adapters/sites/`. Example:

```json
{
  "id": "acme",
  "label": "Acme Realty",
  "domains": ["acme.example"],
  "listing": {
    "row": "article.agent-card",
    "profile_link": "a.agent-card__link",
    "next": "a.pagination__next",
    "page_param": "page",
    "record_fields": {
      "agent_name": ".agent-card__name",
      "phone": ".agent-card__phone",
      "email": ".agent-card__email",
      "title": ".agent-card__title"
    }
  },
  "profile": {
    "fields": {
      "agent_name": "h1.agent-name",
      "brokerage_name": ".office-name",
      "phone": "a[href^='tel:']",
      "email": "a[href^='mailto:']",
      "website": ".website a@href",
      "license_number": ".license"
    }
  }
}
```

Selector forms: `css` (text), `css@attr` (attribute), `a || b` (first match). Any field
name outside the catalogue becomes an extra exported column. See
`core/adapters/sites/README.md`.

For unusual structures a Python adapter is still one file in `core/adapters/`, as
before.

---

## Command line

```bash
# inspect only
python scripts/extract_cli.py https://example.com/agents --inspect

# static extraction, all formats
python scripts/extract_cli.py https://example.com/agents --max-pages 10 --format all

# browser extraction with profile enrichment
python scripts/extract_cli.py https://example.com/agents --engine browser --enrich --max-pages 5

# choose fields
python scripts/extract_cli.py https://example.com/agents --fields agent_name,brokerage_name,phone,email
```

Outputs default to `data/exports/`. Use `--out` to choose a basename or path.

Live verification on your machine:

```bash
python scripts/verify_live.py https://permitted-directory.example/agents --max-pages 2 --max-records 10
python scripts/verify_live.py http://127.0.0.1:8765/agents --allow-local
```

Pre-engagement assessment:

```bash
python scripts/assess_site.py https://example.com/agents
```

---

## Configuration

All knobs live in `core/settings.py` and can be overridden with environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `B2B_REQUEST_DELAY` | `1.0` | Seconds between requests to the same host |
| `B2B_MAX_LIST_PAGES` | `20` | Default listing page cap |
| `B2B_MAX_PROFILES` | `500` | Default profile cap |
| `B2B_MAX_RETRIES` | `2` | Retries on 5xx / 429 |
| `B2B_ROBOTS_POLICY` | `warn` | `warn` reports robots.txt; `enforce` blocks; `ignore` stays silent |
| `B2B_RESPECT_ROBOTS` | `false` | Legacy switch; `true` means enforce |
| `B2B_ALLOW_LOCAL` | `false` | Permit loopback URLs for the demo server |
| `B2B_BROWSER_HEADLESS` | `true` | Run Chromium headless |
| `B2B_BROWSER_BLOCK_MEDIA` | `false` | Skip images/fonts for faster extraction |
| `B2B_BROWSER_REVEAL_CONTACTS` | `true` | Click ordinary "show phone/email" controls |
| `B2B_PREFER_EMBEDDED` | `true` | Prefer a richer embedded payload over DOM cards |
| `B2B_PREFER_LISTING_RECORDS` | `true` | Use complete listing cards instead of opening every profile |
| `B2B_MAX_SNAPSHOT_BYTES` | `4,000,000` | Visual-picker snapshot limit |
| `B2B_LICENSE_ENABLED` | `false` | Re-enable the original $49 export paywall |
| `B2B_DATA_DIR` | `./data` | Run history, exports, screenshots |

---

## Tests

Run:

```bash
python -m unittest discover -s tests -t .
```

139 tests currently pass. They cover:

- normalization (phones with extensions, international addresses, obfuscated emails);
- schema.org, `@graph`, `ItemList`, embedded JSON and Next.js flight payloads;
- card, table, unique-class, icon-contact and nested layouts;
- all pagination styles plus stalled pagination;
- extra columns through mapping, records and export;
- cleaning, filters, duplicate strategies and issue review;
- config-driven adapters and the bundled demo directory;
- **real HTTP** extraction against the local demo server, including the browser
  engine's navigation and pagination over HTTP;
- the CLI writing a 36-row CSV;
- Playwright control flow against a fake Playwright API;
- Streamlit render paths for all views, including the visual picker fallback and the
  embedded-only extraction screen;
- Node syntax-checking of every injected JavaScript snippet and the picker UI.

What the test suite cannot prove in this build environment: that Chromium renders a
specific live site, that the Streamlit custom component appears correctly in your
browser, and that any given third-party site will not block access. See
`docs/V3_CHANGES.md` for the exact tested/unverified list.

---

## Known limitations

- **No external live-site run was possible in the build environment.** Egress is
  proxy-blocked, Playwright and Streamlit are not installed. The browser engine was
  exercised over real HTTP against the demo server using the same session protocol,
  but real Chromium execution is verified only by `scripts/verify_live.py` on your
  machine.
- **The visual picker component is new.** Its Python mapping logic, sanitizer and
  JavaScript syntax are tested; it has not been rendered inside a real Streamlit
  server here. If the component does not load, the column dropdowns remain fully
  functional.
- **Detection generalises, but not infinitely.** Unusual layouts may need a config
  or Python adapter. The tool reports low confidence rather than inventing records.
- **Contact regexes can produce false positives.** Structured data and `tel:`/
  `mailto:` links are preferred. Review flagged rows before delivery.
- **Address parsing** covers US formats well and common CA/UK/AU tails; other formats
  may need an adapter.
- **No authentication.** One operator, local machine. Do not expose the app publicly
  as-is.
- **OpenStreetMap services** are shared infrastructure; use the discovery workspace
  for light research, not bulk harvesting.

---

## Responsible use

Collect only publicly accessible, permitted business directory information. Respect
site terms and applicable law. robots.txt is reported and configurable; it is one
input, not proof of permission. Do not bypass logins, paywalls, CAPTCHAs or other
access controls. The tool stops on 401/403/429 and on challenge pages, and does not
attempt to evade bot detection.

See `OPERATOR_GUIDE.md` for the pre-delivery checklist and `docs/V3_CHANGES.md` for
the implementation and testing record.
