"""Visual click-to-select.

The captured, rendered HTML is sanitised and shown inside a Streamlit custom
component. The component highlights the element under the cursor, works out which
repeated row it belongs to, and sends back a position path. Python maps that path
to a detected column, so clicking an agent's name is exactly equivalent to choosing
the column whose sample values are names — no CSS is shown to the operator.

The component protocol is implemented directly in `visual_picker/index.html`
(componentReady / render / setComponentValue / setFrameHeight), so no Node build
step is required.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.browser.structure import column_for_click
from core.inspect_site import parse_html
from core.models import StructureModel
from core.normalize import clean_text
from core.settings import SETTINGS

_COMPONENT = None
_COMPONENT_ERROR = ""
_COMPONENT_DIR = Path(__file__).resolve().parent / "visual_picker"


def _component():
    """Lazily declare the component; tests run without Streamlit installed."""
    global _COMPONENT, _COMPONENT_ERROR
    if _COMPONENT is not None:
        return _COMPONENT
    if _COMPONENT_ERROR:
        return None
    try:
        import streamlit.components.v1 as components  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on runtime
        _COMPONENT_ERROR = f"{exc.__class__.__name__}: {exc}"
        return None
    try:
        _COMPONENT = components.declare_component(
            "b2b_visual_picker",
            path=str(_COMPONENT_DIR),
        )
        return _COMPONENT
    except Exception as exc:  # pragma: no cover - depends on runtime
        _COMPONENT_ERROR = f"{exc.__class__.__name__}: {exc}"
        return None


def component_available() -> bool:
    return _component() is not None


def sanitize_snapshot(html: str, base_url: str = "",
                      max_bytes: int | None = None) -> tuple[str, bool]:
    """Strip executable content and make relative assets resolve.

    Returns (html, ok). `ok` is False when the snapshot is too large to send
    through Streamlit's component channel.
    """
    if not html:
        return "", False
    limit = SETTINGS.max_snapshot_bytes if max_bytes is None else max_bytes
    soup = parse_html(html)

    for tag in soup(["script", "iframe", "object", "embed", "applet", "frame", "frameset", "noscript"]):
        tag.decompose()
    for meta in list(soup.find_all("meta")):
        equiv = str(meta.get("http-equiv") or "").lower()
        if equiv in {"content-security-policy", "refresh", "x-frame-options"}:
            meta.decompose()

    for tag in soup.find_all(True):
        for attribute in list(tag.attrs):
            lowered = str(attribute).lower()
            if lowered.startswith("on") or lowered in {"srcdoc", "formaction"}:
                del tag.attrs[attribute]
        if tag.name == "link":
            tag.attrs.pop("integrity", None)
            tag.attrs.pop("crossorigin", None)
        if tag.name == "form":
            tag.attrs.pop("action", None)
            tag.attrs.pop("method", None)

    head = soup.head
    if head is None:
        head = soup.new_tag("head")
        if soup.html is not None:
            soup.html.insert(0, head)
        else:
            soup.insert(0, head)
    for existing in head.find_all("base"):
        existing.decompose()
    if base_url:
        base = soup.new_tag("base", href=base_url)
        head.insert(0, base)
    charset = soup.new_tag("meta")
    charset["charset"] = "utf-8"
    head.insert(0, charset)

    body = soup.body
    if body is None:
        body = soup.new_tag("body")
        if soup.html is not None:
            soup.html.append(body)
        else:
            soup.append(body)
    classes = [c for c in (body.get("class") or []) if isinstance(c, str)]
    if "b2b-snapshot" not in classes:
        classes.append("b2b-snapshot")
    body["class"] = classes

    output = str(soup)
    if len(output.encode("utf-8")) > limit:
        return "", False
    return output, True


def _clean_selection(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("reset"):
        return value
    if not value.get("path") and not value.get("text"):
        return None
    return value


def render_picker(html: str, base_url: str, field: str, field_label: str,
                  nonce: str, key: str = "b2b_visual_picker") -> dict[str, Any] | None:
    """Render the picker and return the last selection, if any."""
    component = _component()
    if component is None:
        return None
    snapshot, ok = sanitize_snapshot(html, base_url)
    if not ok:
        return None
    try:
        value = component(
            html=snapshot,
            base_url=base_url,
            field=field,
            field_label=field_label,
            nonce=nonce,
            key=key,
            default=None,
        )
    except Exception:
        return None
    return _clean_selection(value)


def map_selection(structure: StructureModel | None, selection: dict[str, Any] | None) -> int | None:
    if structure is None or not selection:
        return None
    path = str(selection.get("path") or "")
    text = clean_text(selection.get("text") or "")
    return column_for_click(structure, path, text)


def selection_description(selection: dict[str, Any] | None) -> str:
    if not selection:
        return ""
    text = clean_text(selection.get("text") or "")
    count = selection.get("group_count") or 0
    bits = []
    if text:
        bits.append(f"“{text[:80]}”")
    if count:
        bits.append(f"{count} similar rows")
    return " · ".join(bits)


def snapshot_message(html: str, base_url: str = "") -> str:
    """Explain why the picker is unavailable, if it is."""
    if not html:
        return "Analyse the page first; there is no rendered snapshot to show."
    _snapshot, ok = sanitize_snapshot(html, base_url)
    if not ok:
        return (
            "This page's rendered HTML is too large to display in the visual picker. "
            "Use the column dropdowns below; they are built from the same detected structure."
        )
    if not component_available():
        return (
            "The visual picker component is not available in this environment. "
            "Use the column dropdowns below; they map the same fields."
        )
    return ""
