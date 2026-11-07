# Operator Guide

How to run this tool as a one-person business, from first inspection to a delivered
file. Written for the person who will actually use it, not for a developer.

---

## 0. First-run acceptance check (do this once, on your own machine)

The build environment has no network, no Playwright and no Streamlit, so the browser
and the visual picker must be accepted on your machine. The demo server makes this
safe and repeatable.

1. Install and launch:
   ```bash
   pip install -r requirements.txt
   python -m playwright install chromium
   ```
2. Start the demo in one terminal: `python scripts/serve_demo.py --port 8765`
3. Start the dashboard in another:
   - PowerShell: `$env:B2B_ALLOW_LOCAL="true"; streamlit run app.py`
   - cmd: `set B2B_ALLOW_LOCAL=true && streamlit run app.py`
   - macOS/Linux: `B2B_ALLOW_LOCAL=true streamlit run app.py`
4. **Local Discovery** → search `plumbers` / `Houston, Texas`. Records appear and
   filters work. This proves the original pipeline still runs.
5. **Directory Extraction** → enter `http://127.0.0.1:8765/agents` → **Browser** →
   **Open and analyse**.
6. Confirm 12 rows and `next_button` pagination. Click **Agent name** in the visual
   picker field list, then click `Jane Smith` in the page snapshot. The card and its
   siblings should highlight, and the mapping should update.
7. Scroll down, open **Filters and cleaning**, and confirm the preview shows 12
   records. Add a custom column if you want (title or licence).
8. Extract with **Maximum pages = 3** and **Read profile pages** on for a small run.
   Confirm 36 records and download the Excel.
9. Open the workbook: **Records** has the data, **Run Summary** has source and
   coverage, **Issues** appears when any row is flagged.
10. **Adapter Health** shows the demo site and the configured adapters.

Scripted version:

```bash
python scripts/verify_live.py http://127.0.0.1:8765/agents --allow-local
```

Then repeat steps 5–9 against a real directory you are permitted to collect from.

---

## 1. Taking on a customer project

**Before you start**

- Get the target list in writing: which brokerages, cities and fields.
- Confirm permission to collect from each source. A customer asking for "realtors"
  is not permission to breach a site's terms.
- Run the assessment and read its checklist:
  `python scripts/assess_site.py https://example.com/agents`
- Agree what "complete" means. A brokerage with 400 agents must not silently deliver
  120.

**During**

1. Analyse the page and record the row count. That is your expectation for the run.
2. Extract a small sample first (one or two pages, ten records). Check name, phone,
   email, city and one extra column by hand against the live page.
3. If the sample is wrong, fix the mapping or stop. Do not scale a wrong mapping.
4. Run the full extraction.
5. Compare the record count with rows-per-page × pages. A large gap means pagination
   stopped early; the run says why.

**Before you deliver**

- **Adapter Health** must be green, or you must explain the deviation.
- Read every warning. Each one is a specific claim about what is missing.
- Open **Review issues** and decide whether to filter, fix or explain each gap.
- Spot-check five records against the source pages yourself.
- Confirm every row has `Source URL` and `Collected (UTC)`.
- Do not describe data as "verified" unless you actually verified it.

**What to send**

The `.xlsx`. It carries the Run Summary sheet (source, timestamp, method, coverage,
filters, warnings, data statement) and an Issues sheet whenever a row needs review.
That protects you when a customer asks why some rows lack an email.

---

## 2. Choosing the extraction mode

| Situation | Mode |
|---|---|
| Automatic finds profile links or rich listing cards | **Automatic** — faster, no browser |
| Automatic says "JavaScript" or "no profile links" | **Browser** |
| Load-more, infinite scroll, or JS pagination | **Browser** |
| Site publishes `__NEXT_DATA__` / Next.js flight data | **Browser** (uses payload first) |
| Browser is not installed | Automatic only; install Chromium to unlock the rest |

Run Automatic first. It tells you when a page needs a browser. The Browser workspace
also handles pages with no DOM cards but a usable embedded payload; it offers a
dedicated "Extract embedded records" action.

## 3. Mapping fields without selectors

The mapping is a column model, not CSS.

- **Visual picker.** Choose a field, then click one example value in the page
  snapshot. The picker finds the repeating row and sends back the element's position
  path. Python maps that path to the detected column. The status line reports how many
  similar rows it found.
- **Column dropdowns.** Each field lists detected columns with example values. The
  automatic guess is a guess; check the preview and change anything wrong.
- **Extra columns.** In the mapping card, open "Add another detected column as a
  custom field", choose a column, and name it (Title, Licence Number, MLS ID,
  Languages…). It is exported like any other field. Visually picked custom fields
  appear automatically.
- **Preview.** The preview uses the same mapping as the full run. A wrong guess is
  visible before you spend time extracting.

If the visual component does not appear, the dropdowns are fully functional. The
picker is an accelerator, not a requirement.

## 4. Cleaning and review

Everything in **Step 6** works on a copy of the run. The original extraction remains
intact.

- **Search** across every field, including extras.
- **Filters**: named only, has phone, has email, any contact, city, state, flagged
  rows, and rows missing a selected field.
- **Duplicate handling**: smart (URL → name+phone → name+firm+city), URL only,
  name+phone, name+firm+city, email, phone, name only, or none. The result count is
  shown before export.
- **Sort** by name, city, brokerage, state or email.
- **Review issues**: rows with no name, no contact, invalid email, missing location or
  another quality flag. Issues are included in the export unless you filter them out.
- **Columns to export**: choose exactly what the customer receives. Extra columns are
  listed here.
- **Export**: CSV, Excel and JSON. Excel includes the Issues sheet when needed.

Phones are normalized to a consistent display, emails are lowercased and validated,
markup and whitespace are stripped, and deduplication never merges two different
people just because they share an office phone.

## 5. Reading the quality signals

| Signal | Means | Action |
|---|---|---|
| Success rate < 70% | Profiles were read but produced no name | Name column mapped wrong, or layout changed |
| A field at 0% | Field isn't on the page, or the rule broke | Check one record by hand |
| "X% of records have no phone or email" | Contact extraction underperforming | Try profile enrichment, or accept and explain the gap |
| "did not change after the pagination action" | Last page, or disabled control | Confirm the page count manually |
| "no new records" | Pagination returned duplicates | Check the site's paging |
| Markup signature changed | Listing container or row markup changed | Re-analyse; expect to update the mapping |
| Record count down > 50% | Assume broken | Do not deliver; fix first |
| "Records came from the embedded payload" | JSON was the source | Verify a few values against the page |
| "robots.txt note" | Path is disallowed but policy is warn | Review the site's terms and permission |

## 6. When a layout is not detected

Order of attack:

1. **Check the URL** — the roster page, not a profile, search form or home page.
2. **Try Browser mode.** Most "no records" results on real sites are JavaScript.
3. **Look for an embedded payload.** `assess_site.py` lists payloads such as
   `__NEXT_DATA__`, `next-flight`, `__INITIAL_STATE__`. Browser mode uses them
   automatically, and offers an embedded-only extraction action.
4. **Try a different listing URL** — many brokerages have an `?page=` roster or a
   sitemap that renders differently.
5. **Add a JSON adapter** (§7) when the selectors are stable. This is the usual
   maintenance path and needs no Python.
6. **Write a Python adapter** only for structures that selectors cannot express.

## 7. Adding a site adapter without code

1. Copy `core/adapters/sites/example.json` to `core/adapters/sites/<site>.json`.
2. Set `id`, `label`, and `domains` (the host, without `www`).
3. On the roster, identify the repeating row and the profile link:
   `listing.row`, `listing.profile_link`.
4. Identify pagination: `listing.next` or `listing.page_param`.
5. On a profile, map the fields:
   `profile.fields` with `css`, `css@attr`, or `a || b`.
6. If the roster already contains the fields, add `listing.record_fields`; the engine
   then avoids opening every profile.
7. Restart the app. **Adapter Health → Supported adapters** lists the new config and
   reports selector errors.

Any field name outside the standard catalogue becomes an extra exported column.
See `core/adapters/sites/README.md` for syntax and examples.

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "The browser engine is not installed" | Playwright missing | `pip install playwright` then `python -m playwright install chromium` |
| "Executable doesn't exist" | Browser binary missing | `python -m playwright install chromium` |
| Visual picker does not appear | Component not loaded / snapshot too large | Use the column dropdowns; or raise `B2B_MAX_SNAPSHOT_BYTES` |
| "robots.txt enforcement is on" | `B2B_ROBOTS_POLICY=enforce` | Set `warn` to review instead of block; decide on permission separately |
| "Site refused access (HTTP 403)" | Bot protection or terms | Stop. Use a permitted source. |
| "anti-bot challenge" | Cloudflare/CAPTCHA page | Not bypassed by design. Use another source. |
| "No repeating record structure" | Not a listing page, or unfamiliar layout | See §6 |
| "Nothing matched this mapping" | Wrong column chosen for the name | Change the mapping; check preview |
| Duplicate count looks too high | Aggressive dedupe strategy | Switch to "Profile URL only" and re-check |
| Extraction is slow | Browser + profile enrichment | Lower "Profiles to read", turn enrichment off, or use listing records |
| Excel button disabled with a message | `openpyxl` missing | `pip install openpyxl` |
| History not appearing | `data/` not writable | Check permissions or set `B2B_DATA_DIR` |
| Local demo refused | Loopback blocked | Start with `B2B_ALLOW_LOCAL=true` |

## 9. Legal checklist before each delivery

- The pages were publicly accessible without a login.
- You reviewed the robots.txt notice. If it disallowed the path, you made a separate
  decision about permission rather than treating the tool's warn policy as consent.
- You did not bypass a CAPTCHA, paywall or access control.
- You collected business/professional contact details, not private personal data.
- You can name the source URL and collection date for every row.
- Your customer agreement states what was collected, when, and from where.
- For EU/UK customers, confirm the lawful basis with them; this tool does not make
  that determination.

Public accessibility alone does not establish permission for commercial collection or
redistribution. This guide is operational, not legal advice.

## 10. What the tool will not do

- Solve CAPTCHAs, defeat bot detection, or rotate proxies.
- Log in to gated directories.
- Guarantee that any given site is extractable. It reports what it found and stops
  honestly when it cannot.
- Verify an email address or phone number. Nothing is labelled "verified".
- Decide whether you have permission to collect or redistribute a source.

## 11. Testing status

`python -m unittest discover -s tests -t .` runs 139 tests, including real HTTP
extraction against the bundled demo for both the static and browser engines. The
browser's actual Chromium rendering, the Streamlit visual component, and any
third-party live site still need acceptance on your machine. See
`docs/V3_CHANGES.md` for the exact list.
