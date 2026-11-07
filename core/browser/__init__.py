"""Real browser extraction engine.

Public entry points:
    inspect_in_browser(url)        open a page and describe what is extractable
    run_browser_extraction(...)    extract records across pages
    PlaywrightSession              the browser backend
    analyze_html(html)             structure detection on rendered DOM

Nothing in this package imports Streamlit.
"""
from .driver import (
    BrowserBlocked,
    BrowserError,
    BrowserTimeout,
    BrowserUnavailable,
    PlaywrightSession,
    diagnose,
    playwright_available,
)
from .engine import inspect_in_browser, run_browser_extraction
from .extract import FieldMapping, extract_page, suggest_mapping
from .structure import analyze_html, best_structure

__all__ = [
    "BrowserBlocked",
    "BrowserError",
    "BrowserTimeout",
    "BrowserUnavailable",
    "PlaywrightSession",
    "diagnose",
    "playwright_available",
    "inspect_in_browser",
    "run_browser_extraction",
    "FieldMapping",
    "extract_page",
    "suggest_mapping",
    "analyze_html",
    "best_structure",
]
