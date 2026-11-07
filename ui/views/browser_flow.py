"""Browser extraction mode.

The operator never sees a selector. They pick, for each field, the column whose
example values look right — or accept the auto-mapping — then run.

Steps 5 and 6 reuse the shared quality and preview components, so both extraction
modes end in the same review-and-export screen.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core.browser.driver import (
    INSTALL_HELP,
    BrowserBlocked,
    BrowserError,
    BrowserUnavailable,
    playwright_available,
)
from core.browser.engine import inspect_in_browser, run_browser_extraction
from core.browser.extract import FieldMapping, extract_page, suggest_mapping
from core.models import FIELD_BY_KEY
from ui.components import count_pill, kv, metric_card, metric_grid, notes, section_header
from ui.views.result_view import render_preview, render_quality

STATE_INSPECTION = "x_browser_inspection"
STATE_HTML = "x_browser_html"

# Fields that can be mapped from a detected column.
MAPPABLE = (
    "agent_name", "brokerage_name", "phone", "email",
    "street_address", "city", "state", "postal_code", "website", "profile_url",
)


def _label_for(column) -> str:
    sample = column.sample_values[0] if column.sample_values else ""
    if len(sample) > 38:
        sample = sample[:35] + "…"
    sample = sample.replace('"', "'")
    return f"Column {column.index + 1} — “{sample}”" if sample else f"Column {column.index + 1}"


def _render_inspect(url: str) -> None:
    with st.container(key="card_browser_inspect"):
        st.markdown('<p class="sec-eyebrow">Step 2</p>', unsafe_allow_html=True)
        st.markdown(
            '<p class="sec-title"><span class="stepnum">2</span>Open the page in a browser</p>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<p class="sec-sub">This renders the page the way a visitor sees it, including content '
            "built by JavaScript. Nothing is extracted yet — you will see what was found first.</p>",
            unsafe_allow_html=True,
        )

        if not playwright_available():
            st.warning(
                "The browser engine is not installed on this machine. The automatic method still "
                "works for server-rendered sites."
            )
            st.code(INSTALL_HELP, language="bash")
            return

        if st.button("🌐 Open and analyse", type="primary", use_container_width=False,
                     key="x_browser_analyse"):
            st.session_state.pop(STATE_INSPECTION, None)
            st.session_state.pop(STATE_HTML, None)
            st.session_state.pop("x_run", None)
            try:
                with st.spinner("Opening the page and looking for repeating records…"):
                    report = inspect_in_browser(url)
                st.session_state[STATE_INSPECTION] = report
                st.session_state[STATE_HTML] = report.rendered_html
            except BrowserUnavailable as exc:
                st.error(str(exc))
                st.code(INSTALL_HELP, language="bash")
            except BrowserBlocked as exc:
                st.error(str(exc))
            except BrowserError as exc:
                st.error(f"Could not open that page: {exc}")
            except Exception as exc:  # never leave a blank screen
                st.error(f"Unexpected error while analysing: {exc.__class__.__name__}: {exc}")


def _render_summary(report) -> None:
    with st.container(key="card_browser_summary"):
        section_header(
            "Step 3",
            "What was found",
            "Check these numbers against the page before extracting. A low row count usually "
            "means this is not the listing page.",
        )
        pagination = report.pagination or {}
        metric_grid(
            metric_card("📇", "Records on page", str(report.row_count),
                        "repeating rows detected", "#5b2fd6")
            + metric_card("➡️", "Pagination", str(pagination.get("strategy", "none")).replace("_", " ").title(),
                          pagination.get("reason", "")[:48], "#10b981")
            + metric_card("🧩", "Embedded data", str(len(report.embedded_keys)),
                          ", ".join(report.embedded_keys[:2]) or "none found", "#8b5cf6")
            + metric_card("🪟", "Rendered by", "Browser" if report.rendered else "HTTP",
                          "JavaScript executed" if report.rendered else "", "#f59e0b")
        )
        kv([
            ("Page title", report.title or "—"),
            ("Final URL", report.final_url or report.url),
            ("Detected layout", report.structure.row_selector if report.structure else "not detected"),
            ("Pagination detail", pagination.get("reason", "—")),
        ])
        if report.warnings:
            notes(report.warnings)


def _resolve_structure(report):
    candidates = report.candidates or ([report.structure] if report.structure else [])
    candidates = [c for c in candidates if c is not None]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    options = list(range(len(candidates)))
    choice = st.selectbox(
        "Which block of records should be used?",
        options,
        index=0,
        format_func=lambda i: (
            f"{candidates[i].row_count} rows · {candidates[i].row_selector} · "
            f"{len(candidates[i].columns)} columns"
        ),
        key="bm_structure",
        help="The first option is usually right. If the preview looks wrong, try another.",
    )
    return candidates[choice]


def _render_mapping(structure, html: str, page_url: str) -> FieldMapping:
    suggested = suggest_mapping(structure)
    options = [None] + [c.index for c in structure.columns]

    with st.container(key="card_browser_fields"):
        section_header(
            "Step 4",
            "Choose what to collect",
            "Each field is filled from one detected column. The auto-selection is a best guess — "
            "check a few example values and change any that look wrong.",
        )

        samples = {c.index: _label_for(c) for c in structure.columns}
        chosen: dict[str, int] = {}

        columns = st.columns(2)
        for position, key in enumerate(MAPPABLE):
            spec = FIELD_BY_KEY.get(key)
            if spec is None:
                continue
            current = suggested.column_for(key)
            try:
                default_index = options.index(current) if current in options else 0
            except ValueError:
                default_index = 0
            column = columns[position % 2]
            with column:
                selection = st.selectbox(
                    spec.label,
                    options=options,
                    index=default_index,
                    format_func=lambda value: "— do not collect —" if value is None else samples.get(value, f"Column {value + 1}"),
                    key=f"bm_field_{key}",
                    help=spec.description,
                )
            if selection is not None:
                chosen[key] = selection

    mapping = FieldMapping(chosen)

    # Immediate preview: proves the mapping before a full run is attempted.
    if mapping.columns:
        with st.container(key="card_browser_preview"):
            st.markdown(
                '<div class="tbl-head"><div><p class="sec-eyebrow">Check</p>'
                '<p class="sec-title">Preview of the first records</p></div>'
                + count_pill("first page only") + "</div>",
                unsafe_allow_html=True,
            )
            rows = extract_page(html, page_url, structure, mapping)
            if not rows:
                st.warning(
                    "Nothing matched this mapping. Try choosing different columns — usually the "
                    "one holding the person's name."
                )
            else:
                frame = pd.DataFrame(rows[:5]).drop(columns=["_key"], errors="ignore")
                frame = frame.rename(columns={k: FIELD_BY_KEY[k].label for k in frame.columns if k in FIELD_BY_KEY})
                st.dataframe(frame, use_container_width=True, hide_index=True)
                st.caption(f"{len(rows)} records would be taken from this page.")
    return mapping


def _render_run(url: str, structure, mapping: FieldMapping) -> None:
    with st.container(key="card_browser_run"):
        section_header(
            "Step 5",
            "Extract",
            "The browser will move through the listing using the detected navigation. "
            "Requests are spaced out, robots.txt is honoured, and the run stops at the first "
            "access denial.",
        )

        c1, c2, c3, c4 = st.columns(4, vertical_alignment="bottom")
        strategy_labels = {
            "Auto (detected)": "auto",
            "Next button": "next_button",
            "Numbered pages": "numbered",
            "Load more": "load_more",
            "Infinite scroll": "infinite_scroll",
            "Single page only": "none",
        }
        strategy = c1.selectbox("Navigation", list(strategy_labels), index=0, key="bm_strategy")
        max_pages = c2.number_input("Maximum pages", min_value=1, max_value=100,
                                    value=int(st.session_state.get("bm_pages", 5)), key="bm_pages")
        max_records = c3.number_input("Maximum records", min_value=1, max_value=20000,
                                      value=int(st.session_state.get("bm_records", 500)), key="bm_records")
        enrich = c4.checkbox("Read profile pages", value=False, key="bm_enrich",
                             help="Opens each profile for phone/email. Slower; capped below.")

        e1, e2 = st.columns([1, 3], vertical_alignment="bottom")
        max_profiles = e1.number_input("Profiles to read", min_value=1, max_value=200,
                                       value=int(st.session_state.get("bm_profiles", 25)),
                                       key="bm_profiles", disabled=not enrich)
        run_clicked = e2.button("⚡ Start browser extraction", type="primary",
                                use_container_width=True, key="bm_run",
                                disabled=not mapping.columns)

        if not run_clicked:
            return

        progress = st.progress(0.0, text="Starting browser…")
        status = st.empty()

        def on_progress(stage: str, done: int, total: int) -> None:
            progress.progress(min(1.0, done / total) if total else 0.0, text=stage)
            status.caption(stage)

        try:
            result = run_browser_extraction(
                url,
                mapping=mapping,
                structure=structure,
                strategy=strategy_labels[strategy],
                max_pages=int(max_pages),
                max_records=int(max_records),
                enrich_profiles=bool(enrich),
                max_profiles=int(max_profiles),
                progress=on_progress,
            )
        except BrowserUnavailable as exc:
            progress.empty()
            st.error(str(exc))
            st.code(INSTALL_HELP, language="bash")
            return
        except BrowserBlocked as exc:
            progress.empty()
            st.error(f"The run stopped for compliance reasons: {exc}")
            return
        except BrowserError as exc:
            progress.empty()
            st.error(f"The browser run failed: {exc}")
            return
        except Exception as exc:
            progress.empty()
            st.error(f"Extraction failed: {exc.__class__.__name__}: {exc}")
            return

        progress.progress(1.0, text="Finished")
        status.empty()
        st.session_state["x_run"] = result


def render(url: str) -> None:
    if not url:
        st.warning("Enter a directory URL in Step 1 first.")
        return

    _render_inspect(url)
    report = st.session_state.get(STATE_INSPECTION)
    if report is None:
        return

    _render_summary(report)
    structure = _resolve_structure(report)
    if structure is None:
        return

    html = st.session_state.get(STATE_HTML) or ""
    if not html:
        st.info("The rendered page is no longer in memory. Analyse again to map fields.")
        return

    mapping = _render_mapping(structure, html, report.final_url or url)
    _render_run(url, structure, mapping)

    result = st.session_state.get("x_run")
    if result is None:
        return
    render_quality(result)
    render_preview(result)
