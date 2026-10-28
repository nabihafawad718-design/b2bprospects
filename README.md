# B2BProspects — contact-enrichment test prototype

This is a **separate experimental copy** of your Streamlit app. The existing UI/layout, search fields, filters, table, and export flow are retained. It does not change or deploy the live app.

## What changed

- Keeps OpenStreetMap/Nominatim/Overpass as the business-discovery source.
- After discovery, attempts a **small, bounded best-effort enrichment pass** on up to 20 businesses that have a website but are missing phone and/or email.
- Reads the homepage and at most one clearly labelled same-domain Contact/About page, looking only for contact details visible in returned public HTML.
- Skips social-media sites, uses request timeouts, and continues when sites fail.

## Important limitations

- This is not Bing integration and does not solve OSM's limited business coverage. This is the first testable enrichment step in the agreed source-comparison approach.
- Contact extraction is heuristic and can return false positives; verify data before using it. It cannot reveal data that a site does not publish in the HTML, and it does not bypass logins, CAPTCHAs, paywalls, or access controls.
- The enrichment pass is capped at 20 sites per search to limit load and latency. Some websites block automated requests, use JavaScript to render contacts, or prohibit automated access. Respect each site's terms and applicable law.
- Public OSM services have shared capacity limits and usage policies. Do not use this prototype for bulk harvesting or high-volume commercial requests.
- The app's existing test license key is not production security, and the $49 copy in the UI is not connected to payment processing. Do not charge users or deploy this as a paid service based on this prototype.

## Run on Windows

1. Extract this ZIP into a new folder (keep your original project as a backup).
2. Open Command Prompt in the extracted `b2bprospects-main` folder.
3. Run:

```bat
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements.txt
py -m streamlit run app.py
```

## Test plan

Try one city + one niche and inspect 10–20 results. Compare before/after Email and Phone Number coverage, verify a sample manually, and note elapsed time/errors. The UI is unchanged; results may take longer because a limited number of public websites are checked.
