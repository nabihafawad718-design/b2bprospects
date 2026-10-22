# B2BProspects — OpenStreetMap Prototype

This is a **local test prototype**, not a production-ready paid product. It keeps the existing Streamlit layout and filters but switches the active search to public OpenStreetMap/Nominatim/Overpass data so the workflow can be tested without a paid API key.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## What it can do

- Search OpenStreetMap records by company name plus a city/region.
- Search a small set of mapped categories (plumbers, roofers, dentists, restaurants, cafes, real estate agents, lawyers, electricians, accountants, doctors, veterinarians, auto repair, salons, hotels).
- Show publicly mapped name, category, website, phone, email, address, a map link, and an OpenStreetMap source link when those fields exist.
- Filter results and export CSV/Excel for evaluation.

## Important limitations

- OpenStreetMap is a community-mapped dataset, **not a complete company database**. Coverage varies greatly by location and category. Missing fields do not mean a business lacks that information.
- The search uses a radius of about 25 km from the geocoded place and caps a query at 100 results.
- Decision-maker names are not provided by this prototype.
- Unknown industry terms fall back to matching business names and may miss relevant businesses.
- Public Nominatim and Overpass services have usage policies and shared capacity limits. This small prototype must not be used for bulk harvesting or high-volume commercial requests. For production, choose a data provider whose license and capacity permit the intended use.
- **Payments are not connected.** Exports are enabled only to test usefulness. Do not charge users or deploy this as a paid service yet.
- The previous SearXNG implementation remains in the code as `fetch_leads_legacy` for reference but is not used by the prototype UI.

## Before any production launch

1. Validate results manually across several target markets and customer tasks.
2. Check the current Nominatim and Overpass usage policies and the OpenStreetMap attribution requirements.
3. Choose a permitted, reliable data source for the volume and fields you plan to sell.
4. Implement real payment processing and server-side entitlement checks; do not rely on a hard-coded license key in client code.
5. Add monitoring, rate limits, error handling, and privacy/terms disclosures.
