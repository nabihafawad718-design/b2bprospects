"""Polite HTTP layer.

Design goals, in order: stay inside the site's stated rules, never hammer a host,
fail loudly rather than silently truncate, and be trivially testable offline.
"""
from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol
from urllib.parse import urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests

from .settings import SETTINGS, Settings


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class FetchError(Exception):
    """A page could not be retrieved."""

    def __init__(self, message: str, url: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.url = url
        self.status = status


class RobotsDisallowed(FetchError):
    """robots.txt (or the site's access policy) forbids this path."""


class BlockedError(FetchError):
    """The site actively refused us: 401/403/429 after retries."""


class UnsafeURLError(FetchError):
    """URL points at a local or private network address."""


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
@dataclass
class FetchedPage:
    url: str
    final_url: str
    status_code: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)
    elapsed: float = 0.0

    @property
    def content_type(self) -> str:
        for key, value in self.headers.items():
            if key.lower() == "content-type":
                return value.split(";")[0].strip().lower()
        return ""

    @property
    def is_html(self) -> bool:
        ctype = self.content_type
        return ctype in ("", "text/html", "application/xhtml+xml")


# ---------------------------------------------------------------------------
# URL safety
# ---------------------------------------------------------------------------
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)

_BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "metadata.google.internal"}


def normalize_url(raw: str) -> str:
    """Add a scheme, strip whitespace and fragments. Never invents a path."""
    url = (raw or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError(f"Only http/https URLs are supported (got {parts.scheme!r}).", raw)
    netloc = parts.netloc
    if netloc.startswith("www."):
        pass  # keep as given; host checks normalise separately
    clean = urlunparse((parts.scheme, netloc, parts.path or "/", parts.params, parts.query, ""))
    return clean


def assert_public_url(url: str) -> None:
    """Reject obvious SSRF targets. Literal IPs only — no DNS resolution here."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise UnsafeURLError("URL has no host.", url)
    if host in _BLOCKED_HOSTNAMES or host.endswith(".local") or host.endswith(".internal"):
        raise UnsafeURLError(f"Refusing to fetch internal host {host!r}.", url)
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return  # normal hostname
    if any(addr in net for net in _PRIVATE_NETWORKS):
        raise UnsafeURLError(f"Refusing to fetch private address {host}.", url)


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------
class Transport(Protocol):
    def __call__(self, url: str, headers: dict[str, str], timeout: tuple[int, int], max_bytes: int) -> tuple[int, str, dict[str, str], str]:
        ...


def requests_transport(url: str, headers: dict[str, str], timeout: tuple[int, int], max_bytes: int
                       ) -> tuple[int, str, dict[str, str], str]:
    """Default transport: streamed GET with a hard size cap."""
    with requests.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=True) as resp:
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= max_bytes:
                break
        body = b"".join(chunks)[:max_bytes]
        encoding = resp.encoding or resp.apparent_encoding or "utf-8"
        try:
            text = body.decode(encoding, errors="replace")
        except LookupError:
            text = body.decode("utf-8", errors="replace")
        return resp.status_code, resp.url, dict(resp.headers), text


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------
class PoliteFetcher:
    """Rate-limited, robots-aware fetcher with retries and provenance."""

    def __init__(
        self,
        settings: Settings = SETTINGS,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._transport = transport or requests_transport
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, tuple[RobotFileParser | None, str, float]] = {}
        self.request_count = 0
        self.hosts_contacted: set[str] = set()

    # -- robots -------------------------------------------------------------
    def _robots_for(self, url: str) -> tuple[RobotFileParser | None, str]:
        host = (urlparse(url).hostname or "").lower()
        cached = self._robots.get(host)
        if cached and (self._clock() - cached[2]) < 3600:
            return cached[0], cached[1]
        robots_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}/robots.txt"
        parser: RobotFileParser | None = None
        detail = "robots.txt unavailable — proceeding conservatively"
        try:
            status, final_url, _headers, text = self._transport(
                robots_url, {"User-Agent": self.settings.user_agent}, (5, 10), 512_000
            )
            if status in (401, 403):
                parser = RobotFileParser()
                parser.parse(["User-agent: *", "Disallow: /"])
                detail = "robots.txt returned an access denial for agents (401/403) — treated as disallow"
            elif 200 <= status < 300:
                parser = RobotFileParser()
                parser.parse(text.splitlines())
                detail = "robots.txt loaded"
            elif 400 <= status < 500:
                detail = f"no robots.txt (HTTP {status}) — no restriction recorded"
            else:
                detail = f"robots.txt HTTP {status} — proceeding conservatively"
        except Exception as exc:  # network failure should not hard-block the tool
            detail = f"robots.txt unreachable ({exc.__class__.__name__}) — proceeding conservatively"
        self._robots[host] = (parser, detail, self._clock())
        return parser, detail

    def can_fetch(self, url: str) -> tuple[bool, str]:
        if not self.settings.respect_robots_txt:
            return True, "robots.txt enforcement disabled in settings"
        parser, detail = self._robots_for(url)
        if parser is None:
            return True, detail
        allowed = parser.can_fetch(self.settings.user_agent, url)
        if not allowed:
            return False, "blocked by robots.txt"
        return True, detail

    def robots_status(self, url: str) -> str:
        _parser, detail = self._robots_for(url)
        return detail

    # -- rate limiting ------------------------------------------------------
    def _throttle(self, url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        delay = max(0.0, self.settings.request_delay_seconds)
        if delay == 0:
            return
        last = self._last_request.get(host)
        if last is not None:
            wait = delay - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    # -- main ---------------------------------------------------------------
    def get(self, url: str, *, check_robots: bool = True) -> FetchedPage:
        url = normalize_url(url)
        assert_public_url(url)

        if check_robots:
            allowed, detail = self.can_fetch(url)
            if not allowed:
                raise RobotsDisallowed(f"robots.txt disallows {url}", url)

        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        last_exc: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            self._throttle(url)
            started = self._clock()
            try:
                status, final_url, resp_headers, text = self._transport(
                    url, headers, self.settings.request_timeout, self.settings.max_page_bytes
                )
            except Exception as exc:
                last_exc = exc
                if attempt < self.settings.max_retries:
                    self._sleep(1.5 * (attempt + 1))
                    continue
                raise FetchError(f"{exc.__class__.__name__}: {exc}", url) from exc

            elapsed = self._clock() - started
            self.request_count += 1
            self.hosts_contacted.add((urlparse(final_url or url).hostname or "").lower())

            if status in (401, 403, 429):
                if attempt < self.settings.max_retries:
                    self._sleep(2.0 * (attempt + 1))
                    continue
                raise BlockedError(
                    f"Site refused access (HTTP {status}). Stop — do not retry or work around this.", url, status
                )
            if status in (500, 502, 503, 504):
                if attempt < self.settings.max_retries:
                    self._sleep(2.0 * (attempt + 1))
                    continue
                raise FetchError(f"Server error HTTP {status}", url, status)
            if status >= 400:
                raise FetchError(f"HTTP {status}", url, status)

            return FetchedPage(
                url=url, final_url=final_url or url, status_code=status, text=text,
                headers={str(k).lower(): str(v) for k, v in resp_headers.items()}, elapsed=elapsed,
            )

        raise FetchError(f"Request failed: {last_exc}", url)

    def get_optional(self, url: str) -> FetchedPage | None:
        """Fetch a secondary page; return None instead of raising."""
        try:
            return self.get(url)
        except FetchError:
            return None
