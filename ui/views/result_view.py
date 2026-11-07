"""Quality summary, cleaning and record preview/export.

Shared by both extraction modes. The cleaning controls operate on copies of the
run's records, so the original extraction remains intact and every export can be
traced back to it.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core.cleaning import (
    DEDUPE_STRATEGIES,
    FilterSpec,
    available_extra_keys,
    coverage,
    dedupe_records,
    filter_records,
    filter_summary,
    issue_rows,
    sort_records,
)
from core.exporters import (
    basename_for,
    records_to_frame,
    to_csv_bytes,
    to_excel_bytes,
    to_json_bytes,
)
from core.models import FIELD_BY_KEY, header_for
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
                (FIELD_BY_KEY[key].label if key in FIELD_BY_KEY else header_for(key), f"{rate:.0%}")
                for key, rate in quality.field_fill.items()
            ]
            kv(rows)
        if result.export_context:
            with st.expander("How this run was produced", expanded=False):
                kv([(key, str(value)) for key, value in result.export_context.items()])
        if result.screenshots:
            st.caption("Screenshots captured for debugging: " + ", ".join(result.screenshots))

        warnings = [*health.reasons, *quality.warnings]
        if warnings:
            notes(warnings)
        if result.errors:
            notes(result.errors[:8], error=True)


# ---------------------------------------------------------------------------
# Step 6 — clean, review, export
# ---------------------------------------------------------------------------
def _reset_filters() -> None:
    for key in ("x_search", "x_filter_phone", "x_filter_email", "x_filter_contact",
                "x_filter_name", "x_filter_city", "x_filter_state", "x_filter_flags",
                "x_filter_missing", "x_clean_dedupe", "x_clean_sort"):
        st.session_state.pop(key, None)


def render_preview(result) -> None:
    with st.container(key="card_table"):
        st.markdown(
            '<div class="tbl-head"><div><p class="sec-eyebrow">Step 6</p>'
            '<p class="sec-title"><span class="stepnum">6</span>Preview and export</p></div>'
            + count_pill(f"{len(result.records)} records") + "</div>",
            unsafe_allow_html=True,
        )

        if not result.records:
            st.warning("This run produced no records. See the warnings above before trying again.")
            return

        # A new run must not inherit field selections or filters from the previous
        # site; stale multiselect values can also be invalid for the new options.
        if st.session_state.get("x_export_run") != result.run_id:
            for key in ("x_export_fields", "x_search", "x_filter_phone", "x_filter_email",
                        "x_filter_contact", "x_filter_name", "x_filter_city", "x_filter_state",
                        "x_filter_flags", "x_filter_missing", "x_clean_dedupe", "x_clean_sort"):
                st.session_state.pop(key, None)
            st.session_state["x_export_run"] = result.run_id

        extra_keys = available_extra_keys(result.records)
        canonical_fields = [key for key in FIELD_BY_KEY if key in result.selected_fields]
        if not canonical_fields:
            canonical_fields = [
                key for key in FIELD_BY_KEY
                if any(str(getattr(record, key, "") or "").strip() for record in result.records)
            ]
        all_keys = canonical_fields + [key for key in extra_keys if key not in canonical_fields]

        with st.expander("Filters and cleaning", expanded=True):
            st.caption(
                "Phones are normalized to a consistent format, emails are lowercased, whitespace "
                "and markup are stripped, and every record keeps its source URL. These controls "
                "apply to a copy of the run."
            )
            r1, r2, r3 = st.columns([2.1, 1.5, 1.2], vertical_alignment="bottom")
            query = r1.text_input("Search records", placeholder="Filter by name, city, phone, email, extra field…",
                                  key="x_search")
            strategy = r2.selectbox(
                "Duplicate handling",
                list(DEDUPE_STRATEGIES),
                index=0,
                format_func=lambda key: DEDUPE_STRATEGIES[key],
                key="x_clean_dedupe",
            )
            sort_key = r3.selectbox(
                "Sort by",
                ["none", "agent_name", "city", "brokerage_name", "state", "email"],
                index=0,
                format_func=lambda key: "As extracted" if key == "none" else header_for(key),
                key="x_clean_sort",
            )

            f1, f2, f3, f4, f5 = st.columns(5, vertical_alignment="bottom")
            require_name = f1.checkbox("Named only", key="x_filter_name")
            require_phone = f2.checkbox("Has phone", key="x_filter_phone")
            require_email = f3.checkbox("Has email", key="x_filter_email")
            require_contact = f4.checkbox("Any contact", key="x_filter_contact")
            city = f5.text_input("City contains", key="x_filter_city")

            g1, g2, g3 = st.columns([1, 1.6, 2.4], vertical_alignment="bottom")
            state = g1.text_input("State", key="x_filter_state")
            flag_options = sorted(result.quality.issue_counts or {
                flag for record in result.records for flag in record.quality_flags
            })
            flags = g2.multiselect("Only rows flagged", options=flag_options, key="x_filter_flags")
            missing = g3.multiselect(
                "Only rows missing",
                options=[key for key in FIELD_BY_KEY if key in result.selected_fields],
                format_func=lambda key: FIELD_BY_KEY[key].label,
                key="x_filter_missing",
            )

            if st.button("Reset filters", key="x_reset_filters"):
                _reset_filters()
                st.rerun()

        spec = FilterSpec(
            query=query,
            require_name=require_name,
            require_phone=require_phone,
            require_email=require_email,
            require_contact=require_contact,
            city=city,
            state=state,
            missing_fields=list(missing),
            require_flags=list(flags),
        )
        cleaned, duplicates_removed = dedupe_records(result.records, strategy)
        filtered = filter_records(cleaned, spec)
        if sort_key != "none":
            filtered = sort_records(filtered, sort_key)

        summary_bits = [f"{len(result.records)} extracted"]
        if duplicates_removed:
            summary_bits.append(f"{duplicates_removed} duplicate(s) removed by “{DEDUPE_STRATEGIES[strategy]}”")
        if spec.active():
            summary_bits.append(f"{len(filtered)} after filters")
        st.caption(" · ".join(summary_bits))

        if not filtered:
            st.warning(
                "No records match the current filters. Reset the filters or widen the search."
            )
            return

        export_default = [key for key in all_keys]
        export_fields = st.multiselect(
            "Columns to export",
            options=all_keys,
            default=export_default,
            format_func=header_for,
            key="x_export_fields",
            help="Extra columns detected on the page appear here as well as the standard fields.",
        )
        if not export_fields:
            st.warning("Select at least one column to export.")
            return
        selected_extra = [key for key in export_fields if key not in FIELD_BY_KEY]

        frame = records_to_frame(filtered, export_fields, selected_extra)
        st.dataframe(
            frame,
            use_container_width=True,
            hide_index=True,
            height=min(560, 38 * (len(frame) + 1) + 3),
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

        issues = issue_rows(filtered)
        if issues:
            with st.expander(f"Review issues ({len(issues)} rows)", expanded=False):
                st.caption(
                    "These rows are included in the export. Use the filters above if you want a "
                    "cleaner delivery, or keep them and explain the gaps to the customer."
                )
                st.dataframe(pd.DataFrame(issues), use_container_width=True, hide_index=True)

        fill_keys = [
            key for key in export_fields
            if key in FIELD_BY_KEY or key in selected_extra
        ]
        fill = coverage(filtered, fill_keys)
        if fill:
            with st.expander("Field coverage in this export", expanded=False):
                kv([(header_for(key), f"{rate:.0%}") for key, rate in fill.items()])

        allowed = render_export_gate("license_form_extract")
        if not allowed:
            return

        basename = basename_for(result)
        context = {
            "Records extracted": str(len(result.records)),
            "Records exported": str(len(filtered)),
            "Duplicate strategy": DEDUPE_STRATEGIES[strategy],
            "Duplicates removed during cleaning": str(duplicates_removed),
            "Phone/email normalization": "applied",
            **filter_summary(len(result.records), len(filtered), spec),
        }

        d1, d2, d3, _ = st.columns([1.15, 1.15, 1.15, 3])
        d1.download_button(
            "⬇️ Download CSV",
            data=to_csv_bytes(filtered, export_fields, selected_extra),
            file_name=f"{basename}.csv",
            mime="text/csv",
            use_container_width=True,
            key="x_csv",
        )
        try:
            excel = to_excel_bytes(
                result,
                export_fields,
                extra_keys=selected_extra,
                records=filtered,
                include_issues=bool(issues),
                context=context,
            )
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
        d3.download_button(
            "⬇️ Download JSON",
            data=to_json_bytes(filtered, export_fields, selected_extra),
            file_name=f"{basename}.json",
            mime="application/json",
            use_container_width=True,
            key="x_json",
        )

        st.markdown(
            '<p class="prov">The workbook includes a Run Summary sheet with the source URL, timestamp, '
            "adapter, field coverage, filters and every warning from this run. When any row has an "
            "issue, an Issues sheet lists it too.</p>",
            unsafe_allow_html=True,
        )
