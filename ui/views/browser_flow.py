"""Browser extraction mode.

The operator never sees a selector. They can either click a value in the rendered
page snapshot (visual selection) or choose the column whose example values look
right — or accept the auto-mapping — then run.

Steps 5 and 6 reuse the shared quality and preview components, so both extraction
modes end in the same review-and-export screen.
"""
from __future__ import annotations

import hashlib

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
from core.browser.structure import suggested_extra_label
from core.models import FIELD_BY_KEY, header_for
from core.normalize import clean_text, slugify
from ui.components import count_pill, kv, metric_card, metric_grid, notes, section_header
from ui.visual_picker import map_selection, render_picker, selection_description, snapshot_message
from ui.views.result_view import render_preview, render_quality

STATE_INSPECTION = "x_browser_inspection"
STATE_HTML = "x_browser_html"
STATE_PICKER_SEQ = "bm_picker_last_seq"
STATE_PICKER_NONCE = "bm_picker_nonce"
STATE_EXTRA_COLUMNS = "bm_extra_columns"
STATE_REMOVED_EXTRAS = "bm_removed_extras"
STATE_VISUAL_NOTE = "bm_visual_note"

# Fields that can be mapped from a detected column.
MAPPABLE = (
    "agent_name", "brokerage_name", "phone", "email",
    "street_address", "city", "state", "postal_code", "country", "website", "profile_url",
)

_CUSTOM = "__custom__"


def _label_for(column) -> str:
    sample = column.sample_values[0] if column.sample_values else ""
    if len(sample) > 38:
        sample = sample[:35] + "…"
    sample = sample.replace('"', "'")
    return f"Column {column.index + 1} — “{sample}”" if sample else f"Column {column.index + 1}"


def _clear_field_state() -> None:
    """Clear field mappings without touching the structure selector or snapshot."""
    for key in list(st.session_state.keys()):
        if str(key).startswith(("bm_field_", "bm_extra_", "bm_visual_")):
            st.session_state.pop(key, None)
    st.session_state.pop(STATE_EXTRA_COLUMNS, None)
    st.session_state.pop(STATE_REMOVED_EXTRAS, None)
    st.session_state.pop(STATE_VISUAL_NOTE, None)
    st.session_state.pop(STATE_PICKER_SEQ, None)


def _clear_mapping_state() -> None:
    _clear_field_state()
    for key in ("bm_structure", "bm_mapping_structure", STATE_PICKER_NONCE):
        st.session_state.pop(key, None)


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
            for key in (STATE_INSPECTION, STATE_HTML, "x_run"):
                st.session_state.pop(key, None)
            _clear_mapping_state()
            try:
                with st.spinner("Opening the page and looking for repeating records…"):
                    report = inspect_in_browser(url)
                st.session_state[STATE_INSPECTION] = report
                st.session_state[STATE_HTML] = report.rendered_html
                digest = hashlib.sha1(report.rendered_html.encode("utf-8")).hexdigest()[:12]
                st.session_state[STATE_PICKER_NONCE] = f"{report.final_url}-{digest}"
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
            + metric_card("🧩", "Embedded data", str(report.embedded_record_count or len(report.embedded_keys)),
                          ", ".join(report.embedded_payloads or report.embedded_keys[:2]) or "none found", "#8b5cf6")
            + metric_card("🪟", "Rendered by", "Browser" if report.rendered else "HTTP",
                          "JavaScript executed" if report.rendered else "", "#f59e0b")
        )
        kv([
            ("Page title", report.title or "—"),
            ("Final URL", report.final_url or report.url),
            ("Detected layout", report.structure.row_selector if report.structure else "not detected"),
            ("Pagination detail", pagination.get("reason", "—")),
            ("robots.txt", report.robots_detail or "not checked"),
        ])
        if getattr(report, "detected_layers", None):
            st.caption("Extraction layers available: " + " · ".join(report.detected_layers))
        suggested = getattr(report, "suggested_mapping", {}) or {}
        if suggested:
            labels = [
                FIELD_BY_KEY[key].label if key in FIELD_BY_KEY else header_for(key)
                for key in suggested
            ]
            st.caption("Suggested fields: " + ", ".join(labels[:12]))
        samples = getattr(report, "sample_records", None) or []
        if samples:
            with st.expander("Preview of detected records", expanded=False):
                frame = pd.DataFrame(samples)
                frame = frame.rename(columns={
                    key: (FIELD_BY_KEY[key].label if key in FIELD_BY_KEY else header_for(key))
                    for key in frame.columns
                })
                st.dataframe(frame, use_container_width=True, hide_index=True)
                st.caption(
                    "These values come from the detected structure or embedded data. Nothing has "
                    "been extracted across pages yet."
                )
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


# ---------------------------------------------------------------------------
# Visual selection
# ---------------------------------------------------------------------------
def _render_visual_picker(structure, html: str, page_url: str) -> None:
    if structure is None or not html:
        return
    with st.container(key="card_browser_visual"):
        section_header(
            "Step 3b",
            "Select fields visually",
            "Choose the field you want to map, then click one example of that value in the page "
            "below. The picker recognises the repeating row and highlights its siblings. "
            "No CSS or selectors are involved.",
        )

        options = [key for key in MAPPABLE if key in FIELD_BY_KEY] + [_CUSTOM]
        selected_field = st.selectbox(
            "Field to map",
            options,
            index=0,
            format_func=lambda key: FIELD_BY_KEY[key].label if key in FIELD_BY_KEY else "Custom field…",
            key="bm_visual_field",
        )
        if selected_field == _CUSTOM:
            custom_name = st.text_input(
                "Name for the custom column",
                value=st.session_state.get("bm_visual_custom_name", ""),
                placeholder="e.g. License number, Title, Languages",
                key="bm_visual_custom_name",
            )
            field_label = clean_text(custom_name) or "Custom field"
        else:
            custom_name = ""
            field_label = FIELD_BY_KEY[selected_field].label

        nonce = str(st.session_state.get(STATE_PICKER_NONCE) or page_url)
        message = snapshot_message(html, page_url)
        if message:
            st.caption(message)
            return

        selection = render_picker(html, page_url, selected_field, field_label, nonce)
        if not selection:
            note = st.session_state.get(STATE_VISUAL_NOTE)
            if note:
                st.success(note)
            return

        seq = str(selection.get("seq") or "")
        if not seq or seq == st.session_state.get(STATE_PICKER_SEQ):
            note = st.session_state.get(STATE_VISUAL_NOTE)
            if note:
                st.success(note)
            return
        st.session_state[STATE_PICKER_SEQ] = seq

        if selection.get("reset"):
            st.session_state.pop(STATE_VISUAL_NOTE, None)
            return

        column_index = map_selection(structure, selection, html)
        if column_index is None:
            st.warning(
                "That element did not fall inside one of the detected record columns. "
                "Try clicking the text itself (a name, phone or email) rather than whitespace."
            )
            return

        field_key = selected_field
        if field_key == _CUSTOM:
            field_key = slugify(custom_name or selection.get("text") or f"extra_{column_index}")

        if field_key in FIELD_BY_KEY:
            st.session_state[f"bm_field_{field_key}"] = column_index
            note = f"Mapped {field_label} to Column {column_index + 1} — {selection_description(selection)}"
        else:
            extras = st.session_state.setdefault(STATE_EXTRA_COLUMNS, {})
            fallback_label = (
                clean_text(custom_name)
                or clean_text(selection.get("text"))[:40]
                or f"Extra column {column_index + 1}"
            )
            extras[field_key] = {
                "label": fallback_label,
                "column": column_index,
            }
            st.session_state[f"bm_field_{field_key}"] = column_index
            note = (
                f"Added “{extras[field_key]['label']}” from Column {column_index + 1} — "
                f"{selection_description(selection)}"
            )

        st.session_state[STATE_VISUAL_NOTE] = note
        st.rerun()


# ---------------------------------------------------------------------------
# Manual mapping
# ---------------------------------------------------------------------------
def _render_mapping(structure, html: str, page_url: str) -> FieldMapping:
    suggested = suggest_mapping(structure)
    options = [None] + [c.index for c in structure.columns]
    samples = {c.index: _label_for(c) for c in structure.columns}

    with st.container(key="card_browser_fields"):
        section_header(
            "Step 4",
            "Choose what to collect",
            "Each field is filled from one detected column. The auto-selection is a best guess — "
            "check a few example values and change any that look wrong. Anything you picked "
            "visually is already applied.",
        )

        chosen: dict[str, int] = {}
        used_columns: set[int] = set()

        columns = st.columns(2)
        for position, key in enumerate(MAPPABLE):
            spec = FIELD_BY_KEY.get(key)
            if spec is None:
                continue
            current = st.session_state.get(f"bm_field_{key}", suggested.column_for(key))
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
                used_columns.add(selection)

        # Extra columns are detected automatically (job title, second phone/email,
        # separate last-name column). They still appear here so the operator can
        # change or remove them before extraction.
        extras: dict[str, dict] = st.session_state.get(STATE_EXTRA_COLUMNS, {})
        removed_extras = set(st.session_state.get(STATE_REMOVED_EXTRAS, []))
        for extra_key, column_index in suggested.columns.items():
            if extra_key in FIELD_BY_KEY or extra_key in extras or extra_key in removed_extras:
                continue
            extras[extra_key] = {
                "label": header_for(extra_key),
                "column": column_index,
                "auto": True,
            }
        if extras:
            st.session_state[STATE_EXTRA_COLUMNS] = extras
        for extra_key, info in list(extras.items()):
            current = st.session_state.get(f"bm_field_{extra_key}", info.get("column"))
            try:
                default_index = options.index(current) if current in options else 0
            except ValueError:
                default_index = 0
            label = str(info.get("label") or extra_key)
            e1, e2 = st.columns([5, 1], vertical_alignment="bottom")
            with e1:
                selection = st.selectbox(
                    f"{label} · extra column",
                    options=options,
                    index=default_index,
                    format_func=lambda value: "— do not collect —" if value is None else samples.get(value, f"Column {value + 1}"),
                    key=f"bm_field_{extra_key}",
                )
            with e2:
                remove = st.button("Remove", key=f"bm_extra_remove_{extra_key}", use_container_width=True)
            if remove:
                extras.pop(extra_key, None)
                if info.get("auto"):
                    removed_extras.add(extra_key)
                    st.session_state[STATE_REMOVED_EXTRAS] = sorted(removed_extras)
                st.session_state.pop(f"bm_field_{extra_key}", None)
                st.session_state.pop(f"bm_extra_name_{info.get('column')}", None)
                st.rerun()
            if selection is not None:
                chosen[extra_key] = selection
                used_columns.add(selection)

        if any(info.get("auto") for info in extras.values()):
            st.caption(
                "Additional columns were detected automatically (for example a job title or a "
                "second phone number). They are exported alongside the standard fields; change "
                "or remove any you do not need."
            )

        unused = [c.index for c in structure.columns if c.index not in used_columns]
        if unused:
            with st.expander("Add another detected column as a custom field", expanded=False):
                picks = st.multiselect(
                    "Detected columns to add",
                    options=unused,
                    format_func=lambda value: samples.get(value, f"Column {value + 1}"),
                    key="bm_extra_pick",
                    help="Use this for titles, licence numbers, languages, MLS IDs or any other value the page publishes.",
                )
                added = False
                for index in picks:
                    name_key = f"bm_extra_name_{index}"
                    default_name = st.session_state.get(name_key) or suggested_extra_label(structure.column(index))
                    name = st.text_input(
                        f"Name for Column {index + 1}",
                        value=default_name,
                        key=name_key,
                    )
                    key = slugify(name or f"extra_{index}")
                    if key and key not in extras:
                        extras[key] = {"label": clean_text(name) or f"Extra column {index + 1}", "column": index}
                        added = True
                st.session_state[STATE_EXTRA_COLUMNS] = extras
                if added:
                    st.rerun()

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
                frame = pd.DataFrame(rows[:5])
                frame = frame.drop(
                    columns=[column for column in frame.columns if str(column).startswith("_")],
                    errors="ignore",
                )
                extras = st.session_state.get(STATE_EXTRA_COLUMNS, {})
                frame = frame.rename(columns={
                    key: (FIELD_BY_KEY[key].label if key in FIELD_BY_KEY
                          else extras.get(key, {}).get("label") or header_for(key))
                    for key in frame.columns
                })
                st.dataframe(frame, use_container_width=True, hide_index=True)
                st.caption(f"{len(rows)} records would be taken from this page.")
    return mapping


def _render_embedded_run(url: str, report) -> None:
    """Extraction path for pages whose records exist only in embedded JSON."""
    with st.container(key="card_browser_embedded"):
        section_header(
            "Step 4",
            "Records found in embedded data",
            "This page has no repeating DOM cards, but it ships the records as JSON "
            f"({', '.join(report.embedded_payloads or report.embedded_keys[:2]) or 'payload'}). "
            "The engine can read that payload directly, then follow pagination and optionally "
            "open each profile.",
        )
        kv([
            ("Page title", report.title or "—"),
            ("Final URL", report.final_url or report.url),
            ("Embedded records", str(report.embedded_record_count)),
            ("Pagination", str((report.pagination or {}).get("strategy", "none"))),
        ])
        c1, c2, c3 = st.columns(3, vertical_alignment="bottom")
        max_pages = c1.number_input("Maximum pages", min_value=1, max_value=100,
                                    value=int(st.session_state.get("bm_pages", 5)), key="bm_emb_pages")
        max_records = c2.number_input("Maximum records", min_value=1, max_value=20000,
                                      value=int(st.session_state.get("bm_records", 500)), key="bm_emb_records")
        enrich = c3.checkbox("Read profile pages", value=False, key="bm_emb_enrich")
        run_clicked = st.button("⚡ Extract embedded records", type="primary",
                                use_container_width=True, key="bm_emb_run")
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
                structure=None,
                mapping=None,
                mode="embedded",
                max_pages=int(max_pages),
                max_records=int(max_records),
                enrich_profiles=bool(enrich),
                max_profiles=int(st.session_state.get("bm_profiles", 25)),
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


def _render_run(url: str, structure, mapping: FieldMapping) -> None:
    with st.container(key="card_browser_run"):
        section_header(
            "Step 5",
            "Extract",
            "The browser will move through the listing using the detected navigation. "
            "Requests are spaced out, robots.txt is reported, and the run stops at the first "
            "access denial.",
        )

        c1, c2, c3, c4 = st.columns(4, vertical_alignment="bottom")
        # Auto covers numbered buttons and query-parameter pages. The manual
        # choices are the styles that can be driven with the detected selector.
        strategy_labels = {
            "Auto (detected)": "auto",
            "Next button": "next_button",
            "Numbered links": "numbered",
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
                             help="Opens each profile for phone/email and extra detail. Slower; capped below.")

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
    if structure is not None:
        signature = structure.signature or structure.row_selector or str(structure.row_count)
        if st.session_state.get("bm_mapping_structure") != signature:
            _clear_field_state()
            st.session_state["bm_mapping_structure"] = signature
    if structure is None:
        embedded_count = getattr(report, "embedded_record_count", 0) or 0
        if embedded_count >= 3:
            _render_embedded_run(url, report)
            result = st.session_state.get("x_run")
            if result is not None:
                render_quality(result)
                render_preview(result)
        elif getattr(report, "embedded_keys", None):
            st.info(
                "An embedded JSON payload was found, but it contained fewer than three usable "
                "records. Use the automatic method, or inspect the page manually."
            )
        else:
            st.warning(
                "No repeating records or embedded payload were found on this page. It may be a "
                "search form or a single-profile page. Try the roster URL, or add a site adapter."
            )
        return

    html = st.session_state.get(STATE_HTML) or ""
    if not html:
        st.info("The rendered page is no longer in memory. Analyse again to map fields.")
        return

    _render_visual_picker(structure, html, report.final_url or url)
    mapping = _render_mapping(structure, html, report.final_url or url)
    _render_run(url, structure, mapping)

    result = st.session_state.get("x_run")
    if result is None:
        return
    render_quality(result)
    render_preview(result)
