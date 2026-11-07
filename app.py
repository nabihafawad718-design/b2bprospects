# app.py — B2BProspects.io | Data Extraction
"""Entry point.

The visual shell (page config, CSS, sidebar brand, hero, footer) is the original
B2BProspects design. The main area dispatches to one of four views:

    Directory Extraction  — new: URL → inspect → extract → validate → export
    Local Discovery       — the original OpenStreetMap pipeline, preserved
    Adapter Health        — maintenance dashboard for supported sites
    Run History           — audit trail of every extraction

Run with:  streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="B2BProspects.io | Data Extraction",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

from core.settings import SETTINGS, ensure_dirs  # noqa: E402
from ui.components import render_extraction_footer, render_footer  # noqa: E402
from ui.theme import inject_css  # noqa: E402
from ui.views import discover, extract, health, history  # noqa: E402

VIEWS = {
    "Directory Extraction": "extract",
    "Local Discovery": "discover",
    "Adapter Health": "health",
    "Run History": "history",
}

ensure_dirs()

st.session_state.setdefault("unlocked", False)
st.session_state.setdefault("leads", None)
st.session_state.setdefault("meta", "")
st.session_state.setdefault("nav", "Directory Extraction")

inject_css(hide_grid_toolbar=not (st.session_state["unlocked"] or not SETTINGS.license_enabled))

# ----------------------------------------------------------------------------
# SIDEBAR
# ----------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        '<div class="brand"><div class="brand-mark">🎯</div><div>'
        '<div class="brand-name">B2B<span>Prospects</span></div>'
        '<div class="brand-sub">Data Extraction</div></div></div>',
        unsafe_allow_html=True,
    )
    st.divider()

    with st.container(key="navpills"):
        selected_view = st.radio("Workspace", list(VIEWS), key="nav", label_visibility="collapsed")

    st.write("")
    if selected_view == "Local Discovery":
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
    else:
        max_results = 100
        st.markdown(
            f'<div class="tip"><b>Polite by default.</b> Requests are spaced '
            f"{SETTINGS.request_delay_seconds:g}s apart, robots.txt is honoured, and login-walled or "
            "CAPTCHA-protected pages are never touched. Collect only what the site publishes publicly.</div>",
            unsafe_allow_html=True,
        )

    st.write("")
    if SETTINGS.license_enabled:
        if st.session_state["unlocked"]:
            st.markdown('<span class="pill ok"><i></i>Export unlocked</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span class="pill" style="color:#92400e;background:rgba(245,158,11,.12);'
                        'border:1px solid rgba(245,158,11,.45)">🔒 Export locked</span>',
                        unsafe_allow_html=True)

# ----------------------------------------------------------------------------
# HEADER ZONE
# ----------------------------------------------------------------------------
if selected_view == "Directory Extraction":
    hero_badge = "Public Business Data · Maintained Adapters"
    hero_lede = (
        "Point it at a brokerage directory, see exactly what can be collected, then export a clean, "
        "deduplicated agent list with a full source trail."
    )
elif selected_view == "Local Discovery":
    hero_badge = "Free OpenStreetMap Intelligence"
    hero_lede = (
        "Surface local businesses that are ready to buy. Find companies with no website, a "
        "social-media-only presence or incomplete listings, then turn them into agency clients."
    )
else:
    hero_badge = "Operations"
    hero_lede = "Keep your adapters honest and your deliveries documented."

st.markdown(
    '<div class="hero">'
    f'<span class="hero-badge"><i></i>{hero_badge}</span>'
    '<h1>🎯 B2B<span class="grad">Prospects</span><span class="sep">|</span>Data Extraction</h1>'
    f"<p>{hero_lede}</p>"
    "</div>",
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------------------
# MAIN AREA
# ----------------------------------------------------------------------------
try:
    if selected_view == "Directory Extraction":
        extract.render()
    elif selected_view == "Local Discovery":
        discover.render(max_results)
    elif selected_view == "Adapter Health":
        health.render()
    else:
        history.render()
except Exception as exc:  # keep one bad run from blanking the whole app
    st.error(f"This screen hit an error: {exc.__class__.__name__}: {exc}")
    with st.expander("Technical detail", expanded=False):
        st.exception(exc)

if selected_view == "Local Discovery":
    render_footer()
else:
    render_extraction_footer()
