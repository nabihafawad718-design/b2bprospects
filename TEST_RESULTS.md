# Test results — v4

Run: 2026-11-07 21:27 UTC (build sandbox)
Python: 3.10.12 · Node: v22.23.4
Platform: Linux sandbox (Windows target verified by the test suite's platform-neutral code)

---

## 1. Automated suite

Command:

```bash
python -m unittest discover -s tests -t .
```

Result:

```
Ran 163 tests in 11.762s

OK
```

The suite includes unit tests, fixture-based detection tests, scripted-browser tests,
real-HTTP loopback integration tests, Streamlit render-path tests against a strict
stub, and Node syntax checks for every injected JavaScript snippet.

---

## 2. Local multi-layout matrix (real HTTP on loopback)

The demo server is a real HTTP server. Each row below is a real request cycle through
the extraction engine; the sites are synthetic, not third-party directories.

### Static engine

| Path | Records | Pages | Method used | Quality |
|---|---:|---:|---|---|
| `/agents` | 36 | 3 | `adapter_listing` | ok |
| `/path/1` | 36 | 3 | `adapter_listing` | ok |
| `/table` | 10 | 1 | `generic_listing` | watch |
| `/embedded` | 10 | 1 | `embedded_listing` | ok |
| `/irregular` | 5 | 1 | `profiles` | watch |
| `/sitemap-only` | 3 | 1 | `sitemap_profiles` | watch |

### Browser engine (rendered-DOM HTTP session, no JavaScript execution)

| Path | Records | Pages | Method used | Quality |
|---|---:|---:|---|---|
| `/agents` | 36 | 3 | `dom` | ok |
| `/path/1` | 36 | 3 | `dom` | ok |
| `/table` | 10 | 1 | `dom` | watch |
| `/embedded` | 10 | 1 | `embedded` | ok |
| `/irregular` | 5 | 1 | `dom` | watch |

`watch` in the table/irregular/sitemap rows is the expected field-coverage warning
(for example, the demo table publishes no profile URL and the sitemap-only profiles
hide email behind a button). It is not a crash.

---

## 3. What this run does **not** prove

The sandbox has no outbound network (external hosts return HTTP 403 through the
proxy), no Playwright/Chromium and no Streamlit. Therefore:

- no third-party real-estate or business directory was contacted;
- no real browser executed a live site's JavaScript;
- the Streamlit custom component was not rendered in a real Streamlit server;
- no live sitemap, anti-bot response, login wall or paywall was tested;
- Excel was generated and read back with openpyxl, not opened in Microsoft Excel.

This is stated deliberately. Live acceptance commands are in
`docs/LIVE_ACCEPTANCE.md`; run them on your machine and keep the JSON evidence file
and exports for each target.

---

## 4. Quick local re-run

```bash
python -m unittest discover -s tests -t .
python scripts/serve_demo.py --port 8765
# second terminal
python scripts/verify_live.py http://127.0.0.1:8765/agents --allow-local
python scripts/verify_live.py http://127.0.0.1:8765/path/1 --allow-local --engine static
```
