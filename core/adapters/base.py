"""Adapter contract.

An adapter is responsible for exactly four things:

1. deciding whether it can handle a URL,
2. producing next-page URLs,
3. producing profile URLs from a listing page,
4. turning one profile page into a RawRecord.

Everything else — politeness, normalization, deduplication, quality scoring,
health tracking, export — belongs to the engine. That split is what makes a
site-specific fix a one-file change.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import bs4

from ..models import InspectionReport, RawRecord
from ..net import FetchedPage, PoliteFetcher
from ..settings import SETTINGS, Settings


@dataclass
class AdapterContext:
    """Everything an adapter may need that is not the page itself."""

    start_url: str
    fetcher: PoliteFetcher
    settings: Settings = SETTINGS
    selected_fields: tuple[str, ...] = ()
    max_list_pages: int = 20
    max_profiles: int = 500
    notes: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        self.notes.append(message)


class DirectoryAdapter:
    """Base class. Subclasses override the methods they need."""

    id: str = "base"
    label: str = "Base adapter"
    version: str = "1.0"
    domains: tuple[str, ...] = ()
    expected_fields: tuple[str, ...] = ("agent_name", "phone", "email")
    description: str = ""

    # -- matching -----------------------------------------------------------
    def matches(self, url: str, html: str = "") -> bool:
        from ..normalize import host_of

        host = host_of(url)
        return any(host == d or host.endswith("." + d) for d in self.domains)

    # -- inspection hint ----------------------------------------------------
    def inspect(self, page: FetchedPage) -> InspectionReport | None:
        """Optional adapter-specific inspection. None means 'use the generic inspector'."""
        return None

    # -- listing ------------------------------------------------------------
    def next_page_urls(self, page: FetchedPage, ctx: AdapterContext) -> list[str]:
        return []

    def profile_links(self, page: FetchedPage, ctx: AdapterContext) -> list[str]:
        return []

    def listing_records(self, page: FetchedPage, ctx: AdapterContext) -> list[RawRecord]:
        """Records available directly on the listing page, if the site publishes them.

        A directory that embeds complete Person objects in schema.org or in a
        selector-driven config can skip the per-profile fetch entirely. Returning
        an empty list means "use profile links as usual".
        """
        return []

    # -- profile ------------------------------------------------------------
    def parse_profile(self, page: FetchedPage, ctx: AdapterContext) -> RawRecord | None:
        raise NotImplementedError

    # -- helpers ------------------------------------------------------------
    def soup(self, page: FetchedPage) -> bs4.BeautifulSoup:
        from ..inspect_site import parse_html

        return parse_html(page.text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.__class__.__name__} id={self.id!r}>"
