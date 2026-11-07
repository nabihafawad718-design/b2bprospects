"""Failure diagnosis for extraction runs.

The engine records every extraction layer it tries. This module turns those facts
into one of a small set of failure codes plus operator-facing next steps, so the
UI can say *why* a page produced nothing instead of showing a generic error.

The important distinction is between:

* a refusal (HTTP 401/403/429 or an anti-bot challenge) — stop and respect it;
* a technical miss (JavaScript shell, wrong page, mapping empty, pagination
  stalled) — explain it and try another supported layer.
"""
from __future__ import annotations

from typing import Any

# Failure codes used by the UI and by scripts. Keep these stable.
BLOCKED = "blocked"
NOT_FOUND = "not_found"
SERVER_ERROR = "server_error"
JAVASCRIPT_ONLY = "javascript_only"
NOT_A_LISTING = "not_a_listing"
SINGLE_PROFILE = "single_profile"
MAPPING_EMPTY = "mapping_empty"
NO_RECORDS = "no_records"
PAGINATION_STALLED = "pagination_stalled"
PARTIAL = "partial"
OK = "ok"

_LABELS = {
    BLOCKED: "Access refused",
    NOT_FOUND: "Page not found",
    SERVER_ERROR: "Server error",
    JAVASCRIPT_ONLY: "JavaScript-only page",
    NOT_A_LISTING: "Not a listing page",
    SINGLE_PROFILE: "Single profile page",
    MAPPING_EMPTY: "Field mapping produced nothing",
    NO_RECORDS: "No usable records",
    PAGINATION_STALLED: "Pagination stalled",
    PARTIAL: "Incomplete coverage",
    OK: "Extraction available",
}


def label(code: str) -> str:
    return _LABELS.get(code, "Extraction problem")


def classify(
    *,
    status_code: int = 0,
    blocked: bool = False,
    page_text_length: int = 0,
    script_count: int = 0,
    row_count: int = 0,
    profile_link_count: int = 0,
    embedded_count: int = 0,
    jsonld_count: int = 0,
    link_cluster_count: int = 0,
    structure_present: bool = False,
    mapping_present: bool = True,
    records: int = 0,
    pages: int = 0,
    pagination_reason: str = "",
    pagination_strategy: str = "",
) -> tuple[str, str, list[str]]:
    """Return (code, one-line summary, recommended next steps)."""
    if blocked or status_code in (401, 403, 429):
        return (
            BLOCKED,
            f"The site refused automated access (HTTP {status_code or '401/403/429'}).",
            [
                "Stop this target; do not retry in a loop or try to evade the restriction.",
                "Record the refusal in the project notes and ask the site owner for permission.",
                "Use a different permitted source for this customer.",
            ],
        )
    if status_code == 404:
        return (
            NOT_FOUND,
            "The URL returned HTTP 404; the roster address is wrong or has moved.",
            ["Check the roster URL in a normal browser and paste the current address."],
        )
    if status_code >= 500:
        return (
            SERVER_ERROR,
            f"The site returned HTTP {status_code}. This is temporary server trouble, not a layout problem.",
            ["Try again later; do not change the extraction rules because of a 5xx response."],
        )

    if records:
        if pages <= 1 and pagination_strategy not in ("", "none"):
            return (
                PARTIAL,
                f"{records} records were collected, but pagination stopped after the first page.",
                [
                    f"Pagination note: {pagination_reason}" if pagination_reason else
                    "Open the page and confirm the next-page control is visible.",
                    "Re-run with a larger page cap or choose the navigation style manually.",
                ],
            )
        return (OK, f"{records} records were extracted.", [])

    if structure_present and not mapping_present:
        return (
            MAPPING_EMPTY,
            "A repeated record structure was found, but no field was mapped to a column.",
            [
                "Map at least the agent name column.",
                "Use the visual picker to click one example name, or choose a column from the dropdowns.",
            ],
        )
    if structure_present and mapping_present:
        return (
            NO_RECORDS,
            "A repeated structure was detected but the mapped columns produced no records.",
            [
                "Check the preview: the chosen name column may be a heading, badge or job title.",
                "Try another detected block at the top of Step 3, then re-map.",
                "If every column is empty, the page probably changed after analysis; analyse again.",
            ],
        )
    if embedded_count or jsonld_count:
        return (
            NO_RECORDS,
            "Structured or embedded records were found but could not be converted into rows.",
            [
                "Run Browser mode so the rendered page and its current payload are used.",
                "If this repeats, capture the page HTML and add a config adapter for the site.",
            ],
        )
    if link_cluster_count:
        return (
            NO_RECORDS,
            "Profile links were found but their surrounding record containers could not be read.",
            [
                "Use Browser mode and map fields with the visual picker.",
                "Add a config adapter if this site recurs in customer work.",
            ],
        )

    if page_text_length < 400 and (script_count >= 3 or profile_link_count == 0):
        return (
            JAVASCRIPT_ONLY,
            "The server HTML contains almost no content; the listing is rendered by JavaScript.",
            [
                "Switch to Browser mode so Chromium can render the page.",
                "If Browser mode also finds nothing, the site may load records from an authenticated API.",
                "For a recurring target, add a site adapter with the profile URL pattern.",
            ],
        )
    if profile_link_count == 0:
        return (
            SINGLE_PROFILE if jsonld_count == 1 else NOT_A_LISTING,
            "No repeated records or profile links were found on this page.",
            [
                "Paste the roster page that lists many agents, not one agent's profile.",
                "If this *is* the roster, open it in a browser: the list may appear after a search or tab click.",
                "Use Browser mode, or add a config adapter for the site's URL pattern.",
            ],
        )
    return (
        NO_RECORDS,
        "The page looks like a listing, but no extraction layer produced usable records.",
        [
            "Analyse the page in Browser mode and check the preview before running.",
            "Try a different detected block or map fields with the visual picker.",
        ],
    )
