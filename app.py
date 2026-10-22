# app.py — B2BProspects.io | Elite Prospecting Portal (OpenStreetMap edition)
from __future__ import annotations

import hmac
import html
import io
import re
import time
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
LICENSE_KEY = "B2BPROSPECTS2026"  # test key; override with st.secrets["LICENSE_KEY"] in prod
CHECKOUT_URL = "https://b2bprospects.io"

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

st.set_page_config(
    page_title="B2BProspects.io | Elite Prospecting Portal",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)


def get_license_key() -> str:
    try:
        return str(st.secrets["LICENSE_KEY"])
    except Exception:
        return LICENSE_KEY


esc = html.escape

# ----------------------------------------------------------------------------
# STYLING
# ----------------------------------------------------------------------------
BASE_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root{
  --bg:#f7f5ff; --surface:#ffffff; --soft:#f4f1ff;
  --line:#e5e0f6; --line-strong:#d6cfee;
  --text:#1e1b4b; --muted:#5d6280;
  --primary:#5b2fd6; --primary-d:#4a22b8; --violet:#7c3aed;
  --green:#10b981; --amber:#f59e0b;
}

/* ---------- Global ---------- */
html, body, .stApp, input, textarea, button {
  font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif !important;
}
.stApp{
  background:
    radial-gradient(900px 480px at 100% 0%, rgba(124,58,237,.15), transparent 60%),
    radial-gradient(800px 420px at 0% -10%, rgba(91,47,214,.07), transparent 60%),
    linear-gradient(180deg,#ffffff 0%, var(--bg) 100%);
  color:var(--text);
}
header[data-testid="stHeader"]{background:transparent;}
[data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer{display:none !important;}
.block-container{max-width:1280px; padding-top:2.2rem; padding-bottom:3rem;}
h1,h2,h3,h4{color:var(--text); letter-spacing:-.02em;}
hr{border-color:var(--line) !important;}

/* ---------- Hero ---------- */
.hero{text-align:center; padding:.6rem 0 1.8rem;}
.hero-badge{
  display:inline-flex; align-items:center; gap:8px; padding:7px 16px; border-radius:999px;
  font-size:.72rem; font-weight:700; letter-spacing:.09em; text-transform:uppercase;
  color:#3b2a8f; background:#fff; border:1px solid var(--line);
  box-shadow:0 6px 20px -10px rgba(91,47,214,.35);
}
.hero-badge i{width:8px; height:8px; border-radius:50%; background:var(--primary); display:inline-block;}
.hero h1{margin:1rem 0 .6rem; font-size:2.8rem; font-weight:800; line-height:1.1; color:var(--text);}
.hero .grad{
  background:linear-gradient(90deg,#5b2fd6 0%,#7c3aed 100%);
  -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; color:transparent;
}
.hero .sep{color:#c4b5fd; font-weight:300; margin:0 .45rem;}
.hero p{margin:0 auto; max-width:680px; color:var(--muted); font-size:1.05rem; line-height:1.65;}

/* ---------- Cards (st.container with key="card_*") ---------- */
[class*="st-key-card_"]{
  background:var(--surface); border:1px solid var(--line); border-radius:18px;
  padding:1.4rem 1.6rem; margin-bottom:1.1rem;
  box-shadow:0 14px 40px -26px rgba(76,29,149,.35);
  transition:border-color .25s ease, box-shadow .25s ease;
}
[class*="st-key-card_"]:hover{
  border-color:#c4b5fd; box-shadow:0 18px 48px -24px rgba(91,47,214,.45);
}
.sec-eyebrow{font-size:.7rem; font-weight:700; letter-spacing:.16em; text-transform:uppercase; color:var(--primary); margin:0;}
.sec-title{font-size:1.2rem; font-weight:700; color:var(--text); margin:.15rem 0 0;}
.sec-sub{font-size:.9rem; color:var(--muted); margin:.2rem 0 .4rem;}

/* ---------- Inputs ---------- */
[data-testid="stTextInput"] [data-testid="stWidgetLabel"] p{
  font-size:.72rem; font-weight:700; letter-spacing:.09em; text-transform:uppercase; color:var(--muted);
}
div[data-baseweb="input"], div[data-baseweb="base-input"]{
  background:#fff !important; border-radius:11px !important;
}
div[data-baseweb="input"]{border:1px solid var(--line-strong) !important; transition:all .2s ease;}
div[data-baseweb="input"]:hover{border-color:#a78bfa !important;}
div[data-baseweb="input"]:focus-within{
  border-color:var(--primary) !important; box-shadow:0 0 0 3px rgba(91,47,214,.18) !important;
}
div[data-baseweb="input"] input{color:var(--text) !important; height:2.7rem; font-size:.95rem;}
div[data-baseweb="input"] input::placeholder{color:#8f93ad !important;}

/* ---------- Buttons ---------- */
.stButton > button, .stFormSubmitButton > button, [data-testid="stDownloadButton"] > button{
  border-radius:11px; font-weight:600; height:2.8rem; border:1px solid var(--line-strong);
  background:#fff; color:var(--text); transition:all .2s ease;
}
.stButton > button:hover, [data-testid="stDownloadButton"] > button:hover{
  border-color:var(--primary); color:var(--primary);
}
button[kind="primary"], button[data-testid="stBaseButton-primary"], button[data-testid="stBaseButton-primaryFormSubmit"]{
  background:var(--primary) !important; border:none !important; color:#fff !important;
  box-shadow:0 10px 24px -10px rgba(91,47,214,.75);
}
button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover,
button[data-testid="stBaseButton-primaryFormSubmit"]:hover{
  background:var(--primary-d) !important; transform:translateY(-1px);
  box-shadow:0 14px 30px -10px rgba(91,47,214,.9) !important;
}
button[kind="primary"] p, button[data-testid="stBaseButton-primary"] p,
button[data-testid="stBaseButton-primaryFormSubmit"] p{color:#fff !important; font-weight:700;}
[data-testid="stDownloadButton"] > button{
  background:#13a56b; border:none; color:#fff; box-shadow:0 10px 24px -12px rgba(19,165,107,.9);
}
[data-testid="stDownloadButton"] > button:hover{background:#0f8f5c; color:#fff;}
[data-testid="stDownloadButton"] > button p{color:#fff !important; font-weight:700;}

/* ---------- Sidebar ---------- */
section[data-testid="stSidebar"]{background:#fff; border-right:1px solid var(--line);}
section[data-testid="stSidebar"] .block-container{padding-top:1.5rem;}
.brand{display:flex; align-items:center; gap:10px; margin-bottom:.4rem;}
.brand-mark{
  width:36px; height:36px; border-radius:10px; display:flex; align-items:center; justify-content:center;
  background:var(--primary); box-shadow:0 8px 20px -8px rgba(91,47,214,.9); font-size:1.05rem;
}
.brand-name{font-weight:800; font-size:1.08rem; color:var(--text); letter-spacing:-.01em;}
.brand-name span{color:var(--primary);}
.brand-sub{font-size:.7rem; color:var(--muted); letter-spacing:.08em; text-transform:uppercase;}
.badge-ok{
  display:block; padding:.85rem 1rem; border-radius:12px; font-size:.85rem; font-weight:700; line-height:1.5;
  color:#047857; background:rgba(16,185,129,.1); border:1px solid rgba(16,185,129,.45);
  box-shadow:0 10px 24px -16px rgba(16,185,129,.9);
}
.pill{display:inline-flex; align-items:center; gap:7px; padding:4px 11px; border-radius:999px; font-size:.74rem; font-weight:600;}
.pill i{width:7px; height:7px; border-radius:50%; display:inline-block;}
.pill.ok{color:#047857; background:rgba(16,185,129,.1); border:1px solid rgba(16,185,129,.35);}
.pill.ok i{background:var(--green);}
.tip{
  font-size:.8rem; line-height:1.55; color:var(--muted); padding:.8rem .9rem; border-radius:12px;
  background:var(--soft); border:1px solid var(--line);
}
.tip b{color:var(--text);}

/* ---------- Status bar ---------- */
.status{
  display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;
  padding:.75rem 1.1rem; margin:.2rem 0 1rem; border-radius:14px; font-size:.9rem; color:var(--muted);
  background:rgba(16,185,129,.07); border:1px solid rgba(16,185,129,.3);
}
.status b{color:var(--text);}
.status .dot{width:8px; height:8px; border-radius:50%; background:var(--green); display:inline-block; margin-right:9px;}
.status .src{font-size:.78rem; color:var(--muted);}

/* ---------- Metric cards ---------- */
.mgrid{display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:.2rem 0 1.3rem;}
@media (max-width:980px){.mgrid{grid-template-columns:repeat(2,minmax(0,1fr));}}
.mcard{
  position:relative; overflow:hidden; border-radius:16px; padding:1.1rem 1.25rem 1.15rem;
  background:#fff; border:1px solid var(--line); transition:all .25s ease;
  box-shadow:0 12px 32px -24px rgba(76,29,149,.4);
}
.mcard::before{content:""; position:absolute; left:0; right:0; top:0; height:3px; background:var(--accent);}
.mcard::after{
  content:""; position:absolute; width:140px; height:140px; right:-50px; top:-60px; border-radius:50%;
  background:radial-gradient(circle, var(--accent), transparent 70%); opacity:.12;
}
.mcard:hover{transform:translateY(-3px); border-color:var(--accent); box-shadow:0 18px 40px -20px var(--accent);}
.mcard-head{display:flex; align-items:center; gap:9px; margin-bottom:.7rem;}
.mcard-icon{
  width:30px; height:30px; display:flex; align-items:center; justify-content:center; border-radius:9px; font-size:.95rem;
  background:var(--soft); border:1px solid var(--line);
}
.mcard-label{font-size:.72rem; font-weight:700; letter-spacing:.09em; text-transform:uppercase; color:var(--muted);}
.mcard-value{font-size:2.15rem; font-weight:800; color:var(--text); line-height:1; letter-spacing:-.03em;}
.mcard-sub{margin-top:.55rem; font-size:.82rem; color:var(--muted);}
.mcard-sub b{color:var(--accent); font-weight:700;}

/* ---------- Filter chips (st.container key="chip_*") ---------- */
[class*="st-key-chip_"]{
  background:#faf8ff; border:1px solid var(--line); border-radius:14px; padding:.9rem 1rem .8rem;
  transition:all .2s ease; gap:.35rem;
}
[class*="st-key-chip_"]:hover{border-color:#a78bfa; box-shadow:0 10px 26px -16px rgba(91,47,214,.6);}
[class*="st-key-chip_"] label p{font-weight:600; font-size:.93rem; color:var(--text);}
.chip-sub{font-size:.8rem; color:var(--muted); line-height:1.4; margin:-.15rem 0 0 1.75rem;}
.chip-sub b{color:var(--primary);}
.chip-title{font-size:.72rem; font-weight:700; letter-spacing:.09em; text-transform:uppercase; color:var(--muted); margin:0 0 .15rem;}
[class*="st-key-chip_"] [role="radiogroup"] label p{font-size:.88rem; color:var(--text);}

/* ---------- Table card ---------- */
.tbl-head{display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-bottom:.4rem;}
.count-pill{
  font-size:.76rem; font-weight:700; color:var(--primary); padding:4px 12px; border-radius:999px;
  background:var(--soft); border:1px solid var(--line-strong);
}
[data-testid="stDataFrame"]{
  border:1px solid var(--line); border-radius:14px; overflow:hidden;
}

/* ---------- Empty state ---------- */
.steps{display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; margin-top:.9rem;}
@media (max-width:900px){.steps{grid-template-columns:1fr;}}
.step{padding:1.1rem 1.2rem; border-radius:14px; background:#faf8ff; border:1px solid var(--line);}
.step-n{
  width:28px; height:28px; border-radius:50%; display:flex; align-items:center; justify-content:center;
  font-size:.8rem; font-weight:700; color:#fff; background:var(--primary); margin-bottom:.65rem;
}
.step-t{font-weight:700; color:var(--text); font-size:.95rem; margin-bottom:.25rem;}
.step-d{font-size:.85rem; color:var(--muted); line-height:1.5;}

/* ---------- Wall (shared text styles) ---------- */
.wall{display:flex; align-items:flex-start; gap:16px;}
.wall-ico{
  flex:0 0 auto; width:48px; height:48px; border-radius:14px; display:flex; align-items:center; justify-content:center; font-size:1.4rem;
  background:rgba(245,158,11,.14); border:1px solid rgba(245,158,11,.45);
}
.wall-title{font-size:1.25rem; font-weight:800; color:var(--text); letter-spacing:-.01em; margin:0;}
.wall-text{font-size:.97rem; color:var(--muted); line-height:1.6; margin:.35rem 0 0;}

/* ---------- Paywall (dark indigo banner on the light page) ---------- */
.st-key-card_paywall{
  background:
    radial-gradient(600px 240px at 90% -30%, rgba(245,158,11,.28), transparent 60%),
    linear-gradient(150deg, #2a1a6e 0%, #1e1b4b 65%) !important;
  border:1px solid rgba(251,191,36,.55) !important; border-radius:20px !important; padding:1.7rem 1.9rem !important;
  box-shadow:0 30px 70px -30px rgba(30,27,75,.85), 0 0 0 4px rgba(245,158,11,.10) !important;
}
.st-key-card_paywall:hover{border-color:#fbbf24 !important;}
.st-key-card_paywall .wall-title{color:#fff;}
.st-key-card_paywall .wall-text{color:#d9d5f7;}
.st-key-card_paywall .wall-text a{color:#fcd34d; font-weight:700; text-decoration:none; border-bottom:1px solid rgba(252,211,77,.5);}
.st-key-card_paywall .wall-text a:hover{color:#fde68a;}
.st-key-card_paywall .wall-text b{color:#fff;}
.wall-badges{display:flex; gap:8px; flex-wrap:wrap; margin:.9rem 0 1.1rem;}
.wall-badges span{
  font-size:.74rem; font-weight:600; color:#fde68a; padding:4px 11px; border-radius:999px;
  background:rgba(245,158,11,.12); border:1px solid rgba(252,211,77,.35);
}
.st-key-card_paywall [data-testid="stTextInput"] [data-testid="stWidgetLabel"] p{color:#cfc9f5;}
.st-key-card_paywall [data-testid="stForm"]{border:none !important; padding:0 !important; background:transparent !important;}
.st-key-card_paywall button[data-testid="stBaseButton-primaryFormSubmit"]{
  background:linear-gradient(135deg,#fbbf24 0%,#f59e0b 60%,#d97706 100%) !important;
  box-shadow:0 12px 30px -10px rgba(245,158,11,.9), inset 0 1px 0 rgba(255,255,255,.35) !important;
}
.st-key-card_paywall button[data-testid="stBaseButton-primaryFormSubmit"] p{color:#1c1203 !important;}

/* ---------- Unlocked ---------- */
.st-key-card_unlocked{
  border-color:rgba(16,185,129,.45) !important;
  background:linear-gradient(150deg, rgba(16,185,129,.08), #fff 60%) !important;
}

/* ---------- Alerts / misc ---------- */
[data-testid="stAlert"]{border-radius:12px; border:1px solid var(--line-strong);}
[data-testid="stSpinner"] p{color:var(--muted);}
.foot{text-align:center; color:var(--muted); font-size:.78rem; padding:1.6rem 0 .2rem; line-height:1.7;}
.foot a{color:var(--primary); text-decoration:none; font-weight:600;}
</style>
"""

HIDE_GRID_TOOLBAR_CSS = """
<style>
/* Hides the grid's built-in hover toolbar (CSV download icon) while export is locked */
[data-testid="stElementToolbar"]{display:none !important;}
</style>
"""


def inject_css(hide_grid_toolbar: bool) -> None:
    st.markdown(BASE_CSS, unsafe_allow_html=True)
    if hide_grid_toolbar:
        st.markdown(HIDE_GRID_TOOLBAR_CSS, unsafe_allow_html=True)


# ----------------------------------------------------------------------------
# OSM DATA LAYER (OpenStreetMap: Nominatim geocoder + Overpass API, no keys)
# ----------------------------------------------------------------------------
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


@st.cache_data(ttl=3600, show_spinner=False)
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
    return parse_elements(elements), geo["name"]


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


def to_excel_bytes(df: pd.DataFrame) -> bytes | None:
    try:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Leads")
        return buf.getvalue()
    except Exception:
        return None  # openpyxl not installed


# ----------------------------------------------------------------------------
# UI HELPERS
# ----------------------------------------------------------------------------
def metric_card(icon: str, label: str, value: str, sub: str, accent: str) -> str:
    return (
        f'<div class="mcard" style="--accent:{accent}">'
        f'<div class="mcard-head"><span class="mcard-icon">{icon}</span>'
        f'<span class="mcard-label">{label}</span></div>'
        f'<div class="mcard-value">{value}</div>'
        f'<div class="mcard-sub">{sub}</div></div>'
    )


def section_header(eyebrow: str, title: str, sub: str = "") -> None:
    sub_html = f'<p class="sec-sub">{sub}</p>' if sub else ""
    st.markdown(
        f'<p class="sec-eyebrow">{eyebrow}</p><p class="sec-title">{title}</p>{sub_html}',
        unsafe_allow_html=True,
    )


def render_footer() -> None:
    st.markdown(
        '<div class="foot">© 2026 <a href="https://b2bprospects.io" target="_blank">B2BProspects.io</a><br>'
        'Business data © <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap contributors</a>'
        " (ODbL) · Served via the Overpass API</div>",
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------------------------
# STATE
# ----------------------------------------------------------------------------
st.session_state.setdefault("unlocked", False)
st.session_state.setdefault("leads", None)
st.session_state.setdefault("meta", "")

inject_css(hide_grid_toolbar=not st.session_state["unlocked"])

# ----------------------------------------------------------------------------
# SIDEBAR
# ----------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        '<div class="brand"><div class="brand-mark">🎯</div><div>'
        '<div class="brand-name">B2B<span>Prospects</span></div>'
        '<div class="brand-sub">Elite Prospecting Portal</div></div></div>',
        unsafe_allow_html=True,
    )
    st.divider()
    st.markdown(
        '<div class="badge-ok">✅ OpenStreetMap Data Pipeline Active (No Billing Required)</div>',
        unsafe_allow_html=True,
    )
    st.write("")
    max_results = st.select_slider(
        "Max results per search",
        options=[25, 50, 100, 200, 300],
        value=100,
        help="Larger searches take longer on the free public OpenStreetMap servers.",
    )
    st.markdown(
        '<div class="tip"><b>No API key needed.</b> Data comes from OpenStreetMap, which is community maintained. '
        "Coverage varies by city, and some businesses have no website or phone listed. "
        "City-level locations such as <b>Houston, Texas</b> work best.</div>",
        unsafe_allow_html=True,
    )
    if st.session_state["unlocked"]:
        st.write("")
        st.markdown('<span class="pill ok"><i></i>Export unlocked</span>', unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# HEADER ZONE
# ----------------------------------------------------------------------------
st.markdown(
    '<div class="hero">'
    '<span class="hero-badge"><i></i>Free OpenStreetMap Intelligence</span>'
    '<h1>🎯 B2B<span class="grad">Prospects.io</span><span class="sep">|</span>Elite Prospecting Portal</h1>'
    "<p>Surface local businesses that are ready to buy. Find companies with no website, a social-media-only "
    "presence or incomplete listings, then turn them into agency clients.</p>"
    "</div>",
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------------------
# SEARCH DASHBOARD
# ----------------------------------------------------------------------------
with st.container(key="card_search"):
    section_header("Step 1", "Search Dashboard", "Pick a niche and a market. We pull live data from OpenStreetMap.")
    c1, c2, c3 = st.columns([3, 3, 1.5], vertical_alignment="bottom")
    keyword = c1.text_input("Target Industry / Keyword", placeholder="e.g. plumbers, dentists, roofing")
    location = c2.text_input("Target Location", placeholder="e.g. Houston, Texas")
    search_clicked = c3.button("🔍 Find Prospects", type="primary", use_container_width=True)

if search_clicked:
    keyword, location = keyword.strip(), location.strip()
    if not keyword or not location:
        st.warning("Enter both a Target Industry / Keyword and a Target Location.")
    elif not sanitize_term(keyword):
        st.warning("The keyword needs at least one letter or number, for example “plumbers”.")
    else:
        try:
            with st.spinner("Searching OpenStreetMap... large cities can take up to a minute."):
                df, place_name = fetch_leads(keyword, location, max_results)
            if df.empty:
                st.session_state["leads"] = None
                st.warning(
                    f"No businesses found for “{keyword}” in “{location}”. OpenStreetMap coverage varies, "
                    "so try a broader keyword (for example “dentist”) or a larger area."
                )
            else:
                st.session_state["leads"] = df
                st.session_state["meta"] = f"{keyword} — {place_name}"
        except LocationNotFound:
            st.warning(f"Couldn't find “{location}”. Try a city and state or country, such as “Houston, Texas”.")
        except GeocodeError as e:
            st.error(f"🌐 The OpenStreetMap location service is unreachable right now. Please retry shortly. ({e})")
        except OverpassError as e:
            st.error(
                "⏳ The free OpenStreetMap servers are busy or the search timed out. Wait a minute and retry, "
                f"or use a smaller area or fewer results. ({e})"
            )
        except Exception as e:
            st.error(f"Unexpected error: {e}")

# ----------------------------------------------------------------------------
# EMPTY STATE
# ----------------------------------------------------------------------------
leads: pd.DataFrame | None = st.session_state["leads"]

if leads is None:
    with st.container(key="card_empty"):
        section_header("Getting started", "Your prospect pipeline is empty", "Three quick steps to your first list of agency-ready leads.")
        st.markdown(
            '<div class="steps">'
            '<div class="step"><div class="step-n">1</div><div class="step-t">Choose your niche</div>'
            '<div class="step-d">Type an industry such as plumbers, dentists, roofing or restaurants.</div></div>'
            '<div class="step"><div class="step-n">2</div><div class="step-t">Pick a market</div>'
            '<div class="step-d">Enter a city and state or country, then hit Find Prospects. No API key or billing needed.</div></div>'
            '<div class="step"><div class="step-n">3</div><div class="step-t">Filter and export</div>'
            '<div class="step-d">Isolate no-website, social-only and incomplete listings, then unlock your download.</div></div>'
            "</div>",
            unsafe_allow_html=True,
        )
    render_footer()
    st.stop()

# ----------------------------------------------------------------------------
# STATUS + METRIC CARDS
# ----------------------------------------------------------------------------
total = len(leads)
m_site, m_social, m_incomplete = opportunity_masks(leads)

score = m_site.astype(int) + m_social.astype(int) + m_incomplete.astype(int)
high_value = int((score >= 2).sum())
quick_wins = int((m_site & leads["Phone Number"].notna()).sum())
with_email = int(leads["Email"].notna().sum())


def pct(n: int) -> str:
    return f"{(n / total * 100):.0f}%" if total else "0%"


st.markdown(
    f'<div class="status"><span><span class="dot"></span><b>{total}</b> prospects loaded for '
    f'<b>{esc(st.session_state["meta"])}</b></span>'
    f'<span class="src">Source: OpenStreetMap via Overpass API · results cached for 60 minutes</span></div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="mgrid">'
    + metric_card("📇", "Total Leads Found", str(total), "Named businesses from OpenStreetMap", "#5b2fd6")
    + metric_card("💎", "High-Value Leads", str(high_value), f"<b>{pct(high_value)}</b> match 2+ agency criteria", "#8b5cf6")
    + metric_card("⚡", "Quick Wins", str(quick_wins), f"<b>{pct(quick_wins)}</b> have no website and a phone number", "#10b981")
    + metric_card("✉️", "Emails Found", str(with_email), f"<b>{pct(with_email)}</b> list a public email address", "#f59e0b")
    + "</div>",
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------------------
# ADVANCED AGENCY FILTERS
# ----------------------------------------------------------------------------
with st.container(key="card_filters"):
    section_header("Step 2", "Advanced Agency Filters", "Isolate the leads that map directly to your services.")
    fc1, fc2, fc3, fc4 = st.columns(4)

    with fc1:
        with st.container(key="chip_site"):
            f_no_site = st.checkbox("🌐 No Website", key="f_no_site")
            st.markdown(
                f'<div class="chip-sub">Web design clients<br><b>{int(m_site.sum())}</b> leads match</div>',
                unsafe_allow_html=True,
            )
    with fc2:
        with st.container(key="chip_social"):
            f_social = st.checkbox("📱 Social-Media-Only Site", key="f_social")
            st.markdown(
                f'<div class="chip-sub">Website rebuild clients<br><b>{int(m_social.sum())}</b> leads match</div>',
                unsafe_allow_html=True,
            )
    with fc3:
        with st.container(key="chip_incomplete"):
            f_incomplete = st.checkbox("📇 Incomplete Listing", key="f_incomplete")
            st.markdown(
                f'<div class="chip-sub">Local SEO clients (no phone or address)<br><b>{int(m_incomplete.sum())}</b> leads match</div>',
                unsafe_allow_html=True,
            )
    with fc4:
        with st.container(key="chip_logic"):
            st.markdown('<p class="chip-title">Combine filters</p>', unsafe_allow_html=True)
            f_mode = st.radio(
                "Combine filters",
                ["Match ANY", "Match ALL"],
                horizontal=True,
                label_visibility="collapsed",
                key="f_mode",
            )

filtered = apply_filters(leads, f_no_site, f_social, f_incomplete, f_mode)

# ----------------------------------------------------------------------------
# DATA GRID
# ----------------------------------------------------------------------------
with st.container(key="card_table"):
    st.markdown(
        '<div class="tbl-head"><div><p class="sec-eyebrow">Step 3</p>'
        '<p class="sec-title">Prospect Database</p></div>'
        f'<span class="count-pill">Showing {len(filtered)} of {total} leads</span></div>',
        unsafe_allow_html=True,
    )
    if filtered.empty:
        st.warning("No leads match the selected filters. Loosen the filters to see more.")
    else:
        st.dataframe(
            filtered,
            use_container_width=True,
            hide_index=True,
            height=min(560, 38 * (len(filtered) + 1) + 3),
            column_config={
                "Company Name": st.column_config.TextColumn("Company Name", width="medium"),
                "Category": st.column_config.TextColumn("Category", width="small"),
                "Website": st.column_config.LinkColumn("Website", display_text="Visit site ↗", width="small"),
                "Phone Number": st.column_config.TextColumn("Phone Number", width="small"),
                "Email": st.column_config.TextColumn("Email", width="medium"),
                "Physical Address": st.column_config.TextColumn("Physical Address", width="large"),
                "Map": st.column_config.LinkColumn("Map", display_text="View ↗", width="small"),
            },
        )

# ----------------------------------------------------------------------------
# MONETIZATION PAYWALL
# ----------------------------------------------------------------------------
if st.session_state["unlocked"]:
    with st.container(key="card_unlocked"):
        st.markdown(
            '<div class="wall"><div class="wall-ico" style="background:rgba(16,185,129,.12);'
            'border-color:rgba(16,185,129,.45)">🔓</div>'
            '<div><p class="wall-title">Export Unlocked</p>'
            '<p class="wall-text">Unlimited lifetime downloads are active. Your current filtered view is ready to export.</p>'
            "</div></div>",
            unsafe_allow_html=True,
        )
        if not filtered.empty:
            slug = re.sub(r"[^a-z0-9]+", "_", st.session_state["meta"].lower()).strip("_") or "leads"
            d1, d2, _ = st.columns([1.2, 1.2, 4])
            d1.download_button(
                "⬇️ Download CSV",
                data=filtered.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"b2bprospects_{slug}.csv",
                mime="text/csv",
                use_container_width=True,
            )
            xlsx = to_excel_bytes(filtered)
            if xlsx:
                d2.download_button(
                    "⬇️ Download Excel",
                    data=xlsx,
                    file_name=f"b2bprospects_{slug}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
else:
    with st.container(key="card_paywall"):
        st.markdown(
            '<div class="wall"><div class="wall-ico">🔒</div><div>'
            '<p class="wall-title">🔒 CSV/Excel Export is Locked.</p>'
            '<p class="wall-text">Unlock unlimited lifetime downloads for a one-time fee of <b>$49</b> at '
            f'<a href="{CHECKOUT_URL}" target="_blank">b2bprospects.io</a></p>'
            "</div></div>"
            '<div class="wall-badges"><span>✓ One-time payment</span><span>✓ Lifetime access</span>'
            "<span>✓ CSV + Excel exports</span></div>",
            unsafe_allow_html=True,
        )
        with st.form("license_form", border=False):
            k1, k2 = st.columns([3, 1.2], vertical_alignment="bottom")
            entered = k1.text_input(
                "Enter License Key to Unlock Download",
                type="password",
                placeholder="Paste your license key",
            )
            submitted = k2.form_submit_button("🔓 Unlock Download", type="primary", use_container_width=True)
        if submitted:
            if hmac.compare_digest(entered.strip().encode(), get_license_key().encode()):
                st.session_state["unlocked"] = True
                st.rerun()
            else:
                st.error("❌ Invalid license key. Please check it and try again.")

render_footer()
