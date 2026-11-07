"""OpenStreetMap discovery mode — the original data pipeline, preserved.

This is the existing behaviour of the app: geocode a city with Nominatim, query
Overpass for a business category, parse the results and best-effort enrich contact
details from each business's own public website.

Changes from the original are deliberately narrow and all on the safety side:
scripts and comments are stripped before contact regexes run, and website fetches
now go through the shared robots-aware fetcher.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
HTTP_HEADERS = {
    "User-Agent": "B2BProspects/1.0 (https://b2bprospects.io; lead research tool)",
    "Accept": "application/json",
}


class LocationNotFound(Exception):
    """Nominatim could not resolve the target location."""


class GeocodeError(Exception):
    """Nominatim was unreachable or returned an unusable response."""


class OverpassError(Exception):
    """All Overpass endpoints failed, timed out or were rate limited."""


# Keyword aliases -> OSM tag filters. Aliases are matched as whole words after light stemming.
CATEGORY_RULES: list[tuple[tuple[str, ...], list[tuple[str, str]]]] = [
    (("plumber", "plumbing"), [("craft", "plumber")]),
    (("electrician", "electrical"), [("craft", "electrician")]),
    (("roofer", "roofing", "roof"), [("craft", "roofer")]),
    (("hvac", "heating", "air conditioning", "ac repair"), [("craft", "hvac")]),
    (("carpenter", "carpentry"), [("craft", "carpenter")]),
    (("painter", "painting"), [("craft", "painter")]),
    (("builder", "contractor", "general contractor", "construction"),
     [("craft", "builder"), ("office", "construction_company")]),
    (("landscaper", "landscaping", "gardener", "lawn care"), [("craft", "gardener")]),
    (("locksmith",), [("craft", "locksmith"), ("shop", "locksmith")]),
    (("photographer", "photography"), [("craft", "photographer"), ("shop", "photo")]),
    (("dentist", "dental"), [("amenity", "dentist"), ("healthcare", "dentist")]),
    (("doctor", "physician", "clinic"),
     [("amenity", "doctors"), ("amenity", "clinic"), ("healthcare", "doctor")]),
    (("pharmacy", "drugstore"), [("amenity", "pharmacy")]),
    (("vet", "veterinarian", "veterinary"), [("amenity", "veterinary")]),
    (("chiropractor",), [("healthcare", "chiropractor")]),
    (("physiotherapist", "physio", "physical therapy"), [("healthcare", "physiotherapist")]),
    (("restaurant", "dining"), [("amenity", "restaurant")]),
    (("cafe", "coffee shop", "coffee"), [("amenity", "cafe")]),
    (("bar", "pub"), [("amenity", "bar"), ("amenity", "pub")]),
    (("fast food", "takeaway"), [("amenity", "fast_food")]),
    (("bakery",), [("shop", "bakery")]),
    (("hair salon", "hairdresser", "barber", "salon"), [("shop", "hairdresser")]),
    (("beauty", "spa", "nail"), [("shop", "beauty")]),
    (("gym", "fitness"), [("leisure", "fitness_centre")]),
    (("car repair", "auto repair", "mechanic", "garage"), [("shop", "car_repair")]),
    (("car dealer", "auto dealer", "car dealership"), [("shop", "car")]),
    (("florist",), [("shop", "florist")]),
    (("lawyer", "attorney", "law firm"), [("office", "lawyer")]),
    (("accountant", "accounting", "cpa"), [("office", "accountant")]),
    (("real estate", "realtor", "estate agent"), [("office", "estate_agent")]),
    (("insurance",), [("office", "insurance")]),
    (("hotel", "motel"), [("tourism", "hotel"), ("tourism", "motel")]),
    (("school",), [("amenity", "school")]),
    (("daycare", "kindergarten", "childcare"), [("amenity", "kindergarten"), ("amenity", "childcare")]),
    (("bank",), [("amenity", "bank")]),
    (("supermarket", "grocery"), [("shop", "supermarket"), ("shop", "convenience")]),
    (("clothing", "boutique", "fashion"), [("shop", "clothes")]),
    (("jeweler", "jewelry"), [("shop", "jewelry")]),
    (("furniture",), [("shop", "furniture")]),
    (("laundry", "dry cleaner", "dry cleaning"), [("shop", "laundry"), ("shop", "dry_cleaning")]),
    (("pet shop", "pet store"), [("shop", "pet")]),
    (("optician", "eyewear"), [("shop", "optician")]),
    (("travel agency", "travel agent"), [("shop", "travel_agency")]),
    (("marketing agency", "advertising", "web design", "digital agency"),
     [("office", "advertising_agency"), ("office", "it")]),
    (("tailor", "tailoring"), [("craft", "tailor")]),
    (("printing", "print shop"), [("craft", "printer"), ("shop", "copyshop")]),
    (("sign maker", "signage"), [("craft", "sign_maker")]),
    (("mobile shop", "phone repair"), [("shop", "mobile_phone"), ("craft", "electronics_repair")]),
    (("software", "it service", "it company"), [("office", "it")]),
]

CATEGORY_KEYS = ("amenity", "shop", "craft", "office", "healthcare", "leisure", "tourism")

SOCIAL_DOMAINS = (
    "facebook.com", "fb.com", "instagram.com", "linktr.ee", "yelp.com", "twitter.com", "x.com",
    "tiktok.com", "linkedin.com", "youtube.com", "wa.me", "business.site", "sites.google.com",
)


def sanitize_term(text: str) -> str:
    """Keep only characters that are safe inside an Overpass QL string / regex."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 \-]", "", text.lower())).strip()


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ses", "xes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _stem_phrase(text: str) -> str:
    return " ".join(_stem(w) for w in sanitize_term(text).split())


def tag_selectors(keyword: str) -> list[str]:
    """Translate a free-text industry into Overpass tag selectors. Every selector requires a name."""
    phrase = f" {_stem_phrase(keyword)} "
    pairs: list[tuple[str, str]] = []
    for aliases, tags in CATEGORY_RULES:
        if any(f" {_stem_phrase(a)} " in phrase for a in aliases):
            for pair in tags:
                if pair not in pairs:
                    pairs.append(pair)
    if pairs:
        return [f'["{k}"="{v}"]["name"]' for k, v in pairs]

    # Fallback for niches not in the table: match the term against the main OSM category tags.
    term = _stem_phrase(keyword)
    if not term:
        return []
    value_term = term.replace(" ", "_")
    return [f'["{k}"~"{value_term}",i]["name"]' for k in CATEGORY_KEYS]


def build_overpass_query(keyword: str, scope: dict, limit: int) -> str:
    selectors = tag_selectors(keyword)
    if not selectors:
        raise ValueError("Empty keyword")
    if scope["kind"] == "area":
        header = f"area(id:{int(scope['id'])})->.searchArea;\n"
        suffix = "(area.searchArea)"
    else:
        south, west, north, east = scope["bbox"]
        header = ""
        suffix = f"({south:.6f},{west:.6f},{north:.6f},{east:.6f})"
    body = "\n".join(f"  nwr{sel}{suffix};" for sel in selectors)
    return f"[out:json][timeout:45];\n{header}(\n{body}\n);\nout center tags {int(limit)};"


def geocode_location(location: str) -> dict:
    """Resolve a place name to an OSM area id and bounding box using free Nominatim."""
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": location, "format": "jsonv2", "limit": 5},
            headers=HTTP_HEADERS,
            timeout=20,
        )
    except requests.exceptions.RequestException as e:
        raise GeocodeError(str(e)) from e
    if resp.status_code != 200:
        raise GeocodeError(f"Nominatim returned HTTP {resp.status_code}")
    try:
        results = resp.json()
    except ValueError as e:
        raise GeocodeError("Nominatim returned an unreadable response") from e
    if not results:
        raise LocationNotFound(location)

    ranked = sorted(results, key=lambda r: {"relation": 0, "way": 1}.get(r.get("osm_type"), 2))
    best = ranked[0]

    bbox = None
    raw_bb = best.get("boundingbox")
    if raw_bb and len(raw_bb) == 4:
        south, north, west, east = (float(x) for x in raw_bb)
        bbox = (south, west, north, east)

    area_id = None
    if best.get("osm_type") == "relation":
        area_id = 3600000000 + int(best["osm_id"])
    elif best.get("osm_type") == "way":
        area_id = 2400000000 + int(best["osm_id"])

    if area_id is None and bbox is None:
        raise LocationNotFound(location)

    display = ", ".join(p.strip() for p in str(best.get("display_name", location)).split(",")[:2])
    return {"name": display or location, "area_id": area_id, "bbox": bbox}


def run_overpass(query: str) -> list[dict]:
    """POST the query to public Overpass servers with retry and failover."""
    errors: list[str] = []
    for url in OVERPASS_ENDPOINTS:
        host = urlparse(url).hostname
        for attempt in range(2):
            try:
                resp = requests.post(url, data={"data": query}, headers=HTTP_HEADERS, timeout=(10, 60))
            except requests.exceptions.Timeout:
                errors.append(f"{host}: timed out")
                break
            except requests.exceptions.RequestException as e:
                errors.append(f"{host}: {e}")
                break

            if resp.status_code == 200:
                try:
                    payload = resp.json()
                except ValueError:
                    errors.append(f"{host}: unreadable data")
                    break
                elements = payload.get("elements", [])
                remark = str(payload.get("remark", ""))
                if "runtime error" in remark and not elements:
                    errors.append(f"{host}: query too heavy ({remark[:80]})")
                    break
                return elements

            if resp.status_code in (429, 502, 503, 504):
                errors.append(f"{host}: busy (HTTP {resp.status_code})")
                time.sleep(2 * (attempt + 1))
                continue

            errors.append(f"{host}: HTTP {resp.status_code}")
            break
    raise OverpassError(" | ".join(errors) or "unknown error")


def _first_value(tags: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = tags.get(key)
        if isinstance(value, str) and value.strip():
            return value.split(";")[0].strip()
    return None


def clean_website(raw: str | None) -> str | None:
    if not raw:
        return None
    url = raw.strip()
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    return url


def build_address(tags: dict) -> str | None:
    full = tags.get("addr:full")
    if isinstance(full, str) and full.strip():
        return full.strip()
    street = " ".join(p.strip() for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p and p.strip())
    city = (tags.get("addr:city") or tags.get("addr:suburb") or tags.get("addr:town") or "").strip()
    region = " ".join(p.strip() for p in (tags.get("addr:state"), tags.get("addr:postcode")) if p and p.strip())
    parts = [p for p in (street, city, region) if p]
    return ", ".join(parts) if parts else None


def category_label(tags: dict) -> str | None:
    for key in CATEGORY_KEYS:
        value = tags.get(key)
        if isinstance(value, str) and value.strip():
            return value.replace("_", " ").title()
    return None


def parse_elements(elements: list[dict]) -> pd.DataFrame:
    rows = []
    for el in elements:
        tags = el.get("tags") or {}
        name = tags.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        email = _first_value(tags, ("email", "contact:email"))
        if email:
            email = email.replace("mailto:", "").strip().lower()
        el_type, el_id = el.get("type"), el.get("id")
        rows.append(
            {
                "Company Name": name.strip(),
                "Category": category_label(tags),
                "Website": clean_website(_first_value(tags, ("website", "contact:website", "url", "contact:url"))),
                "Phone Number": _first_value(tags, ("phone", "contact:phone", "mobile", "contact:mobile")),
                "Email": email or None,
                "Physical Address": build_address(tags),
                "Map": f"https://www.openstreetmap.org/{el_type}/{el_id}" if el_type and el_id else None,
            }
        )
    columns = ["Company Name", "Category", "Website", "Phone Number", "Email", "Physical Address", "Map"]
    if not rows:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(rows, columns=columns)
    return df.sort_values("Company Name", key=lambda s: s.str.lower()).reset_index(drop=True)


def _extract_public_contacts(page_html: str) -> tuple[str | None, str | None]:
    """Extract only contact details explicitly present in fetched public HTML."""
    emails = re.findall(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", page_html)
    phones = re.findall(r"(?<!\w)(?:\+?\d[\d().\s-]{7,}\d)(?!\w)", page_html)
    email = next((e.lower() for e in emails if not e.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))), None)
    phone = None
    for candidate in phones:
        digits = re.sub(r"\D", "", candidate)
        if 10 <= len(digits) <= 15:
            phone = re.sub(r"\s+", " ", candidate).strip(" .-")
            break
    return email, phone


def enrich_public_websites(df: pd.DataFrame, max_sites: int = 20, fetcher=None) -> pd.DataFrame:
    """Bounded, robots-aware pass to fill missing contact fields.

    Only public homepages and one clearly labelled same-domain Contact/About page
    are read. Access controls are never bypassed; refusals are simply skipped.
    """
    if df.empty or "Website" not in df.columns:
        return df

    from .net import FetchError, PoliteFetcher

    fetcher = fetcher or PoliteFetcher()
    result = df.copy()
    checked = 0

    for idx, row in result.iterrows():
        if checked >= max_sites:
            break
        website = row.get("Website")
        missing_email = not isinstance(row.get("Email"), str) or not str(row.get("Email", "")).strip()
        missing_phone = not isinstance(row.get("Phone Number"), str) or not str(row.get("Phone Number", "")).strip()
        if not isinstance(website, str) or not website.strip() or not (missing_email or missing_phone):
            continue
        if is_social_host(website):
            continue
        checked += 1

        try:
            page = fetcher.get_optional(website)
        except FetchError:
            continue
        if page is None or not page.is_html:
            continue

        html_text = strip_script_blocks(page.text)
        email, phone = _extract_public_contacts(html_text)

        if (missing_email and not email) or (missing_phone and not phone):
            links = re.findall(
                "(?is)<a\\b[^>]*href\\s*=\\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html_text
            )
            for href, label_html in links:
                label = re.sub(r"<[^>]+>", " ", label_html).lower()
                if any(term in (label + " " + href.lower()) for term in ("contact", "about", "impressum")):
                    target = urljoin(page.final_url, href)
                    if urlparse(target).hostname != urlparse(page.final_url).hostname:
                        continue
                    sub = fetcher.get_optional(target)
                    if sub is not None and sub.is_html:
                        e2, p2 = _extract_public_contacts(strip_script_blocks(sub.text))
                        email, phone = email or e2, phone or p2
                    break

        if missing_email and email:
            result.at[idx, "Email"] = email
        if missing_phone and phone:
            result.at[idx, "Phone Number"] = phone

    return result


def strip_script_blocks(page_html: str) -> str:
    """Remove script/style/template content before contact regexes run.

    Without this, analytics IDs and inline JSON routinely match the email pattern
    and end up in a customer deliverable.
    """
    cleaned = re.sub(r"(?is)<(script|style|template|noscript)\b[^>]*>.*?</\1\s*>", " ", page_html)
    return re.sub(r"(?is)<!--.*?-->", " ", cleaned)


def fetch_leads(keyword: str, location: str, max_results: int) -> tuple[pd.DataFrame, str]:
    geo = geocode_location(location)

    scopes = []
    if geo["area_id"]:
        scopes.append({"kind": "area", "id": geo["area_id"]})
    if geo["bbox"]:
        scopes.append({"kind": "bbox", "bbox": geo["bbox"]})

    elements: list[dict] = []
    for scope in scopes:
        elements = run_overpass(build_overpass_query(keyword, scope, max_results))
        if elements:
            break
    df = parse_elements(elements)
    # Small bounded pass to fill missing contact fields from businesses' own public websites.
    # This is best-effort; inaccessible pages and absent details remain blank.
    df = enrich_public_websites(df, max_sites=20)
    return df, geo["name"]


# ----------------------------------------------------------------------------
# AGENCY FILTERS
# ----------------------------------------------------------------------------
def website_host(url: str) -> str:
    try:
        host = urlparse(url if "://" in url else "https://" + url).hostname or ""
    except ValueError:
        return ""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def is_social_host(url: str) -> bool:
    host = website_host(url)
    return any(host == d or host.endswith("." + d) for d in SOCIAL_DOMAINS)


def opportunity_masks(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    no_site = df["Website"].isna()
    social_only = df["Website"].apply(lambda u: isinstance(u, str) and is_social_host(u))
    incomplete = df["Phone Number"].isna() | df["Physical Address"].isna()
    return no_site, social_only.astype(bool), incomplete


def apply_filters(df: pd.DataFrame, no_site: bool, social_only: bool,
                  incomplete: bool, mode: str) -> pd.DataFrame:
    m_site, m_social, m_incomplete = opportunity_masks(df)
    masks = []
    if no_site:
        masks.append(m_site)
    if social_only:
        masks.append(m_social)
    if incomplete:
        masks.append(m_incomplete)
    if not masks:
        return df

    combined = masks[0]
    for m in masks[1:]:
        combined = (combined & m) if mode == "Match ALL" else (combined | m)
    return df[combined]
