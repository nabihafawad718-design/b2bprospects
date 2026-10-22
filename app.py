# app.py — B2BProspects.io | Elite Prospecting Portal (search-index edition)
from __future__ import annotations

import html
import io
import ipaddress
import os
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from functools import lru_cache
from urllib.parse import quote_plus, unquote, urljoin, urlparse

import pandas as pd
import requests
import streamlit as st

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="B2BProspects.io | Elite Prospecting Portal",
    page_icon="static/favicon.ico",
    layout="wide",
    initial_sidebar_state="expanded",
)



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
# LEGACY SEARCH DATA LAYER (SearXNG; retained for reference, not used by the prototype UI)
# ----------------------------------------------------------------------------
class SearchBlocked(Exception):
    """Every SearXNG instance tried throttled or refused the request (HTTP 403/429)."""


class SearchUnavailable(Exception):
    """No SearXNG instance could be reached or returned usable results."""


class _InstanceFailure(Exception):
    def __init__(self, msg: str, blocked: bool = False):
        super().__init__(msg)
        self.blocked = blocked


SOCIAL_DOMAINS = (
    "facebook.com", "fb.com", "instagram.com", "linktr.ee", "yelp.com", "twitter.com", "x.com",
    "tiktok.com", "linkedin.com", "youtube.com", "wa.me", "business.site", "sites.google.com",
)

# Business directories: the listing is real, but the business's own website is unknown.
DIRECTORY_DOMAINS = (
    "yellowpages.com", "superpages.com", "bbb.org", "mapquest.com", "manta.com", "angi.com",
    "homeadvisor.com", "thumbtack.com", "nextdoor.com", "tripadvisor.com", "foursquare.com",
    "chamberofcommerce.com", "porch.com", "houzz.com", "expertise.com", "threebestrated.com",
    "yell.com", "cylex.us.com", "buildzoom.com", "alignable.com", "birdeye.com", "merchantcircle.com",
    "local.yahoo.com", "maps.google.com", "google.com", "bing.com", "apple.com", "opentable.com",
)

# Never businesses: reference, jobs, forums, marketplaces, media.
NOISE_DOMAINS = (
    "wikipedia.org", "reddit.com", "quora.com", "pinterest.com", "indeed.com", "glassdoor.com",
    "ziprecruiter.com", "craigslist.org", "amazon.com", "ebay.com", "walmart.com", "medium.com",
    "forbes.com", "nytimes.com", "cnn.com", "bbc.com", "gov", "edu", "github.com", "stackoverflow.com",
    "zillow.com", "realtor.com", "trulia.com", "redfin.com", "wikihow.com", "investopedia.com",
)

# SearXNG: https://searx.space is the public directory of live instances (it is not a search
# endpoint itself). We read its instance list, rank by speed, and fail over between instances.
# For the most reliable setup, self-host SearXNG and set SEARXNG_URL in .streamlit/secrets.toml
# or as an environment variable; that one instance is then used exclusively.
SEARX_SPACE_URL = "https://searx.space/data/instances.json"
SEARX_SEED_INSTANCES = (
    "https://searx.be",
    "https://searx.tiekoetter.com",
    "https://priv.au",
    "https://search.ononoki.org",
    "https://paulgo.io",
)
MAX_PAGES_PER_QUERY = 1
MAX_INSTANCE_TRIES = 2
INSTANCE_COOLDOWN_S = 900
REQUEST_TIMEOUT = (3, 5)
SEARCH_BUDGET_S = 40
EMAIL_WORKERS = 16
EMAIL_BUDGET_S = 18
PAGE_MAX_BYTES = 400_000

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_JSON_HEADERS = {"User-Agent": _UA, "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"}
_HTML_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

NO_DECISION_MAKER = "Not publicly listed"
COLUMNS = [
    "Company Name", "Decision Maker", "Category", "Website", "Phone Number", "Email",
    "Physical Address", "Map", "Source",
]

# --- regexes ---------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_PHONE_RE = re.compile(
    r"(?<![\d\-/])(?:\+?1[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]?\d{3}[\s.\-]?\d{4}(?![\d\-/])"
    r"|(?<![\w])\+\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4}(?![\d])"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_STREET_WORDS = (
    r"St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Hwy|Highway|Way|Ct|Court|"
    r"Pkwy|Parkway|Pl|Place|Cir|Circle|Trl|Trail|Fwy|Freeway|Sq|Square"
)
_ADDRESS_RE = re.compile(
    rf"\b\d{{1,6}}\s+(?:[NSEW]\.?\s+)?(?:[A-Za-z0-9.'\-]+\s+){{0,4}}?(?:{_STREET_WORDS})\b\.?"
    r"(?:,?\s*(?:Suite|Ste|Unit|#)\s*[\w\-]+)?"
    r"(?:,\s*[A-Za-z .'\-]+,?\s*[A-Z]{2}\.?(?:\s+\d{5}(?:-\d{4})?)?)?",
    re.I,
)
_LISTICLE_RE = re.compile(
    r"\b(top|best)\s+\d+\b|\b\d+\s+(best|top)\b|\bthe\s+best\b|\bnear\s+me\b|\btop[\s\-]rated\b|"
    r"\blist\s+of\b|\bhow\s+to\b|\bhow\s+much\b|\bcost\s+of\b|\bprices?\b|\bvs\.?\b|\bguide\b|"
    r"\bfind\s+(a|the)\b|\bhire\s+(a|the)\b|\bdirectory\b|\bcompanies\s+in\b",
    re.I,
)
_TITLE_SEP_RE = re.compile(r"\s+[|\-–—:•·»]\s+|\s*[|•·»]\s*")
_GENERIC_SEGMENTS = {
    "home", "homepage", "welcome", "official site", "official website", "contact", "contact us",
    "about", "about us", "services", "reviews", "photos", "facebook", "instagram", "yelp",
    "linkedin", "twitter", "youtube", "tiktok", "yellow pages", "mapquest", "bbb", "better business bureau",
    "angi", "homeadvisor", "thumbtack", "nextdoor", "foursquare", "tripadvisor",
}
_SITE_LABELS = (
    "facebook", "instagram", "yelp", "linkedin", "twitter", "youtube", "tiktok", "yellow pages",
    "mapquest", "better business bureau", "bbb", "angi", "homeadvisor", "thumbtack", "nextdoor",
)

# SearXNG HTML fallback (used when an instance has the JSON format disabled)
_ARTICLE_SPLIT = re.compile(r"(?=<article\b)", re.I)
_RESULT_DIV_SPLIT = re.compile(r"""(?=<div\b[^>]*class=["'][^"']*\bresult\b[^"']*["'])""", re.I)
_H3_LINK_RE = re.compile(r"<h3[^>]*>\s*<a\b([^>]*)>(.*?)</a>", re.S | re.I)
_CONTENT_RE = re.compile(
    r"""<p\b[^>]*class=["'][^"']*\bcontent\b[^"']*["'][^>]*>(.*?)</p>""", re.S | re.I
)

# Decision maker: "Name, Owner" / "Owner: Name" / "founded by Name"
_TOKEN = r"[A-Z][a-z'\-]{1,14}(?:[A-Z][a-z]{2,10})?"
_NAME = rf"(?P<name>(?:Dr\.\s)?{_TOKEN}(?:\s[A-Z]\.)?(?:\s{_TOKEN}){{1,2}})"
_ROLE = r"(?:co-?\s?founder|founder|owner|ceo|chief\s+executive\s+officer|president|proprietor)"
_DM_PATTERNS = (
    re.compile(rf"{_NAME}\s*(?:,|\(|-|–|—|\||:)\s*(?:the\s+|our\s+)?(?i:(?P<role>{_ROLE}))\b"),
    re.compile(
        rf"(?i:\b(?P<role>{_ROLE}))(?:\s*(?:&|and|/)\s*(?i:ceo|owner|founder|president))?"
        rf"\s*(?:[:\-–—,]|(?i:\bis\b)|(?i:\bnamed\b))\s*{_NAME}"
    ),
    re.compile(
        r"(?i:\b(?P<role>founded|owned|operated|run|started|established)"
        rf"(?:\s+and\s+(?:owned|operated|run))?\s+by)\s+{_NAME}"
    ),
)
_ROLE_LABELS = {
    "cofounder": "Co-Founder", "founder": "Founder", "founded": "Founder", "started": "Founder",
    "established": "Founder", "owner": "Owner", "owned": "Owner", "operated": "Owner", "run": "Owner",
    "proprietor": "Owner", "ceo": "CEO", "chiefexecutiveofficer": "CEO", "president": "President",
}
_NAME_STOPWORDS = {
    "the", "our", "meet", "about", "contact", "home", "owner", "founder", "ceo", "president", "company",
    "services", "service", "llc", "inc", "ltd", "corp", "group", "team", "family", "business", "operated",
    "owned", "licensed", "insured", "free", "best", "top", "call", "email", "request", "get", "ask",
    "hire", "visit", "learn", "view", "read", "click", "serving", "since", "located", "welcome", "hello",
    "plumbing", "plumber", "plumbers", "roofing", "roofer", "electric", "electrical", "dental", "dentist",
    "restaurant", "cafe", "salon", "repair", "auto", "construction", "contractor", "contractors",
    "landscaping", "cleaning", "legal", "law", "firm", "attorney", "medical", "clinic", "health", "care",
    "homes", "heating", "cooling", "air", "hvac", "pest", "control", "painting", "painters", "solutions",
    "systems", "enterprises", "associates", "partners", "brothers", "sons", "co", "and", "of", "for",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".css", ".js")
_CONTACT_PATH_RE = re.compile(r"contact|about|team|reach|owner|staff|who-we-are|our-story", re.I)
_HREF_RE = re.compile(r"""<a\b[^>]*\bhref\s*=\s*["']([^"'#][^"']*)["']""", re.I)


def sanitize_term(text: str) -> str:
    """Lowercase letters/digits/spaces/hyphens only. Used to validate and normalise user input."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 \-]", "", text.lower())).strip()


def _host_matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _strip_html(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", fragment or ""))).strip()


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf"""\b{name}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", attrs, re.I)
    if not m:
        return None
    return next(g for g in m.groups() if g is not None)


# --- SearXNG instances -------------------------------------------------------
def searxng_override() -> str | None:
    """A self-hosted instance from secrets.toml (SEARXNG_URL) or the environment, if configured."""
    for getter in (lambda: st.secrets["SEARXNG_URL"], lambda: os.environ["SEARXNG_URL"]):
        try:
            value = str(getter()).strip().rstrip("/")
        except Exception:
            continue
        if value.startswith("http"):
            return value
    return None


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def discover_instances() -> list[str]:
    """Live, fast, normal-web SearXNG instances from searx.space. Raises on failure (not cached)."""
    resp = requests.get(SEARX_SPACE_URL, headers=_JSON_HEADERS, timeout=(5, 15))
    resp.raise_for_status()
    instances = resp.json().get("instances", {})
    ranked: list[tuple[float, str]] = []
    for url, info in instances.items():
        try:
            if not url.startswith("https://") or ".onion" in url or ".i2p" in url:
                continue
            if info.get("network_type") != "normal":
                continue
            http_info = info.get("http") or {}
            if http_info.get("status_code") != 200 or http_info.get("error"):
                continue
            search = (info.get("timing") or {}).get("search") or {}
            ok = search.get("success_percentage")
            if isinstance(ok, (int, float)) and ok < 90:
                continue
            median = (search.get("all") or {}).get("median")
            ranked.append((float(median) if isinstance(median, (int, float)) else 9.9, url.rstrip("/")))
        except (AttributeError, TypeError, ValueError):
            continue
    ranked.sort()
    return [u for _, u in ranked[:12]]


@st.cache_resource
def _instance_cooldowns() -> dict[str, float]:
    """Instances that recently failed -> time (epoch) until which we skip them."""
    return {}


def get_instances() -> list[str]:
    override = searxng_override()
    if override:
        return [override]
    try:
        found = discover_instances()
    except Exception:
        found = []
    ordered = list(found) + [u for u in SEARX_SEED_INSTANCES if u not in found]
    return ordered


# --- SearXNG search ----------------------------------------------------------
def parse_searx_html(page_html: str) -> list[dict]:
    """Parse the default SearXNG HTML results page (fallback when JSON is disabled)."""
    parts = _ARTICLE_SPLIT.split(page_html)
    if len(parts) <= 1:
        parts = _RESULT_DIV_SPLIT.split(page_html)
    results = []
    for block in parts[1:]:
        lm = _H3_LINK_RE.search(block)
        if not lm:
            continue
        href = _attr(lm.group(1), "href")
        if not href:
            continue
        url = html.unescape(href).strip()
        if not url.startswith("http"):
            continue
        cm = _CONTENT_RE.search(block)
        results.append({"url": url, "title": _strip_html(lm.group(2)), "snippet": _strip_html(cm.group(1)) if cm else ""})
    return results


def _searx_fetch_page(base: str, query: str, page: int) -> list[dict]:
    """One results page from one instance: JSON first, then HTML. Raises _InstanceFailure."""
    params = {"q": query, "pageno": page, "language": "en-US", "safesearch": 0, "categories": "general"}
    endpoint = f"{base}/search"

    try:
        resp = requests.get(endpoint, params={**params, "format": "json"}, headers=_JSON_HEADERS, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as e:
        raise _InstanceFailure(str(e)) from e
    if resp.status_code == 429:
        raise _InstanceFailure("HTTP 429", blocked=True)
    if resp.status_code == 200:
        try:
            data = resp.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            results = [
                {"url": it["url"], "title": _strip_html(it.get("title", "")), "snippet": _strip_html(it.get("content", ""))}
                for it in data["results"]
                if isinstance(it, dict) and str(it.get("url", "")).startswith("http")
            ]
            if not results and page == 1 and data.get("unresponsive_engines"):
                raise _InstanceFailure("search engines unresponsive")
            return results

    # JSON disabled (typically HTTP 403) or not JSON: read the HTML page instead.
    try:
        resp = requests.get(endpoint, params=params, headers=_HTML_HEADERS, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as e:
        raise _InstanceFailure(str(e)) from e
    if resp.status_code in (403, 429):
        raise _InstanceFailure(f"HTTP {resp.status_code}", blocked=True)
    if resp.status_code != 200:
        raise _InstanceFailure(f"HTTP {resp.status_code}")
    results = parse_searx_html(resp.text)
    if not results and page == 1:
        raise _InstanceFailure("no parsable results")
    return results


def _run_query(query: str, instances: list[str], cooldowns: dict, start: int, deadline: float) -> tuple[list[dict], list[tuple[bool, str]]]:
    """Fetch up to MAX_PAGES_PER_QUERY pages for one query, failing over between instances."""
    out: list[dict] = []
    failures: list[tuple[bool, str]] = []
    n = len(instances)
    cursor = start
    for page in range(1, MAX_PAGES_PER_QUERY + 1):
        page_results = None
        tries = 0
        for k in range(n):
            if tries >= MAX_INSTANCE_TRIES or time.monotonic() > deadline:
                break
            idx = (cursor + k) % n
            base = instances[idx]
            if cooldowns.get(base, 0) > time.time():
                continue
            tries += 1
            try:
                page_results = _searx_fetch_page(base, query, page)
                cursor = idx  # stick with the instance that worked
                break
            except _InstanceFailure as e:
                cooldowns[base] = time.time() + INSTANCE_COOLDOWN_S
                failures.append((e.blocked, f"{urlparse(base).hostname}: {e}"))
        if not page_results:
            break
        out.extend(page_results)
    return out, failures


def _search_queries(keyword: str, location: str) -> list[str]:
    return [
        f"{keyword} in {location}",
        f"{keyword} {location} phone address",
        f"{keyword} {location} owner OR founder OR CEO",
    ]


# --- lead extraction ---------------------------------------------------------
def website_root(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme or 'https'}://{(p.hostname or '').lower()}"


def _is_business_page(url: str, host: str) -> bool:
    """For social/directory hosts keep single-business pages, drop posts, groups and search pages."""
    path = urlparse(url).path.lower().strip("/")
    segs = [s for s in path.split("/") if s]
    if host.endswith("yelp.com"):
        return path.startswith("biz/")
    if host.endswith("yellowpages.com"):
        return "/mip/" in f"/{path}/"
    if host.endswith("bbb.org"):
        return "/profile/" in f"/{path}/"
    if host.endswith("facebook.com") or host.endswith("fb.com"):
        if not segs:
            return False
        bad = {"groups", "events", "watch", "marketplace", "public", "hashtag", "posts", "photo", "photos", "reel", "share", "story.php", "login", "help"}
        if segs[0] in bad or "posts" in segs or "videos" in segs or "photos" in segs:
            return False
        return True
    if host.endswith("instagram.com"):
        return len(segs) == 1 and segs[0] not in {"explore", "p", "reel", "reels", "tv", "stories"}
    if host.endswith("linkedin.com"):
        return bool(segs) and segs[0] == "company"
    if _host_matches(host, ("twitter.com", "x.com", "tiktok.com", "youtube.com", "linktr.ee", "wa.me")):
        return len(segs) == 1
    return True  # business.site, sites.google.com and generic directories: rely on the title filter


def clean_business_name(title: str, host: str, location: str) -> str | None:
    title = re.sub(r"\(@[^)]*\)", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    loc_norm = _norm(location)
    segments: list[str] = []
    for seg in _TITLE_SEP_RE.split(title):
        seg = seg.strip(" ,.-–—|")
        low = seg.lower()
        if not seg or low in _GENERIC_SEGMENTS or any(low == lbl or low.startswith(lbl + " ") and len(low) < 22 for lbl in _SITE_LABELS):
            continue
        seg_norm = _norm(seg)
        if loc_norm and seg_norm and len(seg_norm) <= len(loc_norm) + 8 and (loc_norm in seg_norm or seg_norm in loc_norm):
            continue  # a bare "Houston, TX" style segment
        if re.fullmatch(r"[A-Za-z .'\-]+,\s*[A-Z]{2}(\s+\d{5})?", seg):
            continue
        segments.append(seg)
    if not segments:
        return None

    core = _norm(host.split(".")[0]) if host else ""
    if core and not _host_matches(host, SOCIAL_DOMAINS + DIRECTORY_DOMAINS):
        for seg in segments:
            sn = _norm(seg)
            if len(sn) >= 4 and (sn in core or core in sn):
                return seg[:80]
    return segments[0][:80]


def _first_phone(text: str) -> str | None:
    for m in _PHONE_RE.finditer(text):
        raw = m.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if 10 <= len(digits) <= 13 and not re.fullmatch(r"(19|20)\d{2}\D?(19|20)\d{2}", raw):
            return raw
    return None


def _first_address(text: str) -> str | None:
    m = _ADDRESS_RE.search(text)
    return re.sub(r"\s+", " ", m.group(0)).strip(" ,") if m else None


def maps_link(name: str, location: str) -> str:
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(f"{name} {location}")


def result_to_lead(r: dict, keyword: str, location: str) -> dict | None:
    url, title, snippet = r["url"], r["title"], r["snippet"]
    host = website_host(url)
    if not host or _host_matches(host, NOISE_DOMAINS):
        return None
    if _LISTICLE_RE.search(title):
        return None

    is_social = is_social_host(url)
    is_directory = _host_matches(host, DIRECTORY_DOMAINS)
    if (is_social or is_directory) and not _is_business_page(url, host):
        return None

    name = clean_business_name(title, host, location)
    if not name or len(_norm(name)) < 2:
        return None

    text = f"{title}. {snippet}"
    if is_social:
        website, kind = url.split("#")[0], "social"
    elif is_directory:
        website, kind = None, "directory"
    else:
        website, kind = website_root(url), "own"

    return {
        "_kind": kind,
        "_host": host,
        "Company Name": name,
        "Decision Maker": None,
        "Category": keyword.strip().title() or None,
        "Website": website,
        "Phone Number": _first_phone(text),
        "Email": None,
        "Physical Address": _first_address(snippet),
        "Map": maps_link(name, location),
    }


def merge_leads(leads: list[dict]) -> list[dict]:
    """De-duplicate: one row per business. A real own-website beats a social/directory listing."""
    by_key: dict[str, dict] = {}
    order: list[str] = []
    for lead in leads:
        key = f"host:{lead['_host']}" if lead["_kind"] == "own" else f"name:{_norm(lead['Company Name'])}"
        name_key = f"name:{_norm(lead['Company Name'])}"
        existing = by_key.get(key) or by_key.get(name_key)
        if existing is None:
            by_key[key] = lead
            by_key.setdefault(name_key, lead)
            order.append(key)
            continue
        if lead["_kind"] == "own" and existing["_kind"] != "own":
            for col in ("Phone Number", "Email", "Physical Address"):
                lead[col] = lead[col] or existing[col]
            existing.clear()
            existing.update(lead)
            by_key[key] = existing
        else:
            for col in ("Phone Number", "Email", "Physical Address"):
                existing[col] = existing[col] or lead[col]
            if existing["Website"] is None and lead["Website"]:
                existing["Website"], existing["_kind"] = lead["Website"], lead["_kind"]
    seen, out = set(), []
    for key in order:
        lead = by_key[key]
        if id(lead) not in seen:
            seen.add(id(lead))
            out.append(lead)
    return out


# --- Decision Maker (regex over public search snippets) -----------------------
def _valid_person(name: str, banned: set[str]) -> bool:
    tokens = [t for t in re.findall(r"[A-Za-z'\-]+", name) if t.lower() != "dr"]
    if len(tokens) < 2:
        return False
    for t in tokens:
        low = t.lower().strip("'-")
        if low in _NAME_STOPWORDS or low in banned:
            return False
    return True


def extract_decision_maker(texts: list[str], banned: set[str]) -> str | None:
    """First 'Name (Role)' found in the texts, matching CEO / Founder / Owner style patterns."""
    for text in texts:
        found = []
        for pattern in _DM_PATTERNS:
            for m in pattern.finditer(text):
                found.append((m.start(), m.group("name").strip(), m.group("role")))
        for _, name, role in sorted(found):
            if _valid_person(name, banned):
                label = _ROLE_LABELS.get(re.sub(r"[^a-z]", "", role.lower()), role.title())
                return f"{name} ({label})"
    return None


def _lead_texts(lead: dict, raw: list[dict]) -> list[str]:
    """Search-result text that belongs to this business: same domain first, then name mentions."""
    host, name_norm = lead["_host"], _norm(lead["Company Name"])
    same_domain, mentions = [], []
    for r in raw:
        text = f"{r['title']}. {r['snippet']}"
        r_host = website_host(r["url"])
        if lead["_kind"] == "own" and (r_host == host or r_host.endswith("." + host)):
            same_domain.append(text)
        elif len(name_norm) >= 6 and name_norm in _norm(text):
            mentions.append(text)
    return same_domain + mentions


def attach_decision_makers(leads: list[dict], raw: list[dict], keyword: str, location: str) -> None:
    generic = {w for w in re.findall(r"[a-z]{3,}", f"{keyword} {location}".lower())}
    for lead in leads:
        banned = generic | {w for w in re.findall(r"[a-z]{3,}", lead["Company Name"].lower())}
        banned |= {w for w in re.findall(r"[a-z]{3,}", lead["_host"].split(".")[0])}
        lead["Decision Maker"] = extract_decision_maker(_lead_texts(lead, raw), banned) or NO_DECISION_MAKER


# --- Email finder (strict: email domain must match the website domain) ---------
def email_matches_site(email: str, site_host: str) -> bool:
    if "@" not in email:
        return False
    email_domain = email.rsplit("@", 1)[1].lower().strip(".")
    host = site_host.lower().strip(".")
    host = host[4:] if host.startswith("www.") else host
    return bool(email_domain) and (
        email_domain == host or host.endswith("." + email_domain) or email_domain.endswith("." + host)
    )


def pick_site_email(text: str, site_host: str) -> str | None:
    text = html.unescape(text)
    candidates = [m.group(1) for m in re.finditer(r"mailto:([^\"'?<>\s]+)", text, re.I)]
    candidates += _EMAIL_RE.findall(text)
    for cand in candidates:
        m = _EMAIL_RE.search(unquote(cand))
        if not m:
            continue
        email = m.group(0).lower()
        if email.endswith(_IMG_EXT):
            continue
        if email_matches_site(email, site_host):
            return email
    return None


@lru_cache(maxsize=4096)
def _is_public_host(host: str) -> bool:
    """SSRF guard: only fetch hosts that resolve exclusively to public internet addresses."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
                return False
        except ValueError:
            return False
    return True


def _fetch_html(url: str) -> tuple[str, str] | None:
    """GET a page (max 3 redirects, each re-checked, 400 KB cap). Returns (final_url, html)."""
    for _ in range(4):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        if parsed.port not in (None, 80, 443) or not _is_public_host(parsed.hostname):
            return None
        try:
            resp = requests.get(url, headers=_HTML_HEADERS, timeout=(4, 7), allow_redirects=False, stream=True)
        except requests.exceptions.RequestException:
            return None
        try:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    return None
                url = urljoin(url, location)
                continue
            if resp.status_code != 200 or "html" not in resp.headers.get("Content-Type", "html").lower():
                return None
            body, size = [], 0
            for chunk in resp.iter_content(65536):
                body.append(chunk)
                size += len(chunk)
                if size >= PAGE_MAX_BYTES:
                    break
            return url, b"".join(body).decode(resp.encoding or "utf-8", errors="replace")
        except requests.exceptions.RequestException:
            return None
        finally:
            resp.close()
    return None


def _contact_links(page_html: str, base_url: str, site_host: str) -> list[str]:
    links: list[str] = []
    for href in _HREF_RE.findall(page_html):
        if href.lower().startswith(("mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, html.unescape(href))
        p = urlparse(absolute)
        if not p.hostname or not email_matches_site(f"x@{p.hostname.lower()}", site_host):
            continue
        if _CONTACT_PATH_RE.search(p.path) and absolute not in links:
            links.append(absolute)
    links.sort(key=lambda u: 0 if "contact" in u.lower() else 1)
    return links


def find_site_email(root_url: str, site_host: str) -> str | None:
    page = _fetch_html(root_url + "/")
    if page is None:
        return None
    final_url, body = page
    email = pick_site_email(body, site_host)
    if email:
        return email
    for link in _contact_links(body, final_url, site_host)[:2]:
        sub = _fetch_html(link)
        if sub:
            email = pick_site_email(sub[1], site_host)
            if email:
                return email
    return None


def attach_site_emails(leads: list[dict], raw: list[dict]) -> None:
    """Fill Email for businesses with their own website. Domain match is mandatory."""
    pending = []
    for lead in leads:
        if lead["_kind"] != "own":
            continue
        for text in _lead_texts(lead, raw)[:6]:
            email = pick_site_email(text, lead["_host"])
            if email:
                lead["Email"] = email
                break
        if not lead["Email"]:
            pending.append(lead)
    if not pending:
        return

    pool = ThreadPoolExecutor(max_workers=EMAIL_WORKERS)
    futures = {pool.submit(find_site_email, lead["Website"], lead["_host"]): lead for lead in pending}
    try:
        for fut in as_completed(futures, timeout=EMAIL_BUDGET_S):
            try:
                futures[fut]["Email"] = fut.result()
            except Exception:
                pass
    except FuturesTimeout:
        pass  # leave the rest without an email rather than keep the user waiting
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def leads_to_frame(leads: list[dict]) -> pd.DataFrame:
    if not leads:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame([{c: lead.get(c) for c in COLUMNS} for lead in leads], columns=COLUMNS)
    df["Decision Maker"] = df["Decision Maker"].fillna(NO_DECISION_MAKER)
    return df.astype(object).where(df.notna(), None).reset_index(drop=True)


# --- OpenStreetMap-based prototype search -----------------------------------
# This is a no-API-key prototype for validating the workflow, not a complete
# commercial lead database. Coverage depends on OpenStreetMap contributors.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OSM_USER_AGENT = "B2BProspectsPrototype/0.1 (+https://b2bprospects.io)"

INDUSTRY_TAGS = {
    "plumber": [('craft', 'plumber')],
    "plumbers": [('craft', 'plumber')],
    "plumbing": [('craft', 'plumber')],
    "roofer": [('craft', 'roofer')],
    "roofers": [('craft', 'roofer')],
    "roofing": [('craft', 'roofer')],
    "dentist": [('healthcare', 'dentist')],
    "dentists": [('healthcare', 'dentist')],
    "restaurant": [('amenity', 'restaurant')],
    "restaurants": [('amenity', 'restaurant')],
    "cafe": [('amenity', 'cafe')],
    "cafes": [('amenity', 'cafe')],
    "realtor": [('office', 'estate_agent')],
    "realtors": [('office', 'estate_agent')],
    "real estate": [('office', 'estate_agent')],
    "lawyer": [('office', 'lawyer')],
    "lawyers": [('office', 'lawyer')],
    "attorney": [('office', 'lawyer')],
    "attorneys": [('office', 'lawyer')],
    "electrician": [('craft', 'electrician')],
    "electricians": [('craft', 'electrician')],
    "accountant": [('office', 'accountant')],
    "accountants": [('office', 'accountant')],
    "doctor": [('healthcare', 'doctor')],
    "doctors": [('healthcare', 'doctor')],
    "veterinarian": [('amenity', 'veterinary')],
    "veterinarians": [('amenity', 'veterinary')],
    "auto repair": [('shop', 'car_repair')],
    "car repair": [('shop', 'car_repair')],
    "hair salon": [('shop', 'hairdresser')],
    "salon": [('shop', 'hairdresser')],
    "hotels": [('tourism', 'hotel')],
    "hotel": [('tourism', 'hotel')],
}


def industry_selectors(keyword: str) -> list[str] | None:
    """Return OSM selectors for common industries; None means name-match fallback."""
    pairs = INDUSTRY_TAGS.get(sanitize_term(keyword))
    if not pairs:
        return None
    return [f' nwr(around:25000,{{lat}},{{lon}})["{key}"="{value}"]["name"];'
            for key, value in pairs]


def _osm_text(tags: dict, *keys: str) -> str | None:
    for key in keys:
        value = tags.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _osm_address(tags: dict) -> str | None:
    parts = []
    street = " ".join(x for x in [tags.get("addr:housenumber"), tags.get("addr:street")] if x)
    if street:
        parts.append(street)
    for key in ("addr:unit", "addr:city", "addr:state", "addr:postcode", "addr:country"):
        if tags.get(key):
            parts.append(str(tags[key]).strip())
    return ", ".join(parts) or None


def _osm_category(tags: dict, fallback: str) -> str:
    for key in ("craft", "office", "shop", "amenity", "healthcare", "tourism", "leisure", "industrial"):
        if tags.get(key):
            return str(tags[key]).replace("_", " ").title()
    return fallback.strip().title()


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def geocode_location(location: str) -> tuple[float, float, str] | None:
    """Geocode one user-supplied place; cached to reduce requests to Nominatim."""
    try:
        response = requests.get(
            NOMINATIM_URL,
            params={"q": location, "format": "jsonv2", "limit": 1},
            headers={"User-Agent": OSM_USER_AGENT, "Accept-Language": "en"},
            timeout=(5, 12),
        )
        if response.status_code == 429:
            raise SearchBlocked("Nominatim rate limit (HTTP 429)")
        response.raise_for_status()
        results = response.json()
        if not results:
            return None
        item = results[0]
        return float(item["lat"]), float(item["lon"]), str(item.get("display_name") or location)
    except SearchBlocked:
        raise
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        raise SearchUnavailable(f"Location lookup failed: {exc}") from exc


def _overpass_query(keyword: str, lat: float, lon: float, search_mode: str, max_results: int) -> str:
    radius = 25000
    if search_mode == "Company name":
        term = sanitize_term(keyword)
        if not term:
            raise ValueError("Enter a company name using letters or numbers.")
        # sanitize_term removes regex punctuation, so the remaining phrase is safe to use here.
        regex = term
        selectors = [f' nwr(around:{radius},{lat},{lon})["name"~"{regex}",i];']
    else:
        selectors = industry_selectors(keyword)
        if selectors:
            selectors = [sel.format(lat=lat, lon=lon).replace('around:25000', f'around:{radius}') for sel in selectors]
        else:
            term = sanitize_term(keyword)
            if not term:
                raise ValueError("Enter an industry or business type using letters or numbers.")
            regex = term
            selectors = [f' nwr(around:{radius},{lat},{lon})["name"~"{regex}",i];']
    limit = max(1, min(int(max_results), 100))
    return "[out:json][timeout:25];(\n" + "\n".join(selectors) + f"\n);out center tags {limit};"


def _osm_elements_to_leads(elements: list[dict], keyword: str, location: str) -> list[dict]:
    leads, seen = [], set()
    for element in elements:
        tags = element.get("tags") or {}
        name = _osm_text(tags, "name", "brand", "operator")
        if not name:
            continue
        website = _osm_text(tags, "contact:website", "website", "url")
        if website and not website.lower().startswith(("http://", "https://")):
            website = "https://" + website
        phone = _osm_text(tags, "contact:phone", "phone", "mobile")
        email = _osm_text(tags, "contact:email", "email")
        if email and not _EMAIL_RE.fullmatch(email.strip()):
            email = None
        if phone:
            phone = phone[:80]
        key = (_norm(name), (website or "").lower(), (phone or "").lower())
        if key in seen:
            continue
        seen.add(key)
        obj_type = str(element.get("type") or "node")
        obj_id = element.get("id")
        source = f"https://www.openstreetmap.org/{obj_type}/{obj_id}" if obj_id else "https://www.openstreetmap.org/"
        leads.append({
            "Company Name": name[:120],
            "Decision Maker": NO_DECISION_MAKER,
            "Category": _osm_category(tags, keyword),
            "Website": website,
            "Phone Number": phone,
            "Email": email,
            "Physical Address": _osm_address(tags),
            "Map": maps_link(name, location),
            "Source": source,
        })
    return leads


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_leads(keyword: str, location: str, max_results: int, search_mode: str = "Industry + location") -> tuple[pd.DataFrame, str]:
    """Small no-key prototype using OpenStreetMap's public geocoding/search services."""
    place = geocode_location(location)
    if not place:
        raise SearchUnavailable(f"Could not find the location '{location}'. Try a city and region, e.g. Houston, Texas.")
    lat, lon, place_name = place
    query = _overpass_query(keyword, lat, lon, search_mode, max_results)
    # Fail over between public Overpass instances if one is temporarily unhealthy.
    # Keep retries limited: these are shared community services, not a bulk-data API.
    last_error = "unknown error"
    payload = None
    headers = {"User-Agent": OSM_USER_AGENT, "Accept": "application/json"}
    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(2):
            try:
                response = requests.post(
                    endpoint,
                    data={"data": query},
                    headers=headers,
                    timeout=(5, 45),
                )
            except requests.exceptions.Timeout:
                last_error = f"{endpoint} timed out"
                break
            except requests.exceptions.RequestException as exc:
                last_error = f"{endpoint}: {exc}"
                break

            if response.status_code == 200:
                try:
                    candidate = response.json()
                except ValueError:
                    last_error = f"{endpoint} returned unreadable data"
                    break
                if not isinstance(candidate, dict):
                    last_error = f"{endpoint} returned an unexpected response"
                    break
                remark = str(candidate.get("remark", ""))
                if "runtime error" in remark.lower() and not candidate.get("elements"):
                    last_error = f"{endpoint}: {remark}"
                    break
                payload = candidate
                break

            if response.status_code == 429:
                # Respect explicit rate limits; don't keep querying other public servers.
                raise SearchBlocked(f"OpenStreetMap Overpass rate limit (HTTP 429) at {endpoint}")
            if response.status_code in (502, 503, 504):
                last_error = f"{endpoint} is temporarily busy (HTTP {response.status_code})"
                if attempt == 0:
                    time.sleep(2)
                    continue
                break
            last_error = f"{endpoint} returned HTTP {response.status_code}"
            break
        if payload is not None:
            break

    if payload is None:
        raise SearchUnavailable(f"OpenStreetMap query failed after trying available Overpass servers: {last_error}")
    elements = payload.get("elements", [])
    leads = _osm_elements_to_leads(elements, keyword, location)
    leads = leads[:max(1, min(int(max_results), 100))]
    return leads_to_frame(leads), place_name


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_leads_legacy(keyword: str, location: str, max_results: int) -> tuple[pd.DataFrame, str]:
    """Legacy SearXNG implementation retained for reference; not used by the prototype UI."""
    instances = get_instances()
    cooldowns = _instance_cooldowns()
    queries = _search_queries(keyword, location)
    deadline = time.monotonic() + SEARCH_BUDGET_S
    raw: list[dict] = []
    failures: list[tuple[bool, str]] = []
    for i, query in enumerate(queries):
        if time.monotonic() >= deadline:
            break
        results, fails = _run_query(query, instances, cooldowns, i, deadline)
        raw.extend(results)
        failures.extend(fails)
    seen_urls: set[str] = set()
    unique_raw = []
    for r in raw:
        if r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            unique_raw.append(r)
    if not unique_raw:
        if failures:
            if any(blocked for blocked, _ in failures):
                raise SearchBlocked(failures[-1][1])
            raise SearchUnavailable(failures[-1][1])
        return leads_to_frame([]), location
    leads = [l for l in (result_to_lead(r, keyword, location) for r in unique_raw) if l]
    merged = merge_leads(leads)[: int(max_results)]
    attach_decision_makers(merged, unique_raw, keyword, location)
    attach_site_emails(merged, unique_raw)
    return leads_to_frame(merged), location


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
        'Data © <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap contributors</a> · ODbL</div>' ,
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------------------------
# STATE
# ----------------------------------------------------------------------------
st.session_state.setdefault("unlocked", True)
st.session_state.setdefault("leads", None)
st.session_state.setdefault("meta", "")

inject_css(hide_grid_toolbar=not st.session_state["unlocked"])

# ----------------------------------------------------------------------------
# SIDEBAR
# ----------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        '<div class="brand"><div>'
        """<div><img src="app/static/logo.png" width="38" style="vertical-align:middle; border-radius:8px; margin-right:8px;"> <span class='brand-name' style='font-weight:800; font-size:1.1rem; color:#1e1b4b;'>B2B<span style='color:#5b2fd6;'>Prospects</span></span></div>"""
        '<div class="brand-sub">Elite Prospecting Portal</div></div></div>',
        unsafe_allow_html=True,
    )
    st.divider()
    st.markdown(
        '<div class="badge-ok">✅ Data Source: OpenStreetMap</div>',
        unsafe_allow_html=True,
    )
    st.write("")
    max_results = st.select_slider(
        "Max results per search",
        options=[10, 25, 50, 100],
        value=25,
        help="The free public data source is best used for small, exploratory searches.",
    )
    st.markdown(
        '<div class="tip"><b>Prototype · no API key.</b> Results come from OpenStreetMap, not a complete company database. '
        "Coverage varies by place. Only publicly mapped contact fields are shown. "
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
    '<span class="hero-badge"><i></i>Live Search Intelligence</span>'
    '<h1>🎯 B2B<span class="grad">Prospects.io</span><span class="sep">|</span>Elite Prospecting Portal</h1>'
    "<p>Find public business listings by company name or industry, review the available contact details, "
    "and export a prospect list with source links.</p>"
    "</div>",
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------------------
# SEARCH DASHBOARD
# ----------------------------------------------------------------------------
with st.container(key="card_search"):
    section_header("Step 1", "Search Dashboard", "Search a company name or look up a supported business category in a city or region.")
    search_mode = st.radio("Search type", ["Industry + location", "Company name"], horizontal=True, key="search_mode")
    c1, c2, c3 = st.columns([3, 3, 1.5], vertical_alignment="bottom")
    if search_mode == "Company name":
        keyword = c1.text_input("Company Name", placeholder="e.g. Acme Roofing")
    else:
        keyword = c1.text_input("Industry / Business Type", placeholder="e.g. plumbers, dentists, roofing")
    location = c2.text_input("City / Region", placeholder="e.g. Houston, Texas")
    search_clicked = c3.button("🔍 Find Prospects", type="primary", use_container_width=True)

if search_clicked:
    keyword, location = keyword.strip(), location.strip()
    if not keyword or not location:
        st.warning("Enter both a Target Industry / Keyword and a Target Location.")
    elif not sanitize_term(keyword):
        st.warning("The keyword needs at least one letter or number, for example “plumbers”.")
    else:
        try:
            with st.spinner("Searching public listings..."):
                df, place_name = fetch_leads(keyword, location, max_results, search_mode)
            if df.empty:
                st.session_state["leads"] = None
                st.warning(
                    f"No matching businesses were found for “{keyword}” near “{location}” in OpenStreetMap. "
                    "Coverage varies. Try a broader search term or a nearby larger city."
                )
            else:
                st.session_state["leads"] = df
                st.session_state["meta"] = f"{keyword} — {place_name}"
                if search_mode == "Industry + location" and industry_selectors(keyword) is None:
                    st.info("This business type is not in the prototype's category map, so results are matched by business name. It may miss relevant companies.")
                st.info("Data source: OpenStreetMap. This is a community-mapped dataset, so missing websites, emails, phones, and businesses do not mean they do not exist.")
        except SearchBlocked:
            st.error(
                "⏳ The public OpenStreetMap service has temporarily limited requests. Please wait before retrying; "
                "repeated requests may prolong the limit. This prototype is intended for small, occasional tests."
            )
        except SearchUnavailable as e:
            st.error(f"🌐 The public data service could not complete this search. Please retry later. ({e})")
        except Exception as e:
            st.error(f"Unexpected error: {e}")

# ----------------------------------------------------------------------------
# EMPTY STATE
# ----------------------------------------------------------------------------
leads: pd.DataFrame | None = st.session_state["leads"]

if leads is None:
    with st.container(key="card_empty"):
        section_header("Getting started", "Try a small business search", "This prototype searches public OpenStreetMap records. Results vary by location and category.")
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
    f'<span class="src">Source: public search results · results cached for 60 minutes</span></div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="mgrid">'
    + metric_card("📇", "Total Leads Found", str(total), "Named businesses from public search", "#5b2fd6")
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
                "Source": st.column_config.LinkColumn("OSM Source", display_text="Open source ↗", width="small"),
            },
        )

# ----------------------------------------------------------------------------
# PROTOTYPE EXPORTS (payments are intentionally not connected in this test build)
# ----------------------------------------------------------------------------
with st.container(key="card_unlocked"):
    st.markdown(
        '<div class="wall"><div class="wall-ico" style="background:rgba(16,185,129,.12);'
        'border-color:rgba(16,185,129,.45)">🧪</div>'
        '<div><p class="wall-title">Prototype Preview</p>'
        '<p class="wall-text">Exports are enabled so the sample data can be evaluated. Payments and paid access are not connected in this prototype.</p>'
        '</div></div>',
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

render_footer()
