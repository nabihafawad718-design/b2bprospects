# Live acceptance on your machine

The build environment has no outbound network and no browser/Streamlit runtime, so no
third-party directory was tested there. This page is the exact procedure to close that
gap. Run it once on the demo, then on each permitted real target.

Record the evidence. A target is not “supported” until a real run produced records and
the exports were opened.

---

## 1. Install

### Windows (PowerShell)

```powershell
cd b2bprospects-v4
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m playwright install chromium
```

### macOS / Linux

```bash
cd b2bprospects-v4
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
```

If Chromium reports missing system libraries on Linux:

```bash
python -m playwright install-deps chromium
```

### Check the runtime

```bash
python -c "import streamlit, playwright, bs4, lxml, openpyxl; print('runtime ok')"
python -m playwright install --dry-run chromium
```

---

## 2. Run the test suite

```bash
python -m unittest discover -s tests -t .
```

Expected: `Ran 163 tests ... OK`. Any failure is a real regression; do not proceed to
customer work until it is understood.

---

## 3. Demo acceptance (safe, repeatable)

Terminal 1:

```bash
python scripts/serve_demo.py --port 8765
```

Terminal 2 — Windows:

```powershell
$env:B2B_ALLOW_LOCAL="true"; streamlit run app.py
```

Terminal 2 — macOS/Linux:

```bash
B2B_ALLOW_LOCAL=true streamlit run app.py
```

Open `http://127.0.0.1:8765/agents` in the dashboard, choose **Browser**, and:

1. **Open and analyse**. Expect 12 rows and `next_button` pagination.
2. In the visual picker choose **Agent name**, click `Jane Smith`. The card and its
   siblings should highlight; the mapping should update.
3. Confirm the preview shows 12 records with name, phone, email, office and address.
4. Extract 3 pages. Expect 36 records, a method-attempt table and an `ok` quality level.
5. Clean/filter, then download CSV and Excel. Open both in the customer software you
   use. Check the Run Summary and Issues sheet.
6. Repeat Browser analysis on the layout variants:

| URL | Expected |
|---|---|
| `http://127.0.0.1:8765/path/1` | 36 records over 3 path-pages |
| `http://127.0.0.1:8765/table` | 10 records; first/last joined; Title extra |
| `http://127.0.0.1:8765/embedded` | 10 records from `__NEXT_DATA__`, no DOM cards |
| `http://127.0.0.1:8765/irregular` | 5 records from link clusters |
| `http://127.0.0.1:8765/sitemap-only` | records discovered from `/sitemap.xml` |

Scripted evidence:

```bash
python scripts/verify_live.py http://127.0.0.1:8765/agents --allow-local \
  --max-pages 3 --max-records 40 --json-out demo-browser.json

python scripts/verify_live.py http://127.0.0.1:8765/path/1 --allow-local \
  --engine static --max-pages 3 --max-records 40 --json-out demo-static.json
```

The script prints every method attempt and writes CSV/Excel to `data/exports/`.

---

## 4. Real target acceptance

Only use a directory you are permitted to collect from. For each target:

1. **Assess before extracting**

   ```bash
   python scripts/assess_site.py https://permitted-directory.example/agents
   ```

   Record: HTTP status, robots detail, JSON-LD, profile links, detected layers,
   pagination type, embedded record count, sitemap result, first sample row.

2. **Inspect in the browser**

   ```bash
   python scripts/verify_live.py https://permitted-directory.example/agents \
     --inspect-only --json-out target-inspect.json
   ```

   If Chromium is unavailable, use `--engine static --inspect-only`.

3. **Small extraction**

   ```bash
   python scripts/verify_live.py https://permitted-directory.example/agents \
     --max-pages 2 --max-records 10 --enrich --json-out target-run.json
   ```

   If the browser fails but the server HTML has records, try:

   ```bash
   python scripts/verify_live.py https://permitted-directory.example/agents \
     --engine static --max-pages 2 --max-records 10 --json-out target-static.json
   ```

4. **Verify by hand.** Open the source page and compare five records on: name, phone,
   email, office, city/state, profile URL. Check that a blank field is genuinely
   blank on the site, not a mapping miss.

5. **Open the exports.** CSV in a text editor, Excel in the customer's spreadsheet
   software. Check encoding, leading zeros in postal codes, phone formatting, the Run
   Summary and the Issues sheet.

6. **Record the evidence.** Keep `target-run.json`, the CSV/XLSX and the exact URL and
   date. A run with no records is a valid negative result; do not present it as
   success.

---

## 5. Interpreting the diagnosis

| Failure code | Meaning | What to do |
|---|---|---|
| `blocked` | HTTP 401/403/429 or anti-bot challenge | Stop. Do not retry in a loop. Ask for permission or use another source. |
| `javascript_only` | Server HTML is an empty shell | Run Browser mode; if that fails, the data is behind an authenticated API. |
| `not_a_listing` | No repeated records or profile links | Paste the roster URL, not a profile/search page. |
| `single_profile` | One Person profile found | Use the roster URL. |
| `mapping_empty` / `no_records` | Structure found but mapping produced nothing | Re-analyse; map the name column; try another detected block. |
| `pagination_stalled` / `partial` | A page did not change or produced no new records | Check the pager manually; lower the page cap; try another navigation style. |
| `server_error` | HTTP 5xx | Try later; do not change the mapping because of it. |

If the method-attempt table shows a layer was `empty`, the engine tried the next one.
If every layer is empty, the target is not extractable with the current adapter; add a
site adapter rather than claiming success.

---

## 6. Multi-target comparison

Test more than one website when possible. A useful evidence sheet:

| Target | URL | Status | Layer used | Records | Named | Contact | Pages | Quality | Notes |
|---|---|---|---|---|---|---|---|---|---|
| Demo brokerage | `/agents` | 200 | DOM | 36 | 36 | 36 | 3 | ok | baseline |
| Demo path pages | `/path/1` | 200 | listing cards | 36 | 36 | 36 | 3 | ok | no next link |
| Demo table | `/table` | 200 | listing cards | 10 | 10 | 10 | 1 | watch | no profile URLs |
| Real target 1 |  |  |  |  |  |  |  |  |  |
| Real target 2 |  |  |  |  |  |  |  |  |  |

For each real target also record: terms/permission decision, robots.txt note, whether a
login/CAPTCHA/paywall was encountered (then stop), and the collection timestamp.

---

## 7. What not to do

- Do not retry a 403/429 or challenge page in a loop.
- Do not add stealth patches, CAPTCHA solving, proxy rotation or login automation.
- Do not describe a target as supported without a real run and evidence.
- Do not describe contact details as verified unless you verified them individually.
- Do not treat robots.txt as permission or as the only legal question.

See `OPERATOR_GUIDE.md` for the delivery checklist.
