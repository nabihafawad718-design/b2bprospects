"""Normalization and field-level validation.

Everything here is pure, synchronous and dependency-light so it can be unit tested
without a browser, a network, or Streamlit.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
_WS = re.compile(r"\s+")
_TAGS = re.compile(r"<[^>]+>")
# Zero-width and bidi control characters that break exact matching.
_INVISIBLE = re.compile(r"[​-‏‪-‮﻿]")


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    text = _TAGS.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    text = _INVISIBLE.sub("", text)
    return _WS.sub(" ", text).strip()


def slugify(value: object, fallback: str = "field") -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (slug or fallback)[:60]


# Designations that follow an agent's name on roster pages. Removing them keeps
# "Jane Doe, ABR, CRS, REALTOR®" and "Jane Doe" from being counted as two people.
_NAME_CREDENTIALS = {
    "abr", "crs", "gri", "sres", "srs", "mrp", "cips", "alhs", "crb",
    "realtor", "realtor®", "broker", "associate broker", "salesperson", "agent",
    "esq", "mba", "phd", "cpa", "pmp", "sr", "jr", "ii", "iii", "iv",
    "clhms", "cnc", "crc", "cre", "csp", "ecba", "epro", "green", "mcn",
    "psa", "rcc", "rsps", "srf", "srms", "trc", "chfc", "clu", "cpm",
}
_NAME_PREFIXES = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "dame", "rev"}
_ORG_WORDS = re.compile(
    r"\b(realty|real estate|properties|property|group|partners|agency|brokerage|"
    r"homes|associates|team|inc|llc|ltd|corp|corporation|company|capital|estates|"
    r"realtors|management|services|consulting|solutions|holdings|ventures)\b",
    re.I,
)
_ADDRESS_WORDS = re.compile(
    r"\b(st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|lane|way|ct|court|"
    r"pl|place|pkwy|parkway|hwy|highway|suite|ste|floor|fl|unit|apt)\b\.?",
    re.I,
)
_SUITE = re.compile(r"^(suite|ste|unit|apt|apartment|floor|fl|#)\b", re.I)
_TITLE_CASE_NAME = re.compile(r"^[A-Z][A-Za-z'’\-]+(\s+[A-Z](?:[A-Za-z'’\-]*|\.)){1,4}$")


def _smart_title(text: str) -> str:
    """Title-case ALL-CAPS names without damaging Mc/Mac/O' forms."""
    if not text or not text.isupper() or len(text) < 4:
        return text
    words = []
    for word in text.split():
        lowered = word.lower()
        if lowered.startswith(("mc", "mac")) and len(word) > 4:
            words.append(word[:2].title() + word[2:].title())
        elif "'" in word:
            head, _, tail = word.partition("'")
            words.append(head.title() + "'" + tail.title())
        else:
            words.append(word.title())
    return " ".join(words)


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
    name = re.sub(r"^(?:agent|realtor|broker|team lead|sales associate)\s*[:\-]\s*", "", name, flags=re.I)
    # "Smith, Jane" -> "Jane Smith" only when both parts look like names.
    if len(keep) >= 2:
        first, second = keep[0].strip(), keep[1].strip()
        if (re.fullmatch(r"[A-Z][a-z'’\-]+(?:\s+[A-Z][a-z'’\-]+)?", first)
                and re.fullmatch(r"[A-Z][a-z'’\-]+(?:\s+[A-Z]\.?)?", second)
                and first.lower() not in _NAME_CREDENTIALS):
            name = f"{second} {first}"
    name = _smart_title(name)
    return name[:120]


def normalize_org(raw: object) -> str:
    text = clean_text(raw)
    text = re.sub(r"\s*[|\-–—]\s*(?:real estate|realtors?|homes?)\s*$", "", text, flags=re.I)
    return text[:160]


def looks_like_person_name(text: object) -> bool:
    value = clean_text(text)
    if not (2 <= len(value) <= 90):
        return False
    if any(ch.isdigit() for ch in value) or "@" in value or "http" in value.lower():
        return False
    if _ORG_WORDS.search(value) or _ADDRESS_WORDS.search(value):
        return False
    if len(value.split()) < 2 or len(value.split()) > 6:
        return False
    # "Dr. Jane Smith" and "Prof. J. Smith" are names, not titles.
    stripped = re.sub(r"^(mr|mrs|ms|miss|dr|prof|sir|dame|rev)\.?\s+", "", value, flags=re.I)
    return bool(_TITLE_CASE_NAME.match(value) or _TITLE_CASE_NAME.match(stripped) or value.isupper())


def looks_like_org_name(text: object) -> bool:
    value = clean_text(text)
    if not (2 <= len(value) <= 160):
        return False
    return bool(_ORG_WORDS.search(value))


# ---------------------------------------------------------------------------
# Phone
# ---------------------------------------------------------------------------
_PHONE_CANDIDATE = re.compile(r"(?<![\w#])(\+?\d[\d().\s\-]{6,22}\d)(?![\w])")
_EXTENSION = re.compile(r"(?:ext(?:ension)?\.?|x)\s*(\d{1,6})\s*$", re.I)


def phone_digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def normalize_phone(raw: object, default_region: str = "US") -> tuple[str, str]:
    """Return (display, digits). Rejects strings that are not plausible phone numbers."""
    text = clean_text(raw)
    if not text:
        return "", ""
    if text.lower().startswith(("tel:", "phone:", "call:", "mobile:")):
        text = text.split(":", 1)[1].strip()

    extension = ""
    match = _EXTENSION.search(text)
    if match:
        extension = match.group(1)
        text = text[: match.start()].strip(" ,;")

    digits = phone_digits(text)
    if not digits:
        return "", ""
    # Reject placeholders (0000000000, 1111111111) that appear in templates and demos.
    if not looks_like_phone(digits):
        return "", ""
    suffix = f" ext. {extension}" if extension else ""

    # Strip a leading international dialling prefix we already know about.
    if len(digits) == 11 and digits.startswith("1") and default_region == "US":
        display = f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
        return display + suffix, digits
    if len(digits) == 10 and default_region == "US":
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}" + suffix, digits
    if 8 <= len(digits) <= 15:
        if text.strip().startswith("+"):
            return f"+{digits}" + suffix, digits
        return digits + suffix, digits
    return "", ""


def looks_like_phone(candidate: str) -> bool:
    digits = phone_digits(candidate)
    if not (8 <= len(digits) <= 15):
        return False
    if len(set(digits)) < 4:            # 0000000000, 1111111111
        return False
    if digits[:4] in {"1900", "2000", "2100"}:
        return False
    # A run of 8+ digits that is actually a year/date/ID.
    if digits[:2] in {"19", "20"} and len(digits) == 8:
        return False
    return True


def find_phones(text: str, limit: int = 3) -> list[str]:
    out: list[str] = []
    for match in _PHONE_CANDIDATE.finditer(text or ""):
        # "Fax: 512..." is not the public phone we want unless nothing else exists.
        window = (text or "")[max(0, match.start() - 12): match.start()].lower()
        if "fax" in window:
            continue
        display, digits = normalize_phone(match.group(1))
        if display and digits not in {phone_digits(o) for o in out}:
            out.append(display)
        if len(out) >= limit:
            break
    return out


def tel_from_fragment(value: object) -> str:
    """Extract a usable phone from `tel:+1512...`, `phone:...` or plain text."""
    text = clean_text(value)
    if ":" in text and text.lower().startswith(("tel", "phone", "mobile", "call")):
        text = text.split(":", 1)[1]
    display, _digits = normalize_phone(text)
    return display


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
_OBFUSCATED_AT = re.compile(r"\s*(?:\[|\(|\{)\s*at\s*(?:\]|\)|\})\s*", re.I)
_OBFUSCATED_DOT = re.compile(r"\s*(?:\[|\(|\{)\s*dot\s*(?:\]|\)|\})\s*", re.I)


def deobfuscate_emails(text: str) -> str:
    """Decode `[at]` / `(dot)` forms, but not bare prose.

    Replacing every " at " would turn "look at example.com" into a false email,
    so only bracketed forms are decoded.
    """
    value = str(text or "")
    value = _OBFUSCATED_AT.sub("@", value)
    value = _OBFUSCATED_DOT.sub(".", value)
    return value


def normalize_email(raw: object) -> str:
    text = clean_text(raw).replace("mailto:", "").strip()
    text = text.split("?", 1)[0].strip(".,;:<>()[]\"'")
    if not text or "@" not in text:
        text = deobfuscate_emails(text)
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
    for match in _EMAIL_RE.finditer(deobfuscate_emails(text or "")):
        email = normalize_email(match.group(0))
        if email and email not in out:
            out.append(email)
        if len(out) >= limit:
            break
    return out


def email_from_fragment(value: object) -> str:
    text = clean_text(value).replace("mailto:", "")
    text = text.split("?", 1)[0]
    found = find_emails(text, limit=1)
    return found[0] if found else ""


# ---------------------------------------------------------------------------
# Address
# ---------------------------------------------------------------------------
_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
    "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY",
    "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "VI", "GU", "AS", "MP",
}
_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY", "district of columbia": "DC",
}
_CA_PROVINCES = {
    "ab", "bc", "mb", "nb", "nl", "ns", "nt", "nu", "on", "pe", "qc", "sk", "yt",
}
_AU_STATES = {"nsw", "vic", "qld", "wa", "sa", "tas", "act", "nt"}

_ADDRESS_TAIL = re.compile(
    r"(?P<city>[A-Za-z][A-Za-z .'\-]{1,40})\s*,\s*(?P<state>[A-Za-z]{2})\s+(?P<postal>\d{5}(?:-\d{4})?)"
)
_ADDRESS_CITY_STATE = re.compile(
    r"(?P<city>[A-Za-z][A-Za-z .'\-]{1,40})\s*,\s*(?P<state>[A-Za-z]{2})\s*$"
)
_ADDRESS_FULL_STATE = re.compile(
    r"(?P<city>[A-Za-z][A-Za-z .'\-]{1,40})\s*,\s*(?P<state>[A-Za-z ]{4,30}?)\s+(?P<postal>\d{5}(?:-\d{4})?)\s*$"
)
_CA_POSTAL = re.compile(r"(?P<city>[A-Za-z][A-Za-z .'\-]{1,40})\s*,\s*(?P<state>[A-Za-z]{2})\s+(?P<postal>[A-Z]\d[A-Z]\s?\d[A-Z]\d)\s*$", re.I)
_UK_POSTAL = re.compile(r"(?P<city>[A-Za-z][A-Za-z .'\-]{1,40})\s*,?\s*(?P<postal>[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2})\s*$", re.I)
_AU_POSTAL = re.compile(r"(?P<city>[A-Za-z][A-Za-z .'\-]{1,40})\s+(?P<state>NSW|VIC|QLD|WA|SA|TAS|ACT|NT)\s+(?P<postal>\d{4})\s*$", re.I)


def normalize_state(value: object) -> str:
    text = clean_text(value)
    if not text:
        return ""
    lowered = text.lower()
    if lowered in _STATE_NAMES:
        return _STATE_NAMES[lowered]
    if len(text) == 2:
        return text.upper()
    return text[:50]


def _address_result(street="", city="", state="", postal="", country="") -> dict[str, str]:
    return {
        "street_address": clean_text(street)[:200],
        "city": clean_text(city).strip(" ,")[:100],
        "state": normalize_state(state)[:50],
        "postal_code": clean_text(postal)[:20],
        "country": clean_text(country)[:60],
    }


def parse_address(raw: object, default_country: str = "") -> dict[str, str]:
    """Best-effort split of an address string into components.

    US addresses are the primary target, but Canadian, UK and Australian tails are
    recognised because many brokerage sites publish international offices.
    """
    text = clean_text(raw)
    if not text:
        return _address_result(country=default_country)

    # US: City, ST 12345
    match = _ADDRESS_TAIL.search(text)
    if match and match.group("state").upper() in _STATE_CODES:
        street = text[: match.start()].strip(" ,")
        city = match.group("city").strip(" ,")
        # A <br> collapsed into a space can leave "901 Round Rock Ave Round Rock"
        # in the city group. If the last street suffix has words on both sides,
        # split there: street becomes "901 Round Rock Ave", city "Round Rock".
        suffixes = list(_ADDRESS_WORDS.finditer(city))
        if suffixes:
            last = suffixes[-1]
            before = city[: last.start()].strip(" ,")
            after = city[last.end():].strip(" ,")
            if before and after and (street.isdigit() or before):
                street = f"{street} {city[:last.end()]}".strip(" ,")
                city = after
        return _address_result(street, city, match.group("state"), match.group("postal"),
                               default_country)

    # US: City, Texas 78746
    match = _ADDRESS_FULL_STATE.search(text)
    if match and match.group("state").strip().lower() in _STATE_NAMES:
        return _address_result(text[: match.start()].strip(" ,"), match.group("city"),
                               _STATE_NAMES[match.group("state").strip().lower()],
                               match.group("postal"), default_country)

    # Canada: City, ON M5V 2T6
    match = _CA_POSTAL.search(text)
    if match and match.group("state").lower() in _CA_PROVINCES:
        return _address_result(text[: match.start()].strip(" ,"), match.group("city"),
                               match.group("state"), match.group("postal"), "Canada")

    # Australia: City NSW 2000
    match = _AU_POSTAL.search(text)
    if match and match.group("state").lower() in _AU_STATES:
        return _address_result(text[: match.start()].strip(" ,"), match.group("city"),
                               match.group("state"), match.group("postal"), "Australia")

    # UK: London SW1A 1AA (no comma required)
    match = _UK_POSTAL.search(text)
    if match:
        return _address_result(text[: match.start()].strip(" ,"), match.group("city"),
                               "", match.group("postal"), "United Kingdom")

    # US: City, ST (no postal)
    match = _ADDRESS_CITY_STATE.search(text)
    if match and match.group("state").upper() in _STATE_CODES:
        return _address_result(text[: match.start()].strip(" ,"), match.group("city"),
                               match.group("state"), "", default_country)

    # No recognised tail: keep the first segment as street, but never call a
    # suite/unit line a city.
    parts = [p.strip() for p in text.split(",") if p.strip()]
    street = parts[0] if parts else text
    city = ""
    state = ""
    postal = ""
    for part in parts[1:]:
        if _SUITE.match(part):
            street = f"{street}, {part}"
            continue
        if not city:
            city = part
        elif not state and len(part) <= 30:
            state = part
    return _address_result(street, city, state, postal, default_country)


def compose_address(street: str, city: str, state: str, postal: str, country: str = "") -> str:
    tail = " ".join(p for p in (state, postal) if p)
    body = f"{street}, {city} {tail}".strip(", ") if city else street
    if country and country.lower() not in {"united states", "usa", "us"}:
        return f"{body}, {country}".strip(", ")
    return body


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
    try:
        parts = urlparse(url)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    try:
        port_number = parts.port
    except ValueError:
        port_number = None
    port = f":{port_number}" if port_number and port_number not in (80, 443) else ""
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


def same_site(a: str, b: str) -> bool:
    """Same host, or a subdomain of it.

    Brokerage directories commonly put profiles on `agents.example.com` while the
    roster lives on `www.example.com`; that is still one site for our purposes.
    """
    ha, hb = host_of(a), host_of(b)
    if not ha or not hb:
        return False
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


def normalize_website(raw: object) -> str:
    """Normalise a website value without turning an email into a URL."""
    text = clean_text(raw)
    if not text or "@" in text:
        return ""
    text = text.split()[0].strip(".,;:()[]<>\"'")
    if text.lower().startswith(("javascript:", "mailto:", "tel:")):
        return ""
    if "." not in text and not text.startswith(("http://", "https://")):
        return ""
    url = canonical_url(text)
    if not url or not urlparse(url).hostname:
        return ""
    return url


def is_valid_url(value: object) -> bool:
    return bool(normalize_website(value))
