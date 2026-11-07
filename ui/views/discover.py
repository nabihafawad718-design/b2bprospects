"""Local Business Discovery — the original dashboard, preserved.

Same controls, same filters, same table, same export behaviour. Delivered as its
own view so the new extraction workflow can sit alongside it without replacing it.
"""
from __future__ import annotations

import io
import re

import pandas as pd
import streamlit as st

from core.discovery import (
    GeocodeError,
    LocationNotFound,
    OverpassError,
    apply_filters,
    fetch_leads,
    opportunity_masks,
    sanitize_term,
)
from ui.components import (
    count_pill,
    metric_card,
    metric_grid,
    render_export_gate,
    section_header,
    status_bar,
)


def _to_excel_bytes(df: pd.DataFrame) -> bytes | None:
    try:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Leads")
        return buffer.getvalue()
    except Exception:
        return None


def render(max_results: int) -> None:
    with st.container(key="card_search"):
        section_header(
            "Step 1",
            "Search Dashboard",
            "Pick a niche and a market. We pull live data from OpenStreetMap.",
        )
        c1, c2, c3 = st.columns([3, 3, 1.5], vertical_alignment="bottom")
        keyword = c1.text_input("Target Industry / Keyword", placeholder="e.g. plumbers, dentists, roofing")
        location = c2.text_input("Target Location", placeholder="e.g. Houston, Texas")
        search_clicked = c3.button("🔍 Find Prospects", type="primary", use_container_width=True, key="disc_search")

    if search_clicked:
        keyword, location = keyword.strip(), location.strip()
        if not keyword or not location:
            st.warning("Enter both a Target Industry / Keyword and a Target Location.")
        elif not sanitize_term(keyword):
            st.warning("The keyword needs at least one letter or number, for example “plumbers”.")
        else:
            try:
                with st.spinner("Searching OpenStreetMap and checking a limited number of public business websites..."):
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

    leads: pd.DataFrame | None = st.session_state.get("leads")

    if leads is None:
        with st.container(key="card_empty"):
            section_header(
                "Getting started",
                "Your prospect pipeline is empty",
                "Three quick steps to your first list of agency-ready leads.",
            )
            st.markdown(
                '<div class="steps">'
                '<div class="step"><div class="step-n">1</div><div class="step-t">Choose your niche</div>'
                '<div class="step-d">Type an industry such as plumbers, dentists, roofing or restaurants.</div></div>'
                '<div class="step"><div class="step-n">2</div><div class="step-t">Pick a market</div>'
                '<div class="step-d">Enter a city and state or country, then hit Find Prospects. No API key or billing needed.</div></div>'
                '<div class="step"><div class="step-n">3</div><div class="step-t">Filter and export</div>'
                '<div class="step-d">Isolate no-website, social-only and incomplete listings, then export.</div></div>'
                "</div>",
                unsafe_allow_html=True,
            )
        return

    total = len(leads)
    m_site, m_social, m_incomplete = opportunity_masks(leads)

    score = m_site.astype(int) + m_social.astype(int) + m_incomplete.astype(int)
    high_value = int((score >= 2).sum())
    quick_wins = int((m_site & leads["Phone Number"].notna()).sum())
    with_email = int(leads["Email"].notna().sum())

    def pct(n: int) -> str:
        return f"{(n / total * 100):.0f}%" if total else "0%"

    status_bar(
        f'<b>{total}</b> prospects loaded for <b>{st.session_state.get("meta", "")}</b>',
        "Source: OpenStreetMap via Overpass API · results cached for 60 minutes",
    )
    metric_grid(
        metric_card("📇", "Total Leads Found", str(total), "Named businesses from OpenStreetMap", "#5b2fd6")
        + metric_card("💎", "High-Value Leads", str(high_value), f"<b>{pct(high_value)}</b> match 2+ agency criteria", "#8b5cf6")
        + metric_card("⚡", "Quick Wins", str(quick_wins), f"<b>{pct(quick_wins)}</b> have no website and a phone number", "#10b981")
        + metric_card("✉️", "Emails Found", str(with_email), f"<b>{pct(with_email)}</b> list a public email address", "#f59e0b")
    )

    with st.container(key="card_filters"):
        section_header("Step 2", "Advanced Agency Filters", "Isolate the leads that map directly to your services.")
        fc1, fc2, fc3, fc4 = st.columns(4)

        with fc1:
            with st.container(key="chip_site"):
                f_no_site = st.checkbox("🌐 No Website", key="d_no_site")
                st.markdown(
                    f'<div class="chip-sub">Web design clients<br><b>{int(m_site.sum())}</b> leads match</div>',
                    unsafe_allow_html=True,
                )
        with fc2:
            with st.container(key="chip_social"):
                f_social = st.checkbox("📱 Social-Media-Only Site", key="d_social")
                st.markdown(
                    f'<div class="chip-sub">Website rebuild clients<br><b>{int(m_social.sum())}</b> leads match</div>',
                    unsafe_allow_html=True,
                )
        with fc3:
            with st.container(key="chip_incomplete"):
                f_incomplete = st.checkbox("📇 Incomplete Listing", key="d_incomplete")
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
                    key="d_mode",
                )

    filtered = apply_filters(leads, f_no_site, f_social, f_incomplete, f_mode)

    with st.container(key="card_table"):
        st.markdown(
            '<div class="tbl-head"><div><p class="sec-eyebrow">Step 3</p>'
            '<p class="sec-title">Prospect Database</p></div>'
            + count_pill(f"Showing {len(filtered)} of {total} leads")
            + "</div>",
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

    if render_export_gate("license_form_discover") and not filtered.empty:
        slug = re.sub(r"[^a-z0-9]+", "_", st.session_state.get("meta", "").lower()).strip("_") or "leads"
        d1, d2, _ = st.columns([1.2, 1.2, 4])
        d1.download_button(
            "⬇️ Download CSV",
            data=filtered.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"b2bprospects_{slug}.csv",
            mime="text/csv",
            use_container_width=True,
            key="disc_csv",
        )
        xlsx = _to_excel_bytes(filtered)
        if xlsx:
            d2.download_button(
                "⬇️ Download Excel",
                data=xlsx,
                file_name=f"b2bprospects_{slug}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="disc_xlsx",
            )
        else:
            st.caption("Excel export unavailable — openpyxl is not installed. CSV still works.")
