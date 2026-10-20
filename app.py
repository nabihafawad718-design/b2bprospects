# app.py — B2BProspects.io | Elite Prospecting Portal (search-index edition)
from __future__ import annotations

import hmac
import html
import io
import random
import re
import time
from urllib.parse import parse_qs, quote_plus, urlparse

import pandas as pd
import requests
import streamlit as st

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
LICENSE_KEY = "B2BPROSPECTS2026"  # test key; override with st.secrets["LICENSE_KEY"] in prod
CHECKOUT_URL = "https://b2bprospects.io"

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
# SEARCH DATA LAYER (DuckDuckGo public search index via requests + regex)
# ----------------------------------------------------------------------------
class SearchBlocked(Exception):
    """DuckDuckGo throttled or challenged the request (rate limit / bot check)."""


class SearchUnavailable(Exception):
    """DuckDuckGo could not be reached or returned unusable pages."""


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

DDG_ENDPOINTS = (
    "https://html.duckduckgo.com/html/",
    "https://lite.duckduckgo.com/lite/",
)
USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
)
MAX_PAGES_PER_QUERY = 4
REQUEST_TIMEOUT = (6, 15)

COLUMNS = ["Company Name", "Category", "Website", "Phone Number", "Email", "Physical Address", "Map"]

# --- regexes ---------------------------------------------------------------
_LINK_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.I)
_SNIPPET_RE = re.compile(
    r"""class=["'][^"']*result[_-]{1,2}snippet[^"']*["'][^>]*>(.*?)</(?:a|td|div)>""", re.S | re.I
)
_TAG_RE = re.compile(r"<[^>]+>")
_FORM_RE = re.compile(r"<form\b[^>]*>(.*?)</form>", re.S | re.I)
_INPUT_RE = re.compile(r"<input\b([^>]*)>", re.I)
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
_BLOCK_MARKERS = ("anomaly-modal", "g-recaptcha", "unfortunately, bots use duckduckgo", "anomaly.js")


def sanitize_term(text: str) -> str:
    """Lowercase letters/digits/spaces/hyphens only. Used to validate and normalise user input."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 \-]", "", text.lower())).strip()


def _host_matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _strip_html(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf"""\b{name}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", attrs, re.I)
    if not m:
        return None
    return next(g for g in m.groups() if g is not None)


# --- HTTP ------------------------------------------------------------------
def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://duckduckgo.com/",
            "Origin": "https://duckduckgo.com",
        }
    )
    return s


def _ddg_post(session: requests.Session, url: str, data: dict) -> str:
    """POST to a DuckDuckGo endpoint with short retries. Raises SearchBlocked / SearchUnavailable."""
    last = "unknown error"
    blocked = False
    for attempt in range(3):
        try:
            resp = session.post(url, data=data, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.RequestException as e:
            last = str(e)
            time.sleep(0.8 * (attempt + 1))
            continue

        body = resp.text or ""
        if resp.status_code in (202, 403, 418, 429) or any(m in body.lower() for m in _BLOCK_MARKERS):
            blocked = True
            last = f"HTTP {resp.status_code}"
            time.sleep(1.5 * (attempt + 1) + random.random())
            continue
        if resp.status_code >= 500:
            last = f"HTTP {resp.status_code}"
            time.sleep(1.0 * (attempt + 1))
            continue
        if resp.status_code != 200:
            raise SearchUnavailable(f"HTTP {resp.status_code}")
        return body
    if blocked:
        raise SearchBlocked(last)
    raise SearchUnavailable(last)


# --- result-page parsing -----------------------------------------------------
def _unwrap_ddg_href(href: str) -> str | None:
    href = html.unescape(href).strip()
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    host = (parsed.hostname or "").lower()
    if host.endswith("duckduckgo.com"):
        if parsed.path.startswith("/l/"):  # organic result redirect; ads use /y.js and are skipped
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            return target if target.startswith("http") else None
        return None
    return href if href.startswith("http") else None


def parse_results(page_html: str) -> list[dict]:
    """Extract organic results (title, url, snippet) from the HTML or Lite results page."""
    hits: list[tuple[int, int, str, str]] = []
    for m in _LINK_RE.finditer(page_html):
        attrs = m.group(1)
        cls = _attr(attrs, "class") or ""
        if "result__a" not in cls and "result-link" not in cls:
            continue
        href = _attr(attrs, "href")
        if not href:
            continue
        url = _unwrap_ddg_href(href)
        hits.append((m.start(), m.end(), url or "", _strip_html(m.group(2))))

    results = []
    for i, (_, end, url, title) in enumerate(hits):
        if not url or not title:
            continue
        region_end = hits[i + 1][0] if i + 1 < len(hits) else len(page_html)
        sm = _SNIPPET_RE.search(page_html, end, region_end)
        results.append({"url": url, "title": title, "snippet": _strip_html(sm.group(1)) if sm else ""})
    return results


def next_page_data(page_html: str) -> dict | None:
    """Return the form fields that request the next results page, or None on the last page."""
    best: dict | None = None
    best_s = -1
    for fm in _FORM_RE.finditer(page_html):
        fields = {}
        for im in _INPUT_RE.finditer(fm.group(1)):
            name, value = _attr(im.group(1), "name"), _attr(im.group(1), "value")
            if name:
                fields[name] = html.unescape(value or "")
        if "s" in fields and "q" in fields and fields["s"].isdigit() and int(fields["s"]) > best_s:
            best, best_s = fields, int(fields["s"])
    return best if best_s > 0 else None


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


def _first_email(text: str) -> str | None:
    for m in _EMAIL_RE.finditer(text):
        email = m.group(0).lower()
        if not email.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            return email
    return None


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
        "Category": keyword.strip().title() or None,
        "Website": website,
        "Phone Number": _first_phone(text),
        "Email": _first_email(text),
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
        # merge into existing row
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


def leads_to_frame(leads: list[dict]) -> pd.DataFrame:
    if not leads:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame([{c: lead.get(c) for c in COLUMNS} for lead in leads], columns=COLUMNS)
    return df.astype(object).where(df.notna(), None).reset_index(drop=True)


def _search_queries(keyword: str, location: str) -> list[str]:
    return [
        f"{keyword} in {location}",
        f"{keyword} {location} phone address",
        f"{keyword} {location} facebook",
    ]


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_leads(keyword: str, location: str, max_results: int) -> tuple[pd.DataFrame, str]:
    session = _new_session()
    collected: list[dict] = []
    raw_seen: set[str] = set()
    errors: list[Exception] = []
    ok_requests = 0

    for qi, query in enumerate(_search_queries(keyword, location)):
        if len(merge_leads([dict(x) for x in collected])) >= max_results:
            break
        form: dict | None = {"q": query, "b": "", "kl": "us-en"}
        endpoint_ok = False
        for endpoint in DDG_ENDPOINTS:
            try:
                page = _ddg_post(session, endpoint, form)
            except (SearchBlocked, SearchUnavailable) as e:
                errors.append(e)
                continue
            endpoint_ok = True
            break
        if not endpoint_ok:
            break  # both endpoints failed: stop now instead of hammering a throttled service

        for page_no in range(MAX_PAGES_PER_QUERY):
            ok_requests += 1
            for r in parse_results(page):
                if r["url"] in raw_seen:
                    continue
                raw_seen.add(r["url"])
                lead = result_to_lead(r, keyword, location)
                if lead:
                    collected.append(lead)
            if len(merge_leads([dict(x) for x in collected])) >= max_results:
                break
            nxt = next_page_data(page)
            if not nxt or page_no == MAX_PAGES_PER_QUERY - 1:
                break
            time.sleep(0.4 + random.random() * 0.4)
            try:
                page = _ddg_post(session, endpoint, nxt)
            except (SearchBlocked, SearchUnavailable) as e:
                errors.append(e)
                break
        time.sleep(0.4)

    if ok_requests == 0:
        blocked = [e for e in errors if isinstance(e, SearchBlocked)]
        if blocked:
            raise SearchBlocked(str(blocked[-1]))
        raise SearchUnavailable(str(errors[-1]) if errors else "no response")

    merged = merge_leads(collected)[: int(max_results)]
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
        "Business data collected from public search results</div>",
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
        '<div class="badge-ok">✅ Live Search Pipeline Active (No Billing Required)</div>',
        unsafe_allow_html=True,
    )
    st.write("")
    max_results = st.select_slider(
        "Max results per search",
        options=[25, 50, 100, 200, 300],
        value=100,
        help="Larger searches take a little longer because more result pages are fetched.",
    )
    st.markdown(
        '<div class="tip"><b>No API key needed.</b> Leads are collected from public search results. '
        "Phone numbers and addresses appear only when the listing shows them. "
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
    "<p>Surface local businesses that are ready to buy. Find companies with no website, a social-media-only "
    "presence or incomplete listings, then turn them into agency clients.</p>"
    "</div>",
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------------------
# SEARCH DASHBOARD
# ----------------------------------------------------------------------------
with st.container(key="card_search"):
    section_header("Step 1", "Search Dashboard", "Pick a niche and a market. We pull live data from public search results.")
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
            with st.spinner("Searching public listings..."):
                df, place_name = fetch_leads(keyword, location, max_results)
            if df.empty:
                st.session_state["leads"] = None
                st.warning(
                    f"No businesses found for “{keyword}” in “{location}”. "
                    "Try a broader keyword (for example “dentist”) or a larger area."
                )
            else:
                st.session_state["leads"] = df
                st.session_state["meta"] = f"{keyword} — {place_name}"
        except SearchBlocked:
            st.error(
                "⏳ The search provider is temporarily throttling requests. Wait about a minute and retry, "
                "or lower the max results."
            )
        except SearchUnavailable as e:
            st.error(f"🌐 The search service is unreachable right now. Please retry shortly. ({e})")
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
