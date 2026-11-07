"""Adapter registry.

Resolution order:
  1. a site-specific adapter that claims the domain,
  2. the adapter the inspector recommended,
  3. the generic HTML adapter.

To support a new site permanently, add its class to `SITE_ADAPTERS`.
"""
from __future__ import annotations

from .base import DirectoryAdapter
from .config_adapter import load_config_adapters
from .custom_site import CustomSiteAdapter
from .generic_html import GenericHtmlAdapter
from .generic_jsonld import GenericJsonLdAdapter

# Config-driven adapters are loaded once at import. Order matters: first match wins.
_CONFIG_ADAPTERS, CONFIG_ERRORS = load_config_adapters()

# Site-specific adapters live here. Add a Python adapter class, or drop a JSON
# file in core/adapters/sites/ for the no-code path.
SITE_ADAPTERS: list[DirectoryAdapter] = [
    *_CONFIG_ADAPTERS,
    CustomSiteAdapter(),
]

GENERIC_JSONLD = GenericJsonLdAdapter()
GENERIC_HTML = GenericHtmlAdapter()


def available_adapters() -> list[dict[str, str]]:
    rows = []
    for adapter in SITE_ADAPTERS:
        if not adapter.domains:
            continue  # template not yet configured for a real site
        if adapter.id == "custom_site":
            continue  # the Python template is documentation, not a live adapter
        rows.append({
            "id": adapter.id,
            "label": adapter.label,
            "domains": ", ".join(adapter.domains),
            "description": adapter.description,
            "kind": "site",
        })
    rows.append({
        "id": GENERIC_JSONLD.id,
        "label": GENERIC_JSONLD.label,
        "domains": "any (when schema.org markup is present)",
        "description": GENERIC_JSONLD.description,
        "kind": "generic",
    })
    rows.append({
        "id": GENERIC_HTML.id,
        "label": GENERIC_HTML.label,
        "domains": "any (server-rendered)",
        "description": GENERIC_HTML.description,
        "kind": "generic",
    })
    return rows


def resolve_adapter(url: str, html: str = "", recommended: str = "") -> DirectoryAdapter:
    for adapter in SITE_ADAPTERS:
        if adapter.domains and adapter.matches(url, html):
            return adapter
    if recommended == GENERIC_JSONLD.id:
        return GENERIC_JSONLD
    return GENERIC_HTML


def adapter_by_id(adapter_id: str) -> DirectoryAdapter | None:
    for adapter in [*SITE_ADAPTERS, GENERIC_JSONLD, GENERIC_HTML]:
        if adapter.id == adapter_id:
            return adapter
    return None
