"""Quality summary and record preview, shared by both extraction modes."""
from __future__ import annotations

import re

import streamlit as st

from core.exporters import basename_for, records_to_frame, to_csv_bytes, to_excel_bytes
from core.models import FIELD_BY_KEY
from ui.components import (
    count_pill,
    health_badge,
    kv,
    metric_card,
    metric_grid,
    notes,
    render_export_gate,
    status_bar,
)

_LEVEL_TONE = {"ok": "ok", "watch": "watch", "broken": "broken", "first_run": "watch"}


def render_quality(result) -> None:
    quality = result.quality
    health = result.health

    with st.container(key="card_quality"):
        st.markdown('<p class="sec-eyebrow">Step 5</p>', unsafe_allow_html=True)
        st.markdown('<p class="sec-title"><span class="stepnum">5</span>Data quality</p>', unsafe_allow_html=True)
        st.markdown(
            f'<p class="sec-sub">Run {result.run_id} · {result.elapsed_seconds:.0f}s · '
            f'{result.pages_fetched} listing page(s) · {result.profiles_attempted} profile(s) attempted</p>',
            unsafe_allow_html=True,
        )

        status_bar(
            f"{health_badge(health.status)} &nbsp; "
            f"<b>{quality.unique}</b> records · <b>{quality.success_rate:.0%}</b> success rate",
            f"{result.adapter_label} · {result.inspection.host}",
            tone=_LEVEL_TONE.get(health.status, "watch"),
        )

        contact_share = (quality.with_any_contact / quality.unique) if quality.unique else 0.0
        metric_grid(
            metric_card("📇", "Records", str(quality.unique),
                        f"{quality.duplicates_removed} duplicate(s) removed", "#5b2fd6")
            + metric_card("✅", "Success rate", f"{quality.success_rate:.0%}",
                          f"{quality.attempted} attempted", "#10b981")
            + metric_card("📞", "Contact coverage", f"{contact_share:.0%}",
                          f"{quality.with_any_contact} with phone or email", "#8b5cf6")
            + metric_card("⚠️", "Flagged rows", str(quality.missing_names),
                          "missing an agent name", "#f59e0b")
        )

        if quality.field_fill:
            rows = [
                (FIELD_BY_KEY[key].label if key in FIELD_BY_KEY else key, f"{rate:.0%}")
                for key, rate in quality.field_fill.items()
            ]
            kv(rows)

        warnings = [*health.reasons, *quality.warnings]
        if warnings:
            notes(warnings)
        if result.errors:
            notes(result.errors[:8], error=True)


# ---------------------------------------------------------------------------
# Step 6
# ---------------------------------------------------------------------------
def render_preview(result) -> None:
    frame = records_to_frame(result.records, result.selected_fields)

    with st.container(key="card_table"):
        st.markdown(
            '<div class="tbl-head"><div><p class="sec-eyebrow">Step 6</p>'
            '<p class="sec-title"><span class="stepnum">6</span>Preview and export</p></div>'
            + count_pill(f"{len(frame)} records")
            + "</div>",
            unsafe_allow_html=True,
        )

        if frame.empty:
            st.warning("This run produced no records. See the warnings above before trying again.")
            return

        query = st.text_input("Search records", placeholder="Filter by name, city, phone…", key="x_search")
        view = frame
        if query.strip():
            needle = query.strip().lower()
            mask = frame.astype(str).apply(
                lambda row: row.str.lower().str.contains(re.escape(needle), regex=True).any(), axis=1
            )
            view = frame[mask]
            st.caption(f"{len(view)} of {len(frame)} records match “{query.strip()}”.")

        st.dataframe(
            view,
            use_container_width=True,
            hide_index=True,
            height=min(560, 38 * (len(view) + 1) + 3),
            column_config={
                "Agent name": st.column_config.TextColumn("Agent name", width="medium"),
                "Brokerage name": st.column_config.TextColumn("Brokerage", width="medium"),
                "Public phone": st.column_config.TextColumn("Phone", width="small"),
                "Public email": st.column_config.TextColumn("Email", width="medium"),
                "Office address": st.column_config.TextColumn("Address", width="large"),
                "City": st.column_config.TextColumn("City", width="small"),
                "State / region": st.column_config.TextColumn("State", width="small"),
                "Postal code": st.column_config.TextColumn("Postal", width="small"),
                "Profile URL": st.column_config.LinkColumn("Profile", display_text="Open ↗", width="small"),
                "Source URL": st.column_config.LinkColumn("Source", display_text="Open ↗", width="small"),
            },
        )

        allowed = render_export_gate("license_form_extract")
        if not allowed:
            return

        basename = basename_for(result)
        d1, d2, _ = st.columns([1.2, 1.2, 4])
        d1.download_button(
            "⬇️ Download CSV",
            data=to_csv_bytes(result.records, result.selected_fields),
            file_name=f"{basename}.csv",
            mime="text/csv",
            use_container_width=True,
            key="x_csv",
        )
        try:
            excel = to_excel_bytes(result, result.selected_fields)
        except Exception as exc:
            d2.button("⬇️ Download Excel", disabled=True, use_container_width=True, key="x_xlsx_disabled")
            st.caption(f"Excel export failed: {exc.__class__.__name__}: {exc}")
        else:
            d2.download_button(
                "⬇️ Download Excel",
                data=excel,
                file_name=f"{basename}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="x_xlsx",
            )

        st.markdown(
            '<p class="prov">The workbook includes a Run Summary sheet with the source URL, timestamp, '
            "adapter, field coverage and every warning from this run.</p>",
            unsafe_allow_html=True,
        )


