"""Central configuration for B2BProspects Data Extraction.

Every operational knob lives here so a solo operator can tune behaviour without
touching engine code. Values can be overridden with environment variables, which
keeps Streamlit secrets optional.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("B2B_DATA_DIR", PROJECT_ROOT / "data"))
RUNS_DIR = DATA_DIR / "runs"
HISTORY_FILE = RUNS_DIR / "history.jsonl"
EXPORT_DIR = DATA_DIR / "exports"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
ADAPTER_CONFIG_DIR = PROJECT_ROOT / "core" / "adapters" / "sites"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_choice(name: str, default: str, choices: tuple[str, ...]) -> str:
    value = str(os.environ.get(name, default)).strip().lower()
    return value if value in choices else default


# "warn" keeps robots.txt visible but does not reject a page on its own. This is
# deliberate: robots.txt is a crawling convention, not a definitive statement of
# permission, and the operator decides after seeing the notice. Set
# B2B_ROBOTS_POLICY=enforce to restore hard blocking, or "ignore" to stay silent.
ROBOTS_POLICY = _env_choice("B2B_ROBOTS_POLICY", "warn", ("warn", "enforce", "ignore"))


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Frozen so a run cannot mutate global behaviour halfway."""

    # --- Identity / politeness -------------------------------------------------
    user_agent: str = (
        "B2BProspectsDataExtraction/1.1 "
        "(business directory research; contact: operator@b2bprospects.io)"
    )
    request_timeout: tuple[int, int] = (10, 30)  # (connect, read)
    request_delay_seconds: float = _env_float("B2B_REQUEST_DELAY", 1.0)
    max_retries: int = _env_int("B2B_MAX_RETRIES", 2)
    max_page_bytes: int = 3_000_000
    # True means "enforce". Kept for backwards compatibility with existing code
    # and tests; the product default is warn-only via robots_policy.
    respect_robots_txt: bool = _env_bool("B2B_RESPECT_ROBOTS", False)
    robots_policy: str = ROBOTS_POLICY
    # Permit 127.0.0.1/localhost. Only for the bundled demo and local tests.
    allow_local_urls: bool = _env_bool("B2B_ALLOW_LOCAL", False)

    # --- Extraction caps -------------------------------------------------------
    max_list_pages: int = _env_int("B2B_MAX_LIST_PAGES", 20)
    max_profiles: int = _env_int("B2B_MAX_PROFILES", 500)
    max_profiles_per_run_hard_cap: int = 5_000
    max_extra_fields: int = _env_int("B2B_MAX_EXTRA_FIELDS", 20)

    # --- Quality thresholds ----------------------------------------------------
    min_success_rate: float = 0.70          # share of attempted profiles that produced a named record
    min_field_fill_rate: float = 0.50       # per expected field, warn below this
    min_contact_share: float = 0.80         # share of records that must carry a phone or email
    record_drop_watch: float = 0.20         # 20% fewer records than baseline -> WATCH
    record_drop_broken: float = 0.50        # 50% fewer records than baseline -> BROKEN
    fill_drop_points: float = 0.25          # 25-point fill-rate drop -> WATCH

    # --- Browser ---------------------------------------------------------------
    browser_headless: bool = _env_bool("B2B_BROWSER_HEADLESS", True)
    browser_nav_timeout_ms: int = _env_int("B2B_BROWSER_NAV_TIMEOUT", 30_000)
    browser_idle_timeout_ms: int = _env_int("B2B_BROWSER_IDLE_TIMEOUT", 8_000)
    browser_block_media: bool = _env_bool("B2B_BROWSER_BLOCK_MEDIA", False)
    browser_scroll_steps: int = _env_int("B2B_BROWSER_SCROLL_STEPS", 10)
    browser_reveal_contacts: bool = _env_bool("B2B_BROWSER_REVEAL_CONTACTS", True)
    browser_screenshot_on_error: bool = _env_bool("B2B_BROWSER_SCREENSHOTS", True)
    prefer_embedded: bool = _env_bool("B2B_PREFER_EMBEDDED", True)
    # Static mode: use complete listing cards instead of opening every profile.
    prefer_listing_records: bool = _env_bool("B2B_PREFER_LISTING_RECORDS", True)
    screenshot_dir: Path = SCREENSHOT_DIR
    # A visual snapshot is passed through Streamlit's component channel, so keep
    # it bounded. Large pages fall back to column-sample mapping.
    max_snapshot_bytes: int = _env_int("B2B_MAX_SNAPSHOT_BYTES", 4_000_000)

    # --- Product flags ---------------------------------------------------------
    # This is an internal operator tool. The $49 export paywall from the original
    # prototype is preserved but off by default so it never locks your own exports.
    license_enabled: bool = _env_bool("B2B_LICENSE_ENABLED", False)
    license_key: str = os.environ.get("B2B_LICENSE_KEY", "B2BPROSPECTS2026")
    checkout_url: str = "https://b2bprospects.io"

    # --- Exchange --------------------------------------------------------------
    default_export_basename: str = "b2bprospects_extract"
    run_history_limit: int = 500

    # --- Platform --------------------------------------------------------------
    browser_automation_available: bool = False  # flipped True by engine if playwright imports

    extra: dict = field(default_factory=dict)

    @property
    def robots_enforced(self) -> bool:
        return self.respect_robots_txt or self.robots_policy == "enforce"

    @property
    def robots_silent(self) -> bool:
        return self.robots_policy == "ignore" and not self.respect_robots_txt


SETTINGS = Settings()


def ensure_dirs() -> None:
    for path in (DATA_DIR, RUNS_DIR, EXPORT_DIR, SNAPSHOT_DIR, SCREENSHOT_DIR):
        path.mkdir(parents=True, exist_ok=True)
