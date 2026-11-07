"""Directory Extraction — the six-step workflow.

Step 1 enter URL → Step 2 inspect → Step 3 choose fields → Step 4 extract →
Step 5 quality → Step 6 preview and export.

Every card uses the existing `card_*` container styling, so this reads as the same
product rather than a bolted-on screen.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core.adapters.registry import resolve_adapter
from core.engine import run_extraction
from core.inspect_site import inspect_url
from core.models import CORE_FIELD_KEYS, FIELD_CATALOGUE, FIELD_BY_KEY, header_for
from core.net import BlockedError, FetchError, PoliteFetcher, RobotsDisallowed
from core.settings import SETTINGS
from ui.views.browser_flow import render as render_browser_flow
from ui.views.result_view import render_preview, render_quality
from ui.components import (
    kv,
    metric_card,
    metric_grid,
    notes,
    section_header,
)

_LEVEL_TONE = {"ok": "ok", "watch": "watch", "broken": "broken", "first_run": "watch"}


def _reset_run() -> None:
    st.session_state.pop("x_run", None)


def _clear_field_state() -> None:
    """A new site deserves fresh defaults; stale checkbox values are misleading."""
    for spec in FIELD_CATALOGUE:
        st.session_state.pop(f"x_field_{spec.key}", None)
    st.session_state.pop("x_fields", None)


def _init_widget_state() -> None:
    st.session_state.setdefault("x_url_input", st.session_state.get("x_url", ""))
    st.session_state.setdefault("x_pages_input", min(5, SETTINGS.max_list_pages))
    st.session_state.setdefault("x_profiles_input", min(100, SETTINGS.max_profiles))


# ---------------------------------------------------------------------------
# Step 1 + 2
# ---------------------------------------------------------------------------
def _render_url_and_inspection() -> None:
    """Render Step 1 and, in automatic mode, Step 2. Returns the reading mode."""
    with st.container(key="card_url"):
        st.markdown('<p class="sec-eyebrow">Step 1</p>', unsafe_allow_html=True)
        st.markdown('<p class="sec-title"><span class="stepnum">1</span>Brokerage directory URL</p>', unsafe_allow_html=True)
        st.markdown(
            '<p class="sec-sub">Paste the public page that lists your agents. It should be the roster '
            "page, not an individual profile.</p>",
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns([4, 1.3], vertical_alignment="bottom")
        url = c1.text_input(
            "Directory URL",
            placeholder="https://www.example-brokerage.com/agents",
            key="x_url_input",
        )
        inspect_clicked = c2.button(
            "🔎 Inspect website", type="primary", use_container_width=True, key="x_inspect",
            disabled=st.session_state.get("x_mode", "Automatic") == "Browser",
        )

        mode = st.radio(
            "How should the page be read?",
            ["Automatic", "Browser"],
            index=0,
            horizontal=True,
            key="x_mode",
            help=(
                "Automatic reads the HTML the server sends and is faster. Browser opens the page "
                "in a real browser, which is required when the listings are built by JavaScript."
            ),
        )
        if mode == "Automatic":
            st.caption("Automatic: fast, no browser needed. Choose Browser if you see no records.")
        else:
            st.caption(
                "Browser: renders JavaScript and can follow pagination. Slower, and the browser "
                "must be installed on this machine."
            )

    if mode == "Browser":
        return

    if inspect_clicked:
        target = (url or "").strip()
        _reset_run()
        _clear_field_state()
        st.session_state.pop("x_inspection", None)
        st.session_state["x_url"] = target
        if not target:
            st.warning("Enter a directory URL first.")
            return
        try:
            with st.spinner("Reading the page and measuring its structure…"):
                report = inspect_url(target, PoliteFetcher())
            st.session_state["x_inspection"] = report
        except RobotsDisallowed as exc:
            st.error(
                f"robots.txt enforcement is on, and this path is disallowed. ({exc}) "
                "Set B2B_ROBOTS_POLICY=warn to review the restriction instead of blocking, "
                "or choose a page the site permits."
            )
        except BlockedError as exc:
            st.error(
                f"The site refused access ({exc}). Do not retry in a loop. "
                "Check the site's terms and your permission to collect before continuing."
            )
        except FetchError as exc:
            st.error(f"Could not open that page: {exc}")
        except Exception as exc:  # defensive: never leave the operator with a blank screen
            st.error(f"Unexpected error while inspecting: {exc.__class__.__name__}: {exc}")

    report = st.session_state.get("x_inspection")
    if report is None:
        return

    with st.container(key="card_inspect"):
        st.markdown('<p class="sec-eyebrow">Step 2</p>', unsafe_allow_html=True)
        st.markdown('<p class="sec-title"><span class="stepnum">2</span>Inspection report</p>', unsafe_allow_html=True)

        adapter = resolve_adapter(report.final_url or report.url, "", report.recommended_adapter)
        if report.requires_javascript and not (report.profile_link_count or report.container_count
                                               or report.embedded_record_count or report.jsonld_blocks):
            capability = "Browser automation required"
        elif report.profile_link_count or report.container_count or report.embedded_record_count or report.jsonld_blocks:
            capability = "Static extraction possible"
        else:
            capability = "No records detected"
        st.markdown(
            f'<p class="sec-sub">{capability} · '
            f'{"Structured data found" if report.jsonld_blocks else "No structured data"} · '
            f'{report.container_count} listing block(s) detected</p>',
            unsafe_allow_html=True,
        )

        metric_grid(
            metric_card("🔗", "Profile links", str(report.profile_link_count),
                        f"{report.container_count} repeated blocks", "#5b2fd6")
            + metric_card("📄", "Pagination", report.pagination_type.replace("_", " ").title(),
                          f"{report.pagination_pages} page(s) found" if report.pagination_pages else "not detected", "#8b5cf6")
            + metric_card("🧩", "Structured data", str(report.jsonld_blocks),
                          ", ".join(report.jsonld_types[:2]) or "none", "#10b981")
            + metric_card("🤖", "Planned adapter", adapter.label.split(" ")[0],
                          adapter.id, "#f59e0b")
        )

        kv([
            ("Page title", report.page_title or "—"),
            ("Final URL", report.final_url or report.url),
            ("Listing block", report.container_selector or "not detected"),
            ("Profile URL pattern", report.profile_link_pattern or "not derived"),
            ("Load-more control", "present" if report.load_more_detected else "not found"),
            ("Sitemap", "found" if report.sitemap_found else "not checked / absent"),
            ("robots.txt", report.robots_detail or "not checked"),
        ])

        if getattr(report, "detected_layers", None):
            st.caption("Extraction layers available: " + " · ".join(report.detected_layers))
        samples = getattr(report, "sample_records", None) or []
        if samples:
            with st.expander("Preview of detected records", expanded=False):
                frame = pd.DataFrame(samples)
                frame = frame.rename(columns={
                    key: (FIELD_BY_KEY[key].label if key in FIELD_BY_KEY else header_for(key))
                    for key in frame.columns
                })
                st.dataframe(frame, use_container_width=True, hide_index=True)
                st.caption("First records on the listing page, before a full extraction.")
        if report.warnings:
            notes(report.warnings)


# ---------------------------------------------------------------------------
# Step 3
# ---------------------------------------------------------------------------
def _detected_fields(report) -> list[str]:
    detected = [k for k in report.available_fields if k in FIELD_BY_KEY]
    if not detected:
        detected = list(CORE_FIELD_KEYS)
    return detected


def _render_field_selection(report) -> list[str]:
    detected = _detected_fields(report)
    for spec in FIELD_CATALOGUE:
        st.session_state.setdefault(f"x_field_{spec.key}", spec.key in detected)

    with st.container(key="card_fields"):
        st.markdown('<p class="sec-eyebrow">Step 3</p>', unsafe_allow_html=True)
        st.markdown('<p class="sec-title"><span class="stepnum">3</span>Extraction fields</p>', unsafe_allow_html=True)
        st.markdown(
            '<p class="sec-sub">Only fields the site actually publishes can be collected. '
            "Fields that are not detected usually come back blank — that is a fact about the site, "
            "not a bug to work around.</p>",
            unsafe_allow_html=True,
        )

        selected: list[str] = []
        columns = st.columns(2)
        for index, spec in enumerate(FIELD_CATALOGUE):
            column = columns[index % 2]
            was_detected = spec.key in detected
            label = spec.label + ("" if was_detected else "  ·  not detected")
            with column:
                if st.checkbox(label, key=f"x_field_{spec.key}", help=spec.description):
                    selected.append(spec.key)

        if not selected:
            st.warning("Select at least one field before extracting.")
    return selected


# ---------------------------------------------------------------------------
# Step 4
# ---------------------------------------------------------------------------
def _render_run_controls(report, selected_fields: list[str]) -> None:
    with st.container(key="card_run"):
        st.markdown('<p class="sec-eyebrow">Step 4</p>', unsafe_allow_html=True)
        st.markdown('<p class="sec-title"><span class="stepnum">4</span>Run extraction</p>', unsafe_allow_html=True)
        st.markdown(
            f'<p class="sec-sub">Requests are spaced {SETTINGS.request_delay_seconds:g}s apart. '
            "robots.txt status is reported; set B2B_ROBOTS_POLICY=enforce to block automatically. "
            "No login-walled or CAPTCHA-protected content is touched. "
            f'Up to {SETTINGS.max_profiles} profiles per run.</p>',
            unsafe_allow_html=True,
        )

        r1, r2, r3 = st.columns([1, 1, 1.6], vertical_alignment="bottom")
        pages = r1.number_input(
            "Listing pages to walk", min_value=1, max_value=100, key="x_pages_input",
        )
        profiles = r2.number_input(
            "Maximum profiles", min_value=1, max_value=SETTINGS.max_profiles_per_run_hard_cap,
            key="x_profiles_input",
        )
        prefer_listing = r3.checkbox(
            "Use complete listing cards (faster; may skip profile-only fields)",
            value=bool(SETTINGS.prefer_listing_records),
            key="x_prefer_listing",
        )
        run_clicked = st.button(
            "⚡ Start extraction", type="primary", use_container_width=True,
            disabled=not selected_fields, key="x_run_button",
        )

        if not run_clicked:
            return

        st.session_state["x_pages"] = int(pages)
        st.session_state["x_profiles"] = int(profiles)
        progress = st.progress(0.0, text="Starting…")
        status = st.empty()

        def on_progress(stage: str, done: int, total: int) -> None:
            fraction = min(1.0, done / total) if total else 0.0
            progress.progress(fraction, text=stage)
            status.caption(stage)

        try:
            result = run_extraction(
                report.final_url or report.url,
                selected_fields=selected_fields,
                max_list_pages=int(pages),
                max_profiles=int(profiles),
                progress=on_progress,
                prefer_listing_records=bool(prefer_listing),
            )
        except (RobotsDisallowed, BlockedError) as exc:
            progress.empty()
            st.error(f"The run stopped for compliance reasons: {exc}")
            return
        except FetchError as exc:
            progress.empty()
            st.error(f"The run could not start: {exc}")
            return
        except Exception as exc:
            progress.empty()
            st.error(f"Extraction failed: {exc.__class__.__name__}: {exc}")
            return

        progress.progress(1.0, text="Finished")
        status.empty()
        st.session_state["x_run"] = result
        st.session_state["x_fields"] = selected_fields


# ---------------------------------------------------------------------------
# Step 5
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def render() -> None:
    _init_widget_state()

    # Switching between automatic and browser mode must not show a result that
    # was produced by the other method.
    current_mode = st.session_state.get("x_mode", "Automatic")
    if st.session_state.get("x_last_mode") not in (None, current_mode):
        st.session_state.pop("x_run", None)
    st.session_state["x_last_mode"] = current_mode

    _render_url_and_inspection()

    if st.session_state.get("x_mode") == "Browser":
        render_browser_flow((st.session_state.get("x_url_input") or "").strip())
        return

    report = st.session_state.get("x_inspection")
    if report is None:
        with st.container(key="card_empty"):
            section_header(
                "Getting started",
                "No directory inspected yet",
                "Three quick steps to your first extraction.",
            )
            st.markdown(
                '<div class="steps">'
                '<div class="step"><div class="step-n">1</div><div class="step-t">Paste a roster URL</div>'
                '<div class="step-d">The page that lists your agents, e.g. a brokerage’s /agents page.</div></div>'
                '<div class="step"><div class="step-t">Inspect the structure</div>'
                '<div class="step-d">We report profile links, pagination, structured data and whether a browser is needed.</div></div>'
                '<div class="step"><div class="step-t">Choose fields and extract</div>'
                '<div class="step-d">Only fields the site publishes are collected, then you review and export.</div></div>'
                "</div>",
                unsafe_allow_html=True,
            )
        return

    selected_fields = _render_field_selection(report)
    _render_run_controls(report, selected_fields)

    result = st.session_state.get("x_run")
    if result is None:
        return

    render_quality(result)
    render_preview(result)
