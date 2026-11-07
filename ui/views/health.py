"""Adapter health — the maintenance dashboard.

One row per site + adapter, with the same OK / WATCH / BROKEN classification the
engine uses. This is where a redesigned brokerage site becomes visible before it
reaches a customer.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core.runs import clear_history, health_dashboard, load_history, prune_history
from core.settings import SETTINGS
from ui.components import health_badge, kv, metric_card, metric_grid, notes, section_header


def render() -> None:
    rows = health_dashboard()

    with st.container(key="card_health_head"):
        section_header(
            "Maintenance",
            "Adapter health",
            "Every run is compared with the recent baseline for the same site. "
            "A redesign that halves the record count shows up here instead of in a customer's inbox.",
        )
        if not rows:
            st.info(
                "No runs recorded yet. Run an extraction and this page starts tracking that site."
            )
            _render_thresholds()
            return

        ok = sum(1 for r in rows if r["status"] in ("ok", "first_run"))
        watch = sum(1 for r in rows if r["status"] == "watch")
        broken = sum(1 for r in rows if r["status"] == "broken")
        metric_grid(
            metric_card("✅", "Healthy sites", str(ok), "no drift detected", "#10b981")
            + metric_card("⚠️", "Needs review", str(watch), "volume or field coverage drifted", "#f59e0b")
            + metric_card("⛔", "Broken", str(broken), "adapter likely needs a fix", "#dc2626")
            + metric_card("🗂️", "Sites tracked", str(len(rows)), "distinct host + adapter", "#5b2fd6")
        )

    with st.container(key="card_health_table"):
        frame = pd.DataFrame(
            [
                {
                    "Status": r["status"].upper(),
                    "Site": r["host"],
                    "Adapter": r["adapter_label"] or r["adapter_id"],
                    "Last run (UTC)": r["last_run"],
                    "Records": r["records"],
                    "Baseline": r["baseline_records"] if r["baseline_records"] is not None else "—",
                    "Success": f"{r['success_rate']:.0%}" if isinstance(r["success_rate"], (int, float)) else "—",
                    "Markup changed": "yes" if r["structure_changed"] else "no",
                }
                for r in rows
            ]
        )
        st.dataframe(frame, use_container_width=True, hide_index=True, height=min(460, 38 * (len(frame) + 1) + 3))

        attention = [r for r in rows if r["status"] in ("watch", "broken")]
        for row in attention:
            st.markdown(
                f"**{row['host']}** &nbsp; {health_badge(row['status'])}",
                unsafe_allow_html=True,
            )
            notes(row["reasons"][:5], error=row["status"] == "broken")

    _render_thresholds()

    with st.container(key="card_health_maint"):
        section_header("Housekeeping", "Run data", "History is stored locally as JSON lines.")
        c1, c2, c3 = st.columns(3)
        if c1.button("🧹 Prune old runs", use_container_width=True, key="health_prune"):
            ok_prune, message = prune_history()
            (st.success if ok_prune else st.error)(message)
        if c2.button("🗑️ Clear history", use_container_width=True, key="health_clear"):
            ok_clear, message = clear_history()
            (st.success if ok_clear else st.error)(message)
        entries = load_history()
        c3.download_button(
            "⬇️ Export history (JSON)",
            data=pd.Series(entries).to_json(orient="records", indent=2).encode("utf-8"),
            file_name="b2bprospects_run_history.json",
            mime="application/json",
            use_container_width=True,
            disabled=not entries,
            key="health_export",
        )


def _render_thresholds() -> None:
    with st.expander("How a site is classified", expanded=False):
        kv([
            ("Broken", f"zero records, more than {SETTINGS.record_drop_broken:.0%} fewer records than baseline, "
                       "missing names on most rows, or the site refused access"),
            ("Needs review", f"{SETTINGS.record_drop_watch:.0%}–{SETTINGS.record_drop_broken:.0%} fewer records, "
                             f"a field's coverage down {SETTINGS.fill_drop_points:.0%} or more, or the listing markup changed"),
            ("Markup signature", "hash of the listing container and row markup, excluding row counts, "
                                 "so legitimate hiring does not trigger a warning"),
            ("Baseline", "median of the last five runs that produced records"),
        ])
