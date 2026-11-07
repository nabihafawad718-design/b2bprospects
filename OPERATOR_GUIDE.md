# Operator Guide

How to run this tool as a one-person business, from first inspection to a delivered
file. Written for the person who will actually use it, not for a developer.

---

## 0. First-run acceptance check (do this once, on your own machine)

The engine is tested offline against realistic fixtures. It could not be run against
the live web from the build environment, so the browser itself is unverified until you
do this. It takes ten minutes.

1. `streamlit run app.py` — the page loads with the same visual design as before.
2. **Local Discovery** → search `plumbers` / `Houston, Texas`. Records appear, filters
   work. This proves the original pipeline still runs.
3. Install the browser once: `python -m playwright install chromium`.
4. **Directory Extraction** → paste a directory URL you are permitted to use →
   select **Browser** → **Open and analyse**.
5. Confirm the "What was found" numbers look plausible: records on page, pagination
   style. If it says zero rows, that layout needs an adapter (see §4).
6. Check the field mapping against the preview table. Change any column that is wrong.
7. Extract with **Maximum pages = 2**. Confirm records appear, and download the Excel.
8. Open the workbook's **Run Summary** sheet. Confirm source URL, timestamp, coverage.
9. **Adapter Health** shows the site. Run again and confirm it is compared to baseline.

For a scripted version of steps 4–7:

```bash
python scripts/verify_live.py https://permitted-directory.example/agents --max-pages 2 --max-records 10
```

It prints PASS/FAIL per check and writes the exports to `data/exports/`.

---

## 1. Taking on a customer project

**Before you start**

- Get the target list in writing: which brokerages, which cities, which fields.
- Confirm you have permission to collect from each source. A customer saying "get me
  realtors" is not permission to breach a site's terms.
- Run the assessment and sign off on its checklist:
  `python scripts/assess_site.py https://example.com/agents`
- Agree what "complete" means: a brokerage with 400 agents must not silently deliver
  120.

**During**

1. Analyse the page and record the row count — that is your expectation for the run.
2. Extract with a small cap first (one or two pages, ten records). Check the sample by
   hand against the live site: name, phone, email, city.
3. If the sample is wrong, fix the mapping or stop. Do not scale up a wrong mapping.
4. Run the full extraction.
5. Compare the record count against rows-per-page × pages. A large gap means
   pagination stopped early — the run says why.

**Before you deliver**

- **Adapter Health** must be green, or you must be able to explain the deviation.
- Read every warning on the run. Each one is a specific claim about what is missing.
- Spot-check five records against the source pages yourself.
- Confirm `Source URL` and `Collected (UTC)` are present on every row.
- Do not describe data as "verified" unless you actually verified it.

**What to send**

The `.xlsx`. It carries the Run Summary sheet: source, timestamp, method, field
coverage, warnings and the data statement. That sheet protects you when a customer
asks why some rows lack an email.

---

## 2. Choosing the extraction mode

| Situation | Mode |
|---|---|
| Automatic inspection finds profile links and rows | **Automatic** — faster, no browser |
| Automatic says "JavaScript" or "no profile links" | **Browser** |
| Page uses load-more, infinite scroll, or JS pagination | **Browser** |
| Site publishes `__NEXT_DATA__` but renders little HTML | **Browser** (it uses the payload first) |
| Browser is not installed | Automatic only; install Chromium to unlock the rest |

You do not have to guess: run Automatic first. It tells you when a page needs a browser.

## 3. Reading the quality signals

| Signal | Means | Action |
|---|---|---|
| Success rate < 70% | Profiles were read but produced no name | Name column mapped wrong, or the layout changed |
| A field at 0% | That field isn't on the page, or the rule broke | Check one record by hand |
| "X% of records have no phone or email" | Contact extraction underperforming | Try profile enrichment, or accept the gap |
| "did not change after the pagination action" | Last page, or the button is disabled | Confirm the page count manually |
| "no new records" | Pagination returned duplicates | Check the site's paging |
| Markup signature changed | Listing container or row markup changed | Re-analyse; expect to update the mapping |
| Record count down > 50% | Assume broken | Do not deliver; fix first |

## 4. When a layout is not detected

Order of attack:

1. **Check you gave it the listing page**, not a profile, a search form or the home page.
2. **Try Browser mode.** Most "no records" results on real sites are JavaScript.
3. **Look for an embedded payload.** `assess_site.py` lists payloads such as
   `__NEXT_DATA__`. If one exists, Browser mode uses it automatically.
4. **Try a different listing URL** — many brokerages have an `?page=` roster or a
   sitemap that renders differently.
5. **Write a dedicated adapter** (§5) when the layout is stable and worth the hour.

## 5. Writing a site adapter (the common maintenance job)

1. Copy `core/adapters/custom_site.py` to `core/adapters/<site>.py`.
2. Rename the class; set `id`, `label`, `domains = ("example.com",)`.
3. In the browser, inspect one roster row. Put the row selector in `ROW_SELECTOR` and
   the profile anchor in `ROW_LINK_SELECTOR`.
4. Inspect one profile. Fill `NAME_SELECTOR`, `PHONE_SELECTOR`, `EMAIL_SELECTOR`,
   `ADDRESS_SELECTOR`. Delete the ones the site does not have.
5. Register it in `core/adapters/registry.py`:
   ```python
   from .acme import AcmeAdapter
   SITE_ADAPTERS = [AcmeAdapter(), CustomSiteAdapter()]
   ```
6. Run Automatic mode with a cap of 10. Check the records. Then let health baseline form.

You never touch the engine, validation, export or UI.

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "The browser engine is not installed" | Playwright missing | `pip install playwright` then `python -m playwright install chromium` |
| "Executable doesn't exist" | Browser binary missing | `python -m playwright install chromium` |
| "robots.txt disallows" | Path not permitted | Use a permitted page or get written permission |
| "Site refused access (HTTP 403)" | Bot protection or terms | Stop. Use a permitted source. |
| "anti-bot challenge" | Cloudflare/CAPTCHA page | Not bypassed by design. Use another source. |
| "No repeating record structure" | Not a listing page, or a layout we don't recognise | See §4 |
| "Nothing matched this mapping" | Wrong column chosen for the name | Change the mapping; check the preview |
| Extraction is slow | Browser + profile enrichment | Lower "Profiles to read" or turn enrichment off |
| Excel button disabled with a message | `openpyxl` missing | `pip install openpyxl` |
| History not appearing | `data/` not writable | Check permissions or set `B2B_DATA_DIR` |

## 7. Legal checklist before each delivery

- The pages were publicly accessible without a login.
- robots.txt permitted the path (the tool tells you if it did not).
- You did not bypass a CAPTCHA, paywall or access control.
- You collected business/professional contact details, not private personal data.
- You can name the source URL and collection date for every row (the export does this).
- Your customer agreement states what was collected, when, and from where.
- If the customer is in the EU/UK, confirm the lawful basis with them; this tool does
  not make that determination.

Public accessibility alone does not establish permission for commercial collection or
redistribution. This guide is operational, not legal advice.

## 8. What the tool will not do

- Solve CAPTCHAs, defeat bot detection, or rotate proxies.
- Log in to gated directories.
- Guarantee that any given site is extractable. It reports what it found and stops
  honestly when it cannot.
- Verify an email address or phone number. Nothing is labelled "verified".
