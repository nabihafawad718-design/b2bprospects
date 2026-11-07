# Installation and launch

## Requirements

- Python 3.10 or newer
- Windows 10/11, macOS, or Linux
- Internet access for real sites
- ~400 MB free for Chromium (browser mode)

The Python dependencies are in `requirements.txt`. Playwright is included there;
the browser binary is installed separately.

---

## Windows

```bat
cd b2bprospects-v4
py -m venv .venv
.venv\Scripts\activate
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m playwright install chromium
py -m streamlit run app.py
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## macOS / Linux

```bash
cd b2bprospects-v4
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
streamlit run app.py
```

On a fresh Linux server, Chromium may also need system libraries:

```bash
python -m playwright install-deps chromium
```

The browser is only needed for Browser mode. Automatic mode works without it; the
app displays install help if Playwright is missing.

---

## Launch

```bash
streamlit run app.py
```

The app opens in your browser. The sidebar has four workspaces:

- **Directory Extraction** — the main URL → analyse → map → extract → clean → export flow
- **Local Discovery** — the original OpenStreetMap pipeline
- **Adapter Health** — baselines, drift, supported/config adapters
- **Run History** — audit trail

## Verify the installation without a third-party site

Terminal 1:

```bash
python scripts/serve_demo.py --port 8765
```

Terminal 2:

```powershell
# PowerShell
$env:B2B_ALLOW_LOCAL="true"; streamlit run app.py
```

```bat
:: cmd
set B2B_ALLOW_LOCAL=true && streamlit run app.py
```

```bash
# macOS / Linux
B2B_ALLOW_LOCAL=true streamlit run app.py
```

In the dashboard, enter `http://127.0.0.1:8765/agents`, choose **Browser**, and run
the steps. The demo has 36 agents over three pages and a site-specific JSON adapter.
It also serves `/path/1`, `/table`, `/embedded`, `/irregular` and `/sitemap-only`
to exercise different real-world layouts.

Or verify headlessly:

```bash
python scripts/extract_cli.py http://127.0.0.1:8765/agents --allow-local --max-pages 3
python scripts/verify_live.py http://127.0.0.1:8765/agents --allow-local
python scripts/verify_live.py http://127.0.0.1:8765/path/1 --allow-local --engine static
```

## Run the tests

```bash
python -m unittest discover -s tests -t .
```

Expected: 163 tests, OK.

## First live run

```bash
python scripts/assess_site.py https://your-permitted-directory.example/agents
python scripts/verify_live.py https://your-permitted-directory.example/agents --max-pages 2 --max-records 10
```

Read `OPERATOR_GUIDE.md` before delivering data, `docs/V4_CHANGES.md` for the
implementation record, and `docs/LIVE_ACCEPTANCE.md` for the per-target evidence
procedure.

## Common setup problems

| Problem | Fix |
|---|---|
| `streamlit: command not found` | Activate the venv, or use `python -m streamlit run app.py` |
| Playwright installed but browser missing | `python -m playwright install chromium` |
| Linux browser launch fails | `python -m playwright install-deps chromium` |
| Local demo refused | Start the app with `B2B_ALLOW_LOCAL=true` |
| Excel download disabled | `pip install openpyxl` |
| Data directory not writable | Set `B2B_DATA_DIR` to a writable folder |
| Corporate proxy blocks sites | Configure `HTTPS_PROXY`; the app uses `requests` and Chromium networking |
