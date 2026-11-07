"""Template for a site-specific adapter — the maintenance path you will use most.

When a brokerage directory redesigns and the generic adapter stops producing
clean records, copy this file to `core/adapters/<site>.py`, fill in `DOMAINS` and
the selectors, and register it in `core/adapters/registry.py`.

You do not touch the engine, validation, export or UI. That is the whole point of
the adapter layer.

A worked example is inlined below using a fictional brokerage domain so the file
is syntactically complete and importable. `DOMAINS` is empty by default, which
means this adapter never claims a real site until you set it.
"""
from __future__ import annotations

from urllib.parse import urljoin

from ..models import RawRecord
from ..net import FetchedPage
from ..normalize import (
    canonical_url,
    clean_text,
    find_emails,
    find_phones,
    normalize_email,
    normalize_name,
    normalize_org,
    normalize_phone,
    parse_address,
)
from .base import AdapterContext, DirectoryAdapter


class CustomSiteAdapter(DirectoryAdapter):
    # --- configuration -----------------------------------------------------
    id = "custom_site"
    label = "Custom site adapter (template)"
    version = "1.0"
    domains: tuple[str, ...] = ()          # e.g. ("examplebrokerage.com",)
    description = "Selector-driven adapter. Fill in the selectors for one website."

    # Listing page
    ROW_SELECTOR = "div.agent-card"                 # each row on the roster page
    ROW_LINK_SELECTOR = "a.agent-card__link"        # anchor to the profile
    NEXT_SELECTOR = "a.pagination__next"            # optional

    # Profile page
    NAME_SELECTOR = "h1.agent-name"
    BROKERAGE_SELECTOR = ".agent-office__name"
    PHONE_SELECTOR = "a[href^='tel:']"
    EMAIL_SELECTOR = "a[href^='mailto:']"
    ADDRESS_SELECTOR = "address.agent-office"

    # --- listing -----------------------------------------------------------
    def profile_links(self, page: FetchedPage, ctx: AdapterContext) -> list[str]:
        soup = self.soup(page)
        links: list[str] = []
        for row in soup.select(self.ROW_SELECTOR):
            anchor = row.select_one(self.ROW_LINK_SELECTOR)
            if anchor and anchor.get("href"):
                links.append(canonical_url(urljoin(page.final_url, anchor["href"])))
        if not links:
            ctx.note(
                f"{self.label}: row selector {self.ROW_SELECTOR!r} matched nothing — "
                "the listing markup has probably changed."
            )
        return list(dict.fromkeys(links))

    def next_page_urls(self, page: FetchedPage, ctx: AdapterContext) -> list[str]:
        soup = self.soup(page)
        anchor = soup.select_one(self.NEXT_SELECTOR)
        if anchor and anchor.get("href"):
            target = canonical_url(urljoin(page.final_url, anchor["href"]))
            if target != canonical_url(page.final_url):
                return [target]
        return []

    # --- profile -----------------------------------------------------------
    def parse_profile(self, page: FetchedPage, ctx: AdapterContext) -> RawRecord | None:
        soup = self.soup(page)
        fields: dict[str, str] = {}

        name_el = soup.select_one(self.NAME_SELECTOR)
        if name_el:
            fields["agent_name"] = normalize_name(name_el.get_text(" "))

        org_el = soup.select_one(self.BROKERAGE_SELECTOR)
        if org_el:
            fields["brokerage_name"] = normalize_org(org_el.get_text(" "))

        phone_el = soup.select_one(self.PHONE_SELECTOR)
        if phone_el:
            display, _digits = normalize_phone(phone_el.get("href", "")[4:] or phone_el.get_text(" "))
            if display:
                fields["phone"] = display
        else:
            found = find_phones(clean_text(soup.get_text(" ")), limit=1)
            if found:
                fields["phone"] = found[0]

        email_el = soup.select_one(self.EMAIL_SELECTOR)
        if email_el:
            email = normalize_email(email_el.get("href", "")[7:] or email_el.get_text(" "))
            if email:
                fields["email"] = email
        else:
            found = find_emails(clean_text(soup.get_text(" ")), limit=1)
            if found:
                fields["email"] = found[0]

        address_el = soup.select_one(self.ADDRESS_SELECTOR)
        if address_el:
            fields.update({k: v for k, v in parse_address(address_el.get_text(" ")).items() if v})

        warnings: list[str] = []
        if not fields.get("agent_name"):
            warnings.append("name selector matched nothing")

        return RawRecord(
            fields=fields,
            profile_url=canonical_url(page.final_url),
            source_url=canonical_url(page.final_url),
            method="custom-selectors",
            warnings=warnings,
        )


# ---------------------------------------------------------------------------
# HOW TO ADAPT THIS FILE
# ---------------------------------------------------------------------------
# 1. Copy to core/adapters/<site>.py and rename the class, e.g. `AcmeRealtyAdapter`.
# 2. Set `id`, `label`, and `domains = ("acmerealty.com",)`.
# 3. Open a roster page in your browser, inspect one row, and copy the CSS
#    selectors into ROW_SELECTOR / ROW_LINK_SELECTOR.
# 4. Open one profile, set NAME_SELECTOR etc. Delete selectors the site lacks.
# 5. In core/adapters/registry.py, import it and add it to `ADAPTERS`.
# 6. Run the tool against the site. Check the Health tab: record count and field
#    fill rates become the baseline that future runs are compared against.
#
# If the site renders with JavaScript and the HTML has no rows, a selector
# adapter cannot help. Use the browser path instead (see OPERATOR_GUIDE.md).
