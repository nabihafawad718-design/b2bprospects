"""Normalization and field-level validation.

Everything here is pure, synchronous and dependency-light so it can be unit tested
without a browser, a network, or Streamlit.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
_WS = re.compile(r"\s+")
_TAGS = re.compile(r"<[^>]+>")
_INVISIBLE = re.compile(r"[​-‏‪-‮﻿]")


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    text = _TAGS.sub(" ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = _INVISIBLE.sub("", text)
    return _WS.sub(" ", text).strip()


# Designations that follow an agent's name on roster pages. Removing them keeps
# "Jane Doe, ABR, CRS, REALTOR®" and "Jane Doe" from being counted as two people.
_NAME_CREDENTIALS = {
    "abr", "crs", "gri", "sres", "srs", "mrp", "cips", "alhs", "crs", "crb", "crs",
    "realtor", "realtor®", "broker", "associate broker", "salesperson", "agent",
    "esq", "mba", "phd", "cpa", "pmp", "sr", "jr", "ii", "iii", "iv",
    "clhms", "cnc", "crc", "cre", "csp", "ecba", "epro", "green", "gri", "mcn",
    "psa", "rcc", "rsps", "srf", "srs", "srms", "sres", "trc",
}


def normalize_name(raw: object) -> str:
    """Strip credentials and markup, keep the person's real casing."""
    text = clean_text(raw)
    if not text:
        return ""
    text = text.replace("|", ",").replace("•", ",")
    parts = [p.strip() for p in text.split(",")]
    keep: list[str] = []
    for index, part in enumerate(parts):
        if not part:
            continue
        lowered = part.lower().strip(".")
        if index > 0 and lowered in _NAME_CREDENTIALS:
            continue
        keep.append(part)
    name = keep[0] if keep else text
    name = re.sub(r"\s{2,}", " ", name).strip(" -–—,")
    # Drop leading labels such as "Agent: Jane Doe"
    name = re.sub(r"^(?:agent|realtor|broker|team lead)\s*[:\-]\s*", "", name, flags=re.I)
    return name[:120]


def normalize_org(raw: object) -> str:
    text = clean_text(raw)
    text = re.sub(r"\s*[|\-–—]\s*(?:real estate|realtors?|homes?)\s*$", "", text, flags=re.I)
    return text[:160]


# ---------------------------------------------------------------------------
# Phone
# ---------------------------------------------------------------------------
_PHONE_CANDIDATE = re.compile(r"(?<![\w#])(\+?\d[\d().\s\-]{6,18}\d)(?![\w])")


def phone_digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def normalize_phone(raw: object, default_region: str = "US") -> tuple[str, str]:
    """Return (display, digits). Rejects strings that are not plausible phone numbers."""
    text = clean_text(raw)
    if not text:
        return "", ""
    if text.lower().startswith(("tel:", "phone:", "call:")):
        text = text.split(":", 1)[1].strip()

    digits = phone_digits(text)
    if not digits:
        return "", ""
    # Reject placeholders (0000000000, 1111111111) that appear in templates and demos.
    if not looks_like_phone(digits):
        return "", ""
    # Strip a leading international dialling prefix we already know about.
    if len(digits) == 11 and digits.startswith("1") and default_region == "US":
        display = f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
        return display, digits
    if len(digits) == 10 and default_region == "US":
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}", digits
    if 8 <= len(digits) <= 15:
        if text.strip().startswith("+"):
            return f"+{digits}", digits
        return digits, digits
    return "", ""


def looks_like_phone(candidate: str) -> bool:
    digits = phone_digits(candidate)
    if not (8 <= len(digits) <= 15):
        return False
    if len(set(digits)) < 4:            # 0000000000, 1111111111
        return False
    if digits[:4] in {"1900", "2000", "2100"}:
        return False
    return True


def find_phones(text: str, limit: int = 3) -> list[str]:
    out: list[str] = []
    for match in _PHONE_CANDIDATE.finditer(text or ""):
        display, digits = normalize_phone(match.group(1))
        if display and digits not in {phone_digits(o) for o in out}:
            out.append(display)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_EMAIL_TLD_OK = re.compile(r"\.[A-Za-z]{2,}$")
_FAKE_LOCAL_PARTS = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "postmaster", "webmaster",
    "hostmaster", "abuse", "privacy", "unsubscribe", "example", "test", "user",
    "your", "youremail", "email", "name", "info-at", "sentry", "sentry_key",
}
_FAKE_DOMAINS = {
    "example.com", "example.org", "example.net", "domain.com", "yourdomain.com",
    "email.com", "test.com", "sentry.io", "sentry-next.wixpress.com", "wixpress.com",
    "googlemail.com.invalid", "localhost",
}
_ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js", ".ico", ".woff", ".woff2")


def normalize_email(raw: object) -> str:
    text = clean_text(raw).replace("mailto:", "").strip().strip(".,;:<>()[]\"'")
    if not text or "@" not in text:
        return ""
    lowered = text.lower()
    if lowered.endswith(_ASSET_SUFFIXES):
        return ""
    if not _EMAIL_RE.fullmatch(lowered):
        return ""
    local, _, domain = lowered.partition("@")
    if not _EMAIL_TLD_OK.search(domain):
        return ""
    if local in _FAKE_LOCAL_PARTS or local.startswith("sentry"):
        return ""
    if domain in _FAKE_DOMAINS or domain.endswith(".invalid"):
        return ""
    if ".." in lowered:
        return ""
    # Reject hashed asset filenames that happen to contain an @.
    if re.fullmatch(r"[0-9a-f]{16,}", local):
        return ""
    return lowered[:254]


def find_emails(text: str, limit: int = 3) -> list[str]:
    out: list[str] = []
    for match in _EMAIL_RE.finditer(text or ""):
        email = normalize_email(match.group(0))
        if email and email not in out:
            out.append(email)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Address
# ---------------------------------------------------------------------------
_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
    "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY",
    "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "VI", "GU", "AS", "MP",
}
_ADDRESS_TAIL = re.compile(
    r"(?P<city>[A-Za-z][A-Za-z .'\-]{1,40})\s*,\s*(?P<state>[A-Za-z]{2})\s+(?P<postal>\d{5}(?:-\d{4})?)"
)


def parse_address(raw: object) -> dict[str, str]:
    """Best-effort split of a US-style address string into components."""
    text = clean_text(raw)
    result = {"street_address": "", "city": "", "state": "", "postal_code": ""}
    if not text:
        return result

    match = _ADDRESS_TAIL.search(text)
    if match and match.group("state").upper() in _STATE_CODES:
        result["city"] = match.group("city").strip(" ,")
        result["state"] = match.group("state").upper()
        result["postal_code"] = match.group("postal")
        street = text[: match.start()].strip(" ,")
        result["street_address"] = street
        return result

    # No state/postcode tail: keep the first line as the street.
    first = text.split(",")[0].strip()
    result["street_address"] = first[:160]
    if len(text.split(",")) > 1:
        result["city"] = text.split(",")[1].strip()[:80]
    return result


def compose_address(street: str, city: str, state: str, postal: str) -> str:
    tail = " ".join(p for p in (state, postal) if p)
    if city and tail:
        return f"{street}, {city}, {tail}".strip(", ")
    return ", ".join(p for p in (street, city, tail) if p)


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referrer"}


def canonical_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _TRACKING_PARAMS]
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((parts.scheme.lower(), host + port, path, "", urlencode(query), ""))


def host_of(url: str) -> str:
    try:
        host = urlparse(url if "://" in url else "https://" + url).hostname or ""
    except ValueError:
        return ""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def same_host(a: str, b: str) -> bool:
    return host_of(a) == host_of(b)
