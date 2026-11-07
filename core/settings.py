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


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Frozen so a run cannot mutate global behaviour halfway."""

    # --- Identity / politeness -------------------------------------------------
    user_agent: str = (
        "B2BProspectsDataExtraction/1.0 "
        "(business directory research; contact: operator@b2bprospects.io)"
    )
    request_timeout: tuple[int, int] = (10, 30)  # (connect, read)
    request_delay_seconds: float = _env_float("B2B_REQUEST_DELAY", 1.0)
    max_retries: int = _env_int("B2B_MAX_RETRIES", 2)
    max_page_bytes: int = 3_000_000
    respect_robots_txt: bool = _env_bool("B2B_RESPECT_ROBOTS", True)

    # --- Extraction caps -------------------------------------------------------
    max_list_pages: int = _env_int("B2B_MAX_LIST_PAGES", 20)
    max_profiles: int = _env_int("B2B_MAX_PROFILES", 500)
    max_profiles_per_run_hard_cap: int = 5_000

    # --- Quality thresholds ----------------------------------------------------
    min_success_rate: float = 0.70          # share of attempted profiles that produced a named record
    min_field_fill_rate: float = 0.50       # per expected field, warn below this
    min_contact_share: float = 0.80         # share of records that must carry a phone or email
    record_drop_watch: float = 0.20         # 20% fewer records than baseline -> WATCH
    record_drop_broken: float = 0.50        # 50% fewer records than baseline -> BROKEN
    fill_drop_points: float = 0.25          # 25-point fill-rate drop -> WATCH

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


SETTINGS = Settings()


def ensure_dirs() -> None:
    for path in (DATA_DIR, RUNS_DIR, EXPORT_DIR):
        path.mkdir(parents=True, exist_ok=True)
