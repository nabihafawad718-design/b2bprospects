"""Visual theme — the original B2BProspects design system, preserved verbatim.

The BASE_CSS and HIDE_GRID_TOOLBAR_CSS blocks below are copied unchanged from the
existing app so the dashboard keeps its exact look. Additions are appended in
EXTRA_CSS and only style new components.
"""
from __future__ import annotations

import streamlit as st

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

EXTRA_CSS = """
<style>
/* ---------- Workspace navigation (sidebar) ---------- */
.navpills [role="radiogroup"]{gap:.4rem;}
.navpills [role="radiogroup"] label{
  border:1px solid var(--line); border-radius:11px; padding:.5rem .8rem;
  background:#fff; transition:all .18s ease;
}
.navpills [role="radiogroup"] label:hover{border-color:#a78bfa;}
.navpills [role="radiogroup"] label:has(input:checked){
  border-color:var(--primary); background:rgba(91,47,214,.06);
  box-shadow:0 8px 20px -14px rgba(91,47,214,.8);
}
.navpills [role="radiogroup"] label p{font-weight:600; font-size:.92rem; color:var(--text);}

/* ---------- Health badges ---------- */
.hbadge{
  display:inline-flex; align-items:center; gap:7px; padding:4px 12px; border-radius:999px;
  font-size:.74rem; font-weight:700; letter-spacing:.03em; text-transform:uppercase;
}
.hbadge i{width:7px; height:7px; border-radius:50%; display:inline-block;}
.hbadge.ok{color:#047857; background:rgba(16,185,129,.10); border:1px solid rgba(16,185,129,.40);}
.hbadge.ok i{background:var(--green);}
.hbadge.watch{color:#92400e; background:rgba(245,158,11,.12); border:1px solid rgba(245,158,11,.45);}
.hbadge.watch i{background:var(--amber);}
.hbadge.broken{color:#991b1b; background:rgba(220,38,38,.10); border:1px solid rgba(220,38,38,.40);}
.hbadge.broken i{background:#dc2626;}
.hbadge.first_run{color:#3730a3; background:rgba(91,47,214,.08); border:1px solid var(--line-strong);}
.hbadge.first_run i{background:var(--primary);}

/* ---------- Key/value detail rows ---------- */
.kv{display:grid; grid-template-columns:180px 1fr; gap:.35rem 1rem; font-size:.9rem; margin:.4rem 0 .2rem;}
.kv dt{color:var(--muted); font-weight:600;}
.kv dd{margin:0; color:var(--text); overflow-wrap:anywhere;}
@media (max-width:700px){.kv{grid-template-columns:1fr;}}

/* ---------- Warning / notice list ---------- */
.notes{margin:.5rem 0 0; padding:0; list-style:none;}
.notes li{
  position:relative; padding:.6rem .8rem .6rem 2rem; margin-bottom:.45rem;
  font-size:.88rem; line-height:1.5; color:var(--text);
  background:#fffbeb; border:1px solid rgba(245,158,11,.35); border-radius:11px;
}
.notes li::before{content:"!"; position:absolute; left:.75rem; top:.62rem;
  width:16px; height:16px; border-radius:50%; background:var(--amber); color:#fff;
  font-size:.68rem; font-weight:800; display:flex; align-items:center; justify-content:center;}
.notes.error li{background:#fef2f2; border-color:rgba(220,38,38,.35);}
.notes.error li::before{background:#dc2626;}

/* ---------- Step pill ---------- */
.stepnum{
  display:inline-flex; align-items:center; justify-content:center; width:26px; height:26px;
  border-radius:50%; background:var(--primary); color:#fff; font-size:.78rem; font-weight:800;
  margin-right:.55rem;
}

/* ---------- Compact field chooser ---------- */
.fieldhint{font-size:.8rem; color:var(--muted); margin:-.2rem 0 .6rem;}

/* ---------- Provenance strip ---------- */
.prov{font-size:.76rem; color:var(--muted); margin-top:.45rem;}
</style>
"""


def inject_css(hide_grid_toolbar: bool = False) -> None:
    """Injects the preserved design system, then the additions."""
    st.markdown(BASE_CSS, unsafe_allow_html=True)
    st.markdown(EXTRA_CSS, unsafe_allow_html=True)
    if hide_grid_toolbar:
        st.markdown(HIDE_GRID_TOOLBAR_CSS, unsafe_allow_html=True)
