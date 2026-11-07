"""Reusable pieces of the original interface.

The helpers here are lifted from the existing app unchanged (section_header,
metric_card, render_footer) plus a few additions that reuse the same markup and
CSS classes. Nothing in this file introduces a new visual language.
"""
from __future__ import annotations

import html

import streamlit as st

from core.settings import SETTINGS

esc = html.escape


# ---------------------------------------------------------------------------
# Original helpers
# ---------------------------------------------------------------------------
def metric_card(icon: str, label: str, value: str, sub: str, accent: str) -> str:
    # Values can include page-derived text (pagination reason, payload name), so
    # escape them rather than injecting site content into the dashboard.
    return (
        f'<div class="mcard" style="--accent:{esc(str(accent))}">'
        f'<div class="mcard-head"><span class="mcard-icon">{esc(str(icon))}</span>'
        f'<span class="mcard-label">{esc(str(label))}</span></div>'
        f'<div class="mcard-value">{esc(str(value))}</div>'
        f'<div class="mcard-sub">{esc(str(sub))}</div></div>'
    )


def metric_grid(cards: str) -> None:
    st.markdown(f'<div class="mgrid">{cards}</div>', unsafe_allow_html=True)


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


def render_extraction_footer() -> None:
    st.markdown(
        '<div class="foot">© 2026 <a href="https://b2bprospects.io" target="_blank">B2BProspects.io</a> · '
        "Data Extraction<br>Collected from publicly accessible pages. "
        "Respect each site's terms of use and applicable law.</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Additions (same markup vocabulary)
# ---------------------------------------------------------------------------
def status_bar(main: str, source: str, tone: str = "ok") -> None:
    colour = {
        "ok": ("rgba(16,185,129,.07)", "rgba(16,185,129,.3)", "var(--green)"),
        "watch": ("rgba(245,158,11,.08)", "rgba(245,158,11,.35)", "var(--amber)"),
        "broken": ("rgba(220,38,38,.07)", "rgba(220,38,38,.3)", "#dc2626"),
    }[tone]
    st.markdown(
        f'<div class="status" style="background:{colour[0]};border-color:{colour[1]}">'
        f'<span><span class="dot" style="background:{colour[2]}"></span>{main}</span>'
        f'<span class="src">{esc(source)}</span></div>',
        unsafe_allow_html=True,
    )


def health_badge(status: str) -> str:
    label = {
        "ok": "Healthy",
        "watch": "Needs review",
        "broken": "Broken",
        "first_run": "First run",
    }.get(status, status)
    return f'<span class="hbadge {esc(status)}"><i></i>{esc(label)}</span>'


def notes(messages: list[str], error: bool = False) -> None:
    if not messages:
        return
    klass = "notes error" if error else "notes"
    items = "".join(f"<li>{esc(str(m))}</li>" for m in messages)
    st.markdown(f'<ul class="{klass}">{items}</ul>', unsafe_allow_html=True)


def kv(rows: list[tuple[str, str]]) -> None:
    body = "".join(f"<dt>{esc(k)}</dt><dd>{esc(str(v))}</dd>" for k, v in rows)
    st.markdown(f'<dl class="kv">{body}</dl>', unsafe_allow_html=True)


def count_pill(text: str) -> str:
    return f'<span class="count-pill">{esc(text)}</span>'


# ---------------------------------------------------------------------------
# Export gating
# ---------------------------------------------------------------------------
def exports_allowed() -> bool:
    """True when the operator may download.

    With LICENSE_ENABLED off (the default for an internal tool) downloads are always
    available. Turning it on restores the original $49 gate exactly.
    """
    if not SETTINGS.license_enabled:
        return True
    return bool(st.session_state.get("unlocked", False))


def current_license_key() -> str:
    try:
        return str(st.secrets["LICENSE_KEY"])
    except Exception:
        return SETTINGS.license_key


def render_export_gate(form_key: str = "license_form") -> bool:
    """Render the original unlock/paywall card. Returns True when export is allowed."""
    if exports_allowed():
        if SETTINGS.license_enabled:
            with st.container(key="card_unlocked"):
                st.markdown(
                    '<div class="wall"><div class="wall-ico" style="background:rgba(16,185,129,.12);'
                    'border-color:rgba(16,185,129,.45)">🔓</div>'
                    '<div><p class="wall-title">Export Unlocked</p>'
                    '<p class="wall-text">Unlimited lifetime downloads are active. '
                    "Your current view is ready to export.</p></div></div>",
                    unsafe_allow_html=True,
                )
        return True

    with st.container(key="card_paywall"):
        st.markdown(
            '<div class="wall"><div class="wall-ico">🔒</div><div>'
            '<p class="wall-title">🔒 CSV/Excel Export is Locked.</p>'
            '<p class="wall-text">Unlock unlimited lifetime downloads for a one-time fee of '
            f'<b>$49</b> at <a href="{SETTINGS.checkout_url}" target="_blank">b2bprospects.io</a></p>'
            "</div></div>"
            '<div class="wall-badges"><span>✓ One-time payment</span><span>✓ Lifetime access</span>'
            "<span>✓ CSV + Excel exports</span></div>",
            unsafe_allow_html=True,
        )
        with st.form(form_key, border=False):
            k1, k2 = st.columns([3, 1.2], vertical_alignment="bottom")
            entered = k1.text_input(
                "Enter License Key to Unlock Download",
                type="password",
                placeholder="Paste your license key",
            )
            submitted = k2.form_submit_button("🔓 Unlock Download", type="primary", use_container_width=True)
        if submitted:
            import hmac

            if hmac.compare_digest(entered.strip().encode(), current_license_key().encode()):
                st.session_state["unlocked"] = True
                st.rerun()
            else:
                st.error("❌ Invalid license key. Please check it and try again.")
    return False
