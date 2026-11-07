"""Run history — an audit trail for every extraction delivered."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from core.runs import load_history
from ui.components import count_pill, kv, notes


def render() -> None:
    entries = load_history()

    with st.container(key="card_history"):
        st.markdown(
            '<div class="tbl-head"><div><p class="sec-eyebrow">Audit</p>'
            '<p class="sec-title">Run history</p></div>'
            + count_pill(f"{len(entries)} runs")
            + "</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            '<p class="sec-sub">Every extraction writes a summary here, including the source URL, '
            "field coverage and warnings. Keep this as your delivery record.</p>",
            unsafe_allow_html=True,
        )

        if not entries:
            st.info("No runs yet. History appears after your first extraction.")
            return

        frame = pd.DataFrame([
            {
                "Run": e.get("run_id", ""),
                "Finished (UTC)": e.get("finished_at", ""),
                "Site": e.get("url", ""),
                "Adapter": e.get("adapter_label", ""),
                "Records": e.get("records", 0),
                "Success": f"{float(e.get('success_rate') or 0):.0%}",
                "Quality": str(e.get("level", "")).upper(),
                "Health": str(e.get("health_status", "")).upper(),
            }
            for e in entries
        ])
        st.dataframe(frame, use_container_width=True, hide_index=True, height=min(460, 38 * (len(frame) + 1) + 3))

        options = {f"{e.get('run_id', '')} · {e.get('adapter_label', '')}": e for e in entries}
        choice = st.selectbox("Inspect a run", list(options), key="hist_choice")
        entry = options[choice]

        kv([
            ("Run ID", entry.get("run_id", "")),
            ("Source URL", entry.get("url", "")),
            ("Adapter", entry.get("adapter_label", "")),
            ("Started", entry.get("started_at", "")),
            ("Finished", entry.get("finished_at", "")),
            ("Duration", f"{entry.get('elapsed_seconds', 0)} s"),
            ("Records", str(entry.get("records", 0))),
            ("Duplicates removed", str(entry.get("duplicates_removed", 0))),
            ("Profiles attempted", str(entry.get("profiles_attempted", 0))),
            ("Listing pages", str(entry.get("pages_fetched", 0))),
            ("Markup signature", entry.get("structure_signature", "") or "—"),
        ])

        fill = entry.get("field_fill") or {}
        if fill:
            st.markdown("**Field coverage**")
            st.dataframe(
                pd.DataFrame([{"Field": k, "Coverage": f"{float(v):.0%}"} for k, v in fill.items()]),
                use_container_width=True, hide_index=True,
            )

        warnings = entry.get("warnings") or []
        if warnings:
            notes(warnings)
        errors = entry.get("errors") or []
        if errors:
            notes(errors, error=True)

        st.download_button(
            "⬇️ Download this run record (JSON)",
            data=json.dumps(entry, indent=2, ensure_ascii=False).encode("utf-8"),
            file_name=f"{entry.get('run_id', 'run')}.json",
            mime="application/json",
            key="hist_download",
        )
