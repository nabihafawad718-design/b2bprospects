"""Run history and adapter health.

This is the maintenance differentiator. Every run records what it produced and
what shape the listing page had. Later runs are compared against that baseline so
a silent regression — a site redesign that halves the record count — is reported
instead of shipped.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import HealthReport, to_json
from .normalize import host_of
from .settings import HISTORY_FILE, SETTINGS, Settings, ensure_dirs

_BASELINE_RUNS = 5


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def append_run(summary: dict[str, Any]) -> str | None:
    """Append one run summary. Returns an error message, or None on success."""
    try:
        ensure_dirs()
        with open(HISTORY_FILE, "a", encoding="utf-8") as handle:
            handle.write(to_json(summary) + "\n")
        return None
    except OSError as exc:
        return f"Could not write run history: {exc}"


def load_history(limit: int = SETTINGS.run_history_limit) -> list[dict[str, Any]]:
    """Newest first. Corrupt lines are skipped rather than raising."""
    if not HISTORY_FILE.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    entries.reverse()
    return entries[:limit]


def runs_for(url_or_host: str, adapter_id: str = "") -> list[dict[str, Any]]:
    host = host_of(url_or_host)
    out = []
    for entry in load_history(limit=10_000):
        if host_of(str(entry.get("url", ""))) != host:
            continue
        if adapter_id and entry.get("adapter_id") != adapter_id:
            continue
        out.append(entry)
    return out


def clear_history() -> tuple[bool, str]:
    try:
        if HISTORY_FILE.exists():
            HISTORY_FILE.unlink()
        return True, "History cleared."
    except OSError as exc:
        return False, f"Could not clear history: {exc}"


def prune_history(keep: int = SETTINGS.run_history_limit) -> tuple[bool, str]:
    entries = load_history(limit=10_000)
    if len(entries) <= keep:
        return True, f"Nothing to prune ({len(entries)} runs kept)."
    kept = entries[:keep]
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as handle:
            for entry in reversed(kept):
                handle.write(to_json(entry) + "\n")
        return True, f"Pruned to the most recent {keep} runs."
    except OSError as exc:
        return False, f"Could not prune history: {exc}"


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
def _baseline(prior: list[dict[str, Any]]) -> list[dict[str, Any]]:
    usable = [e for e in prior if int(e.get("records") or 0) > 0]
    return usable[:_BASELINE_RUNS]


def compute_health(current: dict[str, Any], prior: list[dict[str, Any]],
                   settings: Settings = SETTINGS) -> HealthReport:
    report = HealthReport()
    usable = _baseline(prior)
    report.runs_compared = len(usable)

    if not usable:
        report.status = "first_run"
        report.reasons.append(
            "First successful run for this site. Today's figures become the baseline that "
            "future runs are checked against."
        )
        return report

    counts = [int(e.get("records") or 0) for e in usable]
    baseline_records = int(statistics.median(counts))
    report.baseline_records = baseline_records
    current_records = int(current.get("records") or 0)

    # --- structure ---------------------------------------------------------
    current_sig = str(current.get("structure_signature") or "")
    baseline_sig = str(usable[0].get("structure_signature") or "")
    if current_sig and baseline_sig and current_sig != baseline_sig:
        report.structure_changed = True
        report.reasons.append(
            "The listing markup signature changed since the last successful run. "
            "The site may have been redesigned; verify a sample of records."
        )

    # --- volume ------------------------------------------------------------
    if baseline_records > 0:
        if current_records == 0:
            report.status = "broken"
            report.reasons.append(
                f"Zero records, against a baseline of {baseline_records}. The adapter is almost "
                "certainly broken."
            )
        else:
            drop = (baseline_records - current_records) / baseline_records
            if drop >= settings.record_drop_broken:
                report.status = "broken"
                report.reasons.append(
                    f"Record count fell {drop:.0%} ({baseline_records} → {current_records}), "
                    "past the broken threshold. Treat this run as incomplete."
                )
            elif drop >= settings.record_drop_watch and report.status != "broken":
                report.status = "watch"
                report.reasons.append(
                    f"Record count fell {drop:.0%} ({baseline_records} → {current_records}). "
                    "Check pagination and filters before delivering."
                )
            elif current_records > baseline_records * 2 and baseline_records >= 5:
                report.reasons.append(
                    f"Record count more than doubled ({baseline_records} → {current_records}). "
                    "Confirm the site has not started listing unrelated profiles."
                )

    # --- field fill --------------------------------------------------------
    current_fill = current.get("field_fill") or {}
    baseline_fill: dict[str, list[float]] = {}
    for entry in usable:
        for key, value in (entry.get("field_fill") or {}).items():
            baseline_fill.setdefault(key, []).append(float(value))
    for key, values in baseline_fill.items():
        if not values:
            continue
        median = statistics.median(values)
        report.baseline_fill[key] = round(median, 3)
        now = float(current_fill.get(key, 0.0))
        if median >= 0.4 and (median - now) >= settings.fill_drop_points:
            if report.status == "ok":
                report.status = "watch"
            report.reasons.append(
                f"{key} coverage dropped from {median:.0%} to {now:.0%} of records. "
                "That field's extraction rule is the likely cause."
            )

    # --- errors ------------------------------------------------------------
    errors = current.get("errors") or []
    blocked = [e for e in errors if "refused access" in str(e).lower() or "403" in str(e)]
    if blocked:
        report.status = "broken"
        report.reasons.append(
            "The site refused access during this run. Do not retry in a loop; check the "
            "site's terms and your permission to collect."
        )

    if report.status == "ok" and report.reasons:
        report.status = "watch"
    return report


# ---------------------------------------------------------------------------
# Dashboard aggregates
# ---------------------------------------------------------------------------
def health_dashboard(limit: int = 50) -> list[dict[str, Any]]:
    """One row per site+adapter, newest first."""
    entries = load_history(limit=10_000)
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        key = (host_of(str(entry.get("url", ""))), str(entry.get("adapter_id", "")))
        if key in seen:
            continue
        prior = [e for e in entries if (
            host_of(str(e.get("url", ""))) == key[0]
            and str(e.get("adapter_id", "")) == key[1]
            and e.get("run_id") != entry.get("run_id")
        )]
        health = compute_health(entry, prior)
        seen[key] = {
            "host": key[0],
            "adapter_id": key[1],
            "adapter_label": entry.get("adapter_label", ""),
            "last_run": entry.get("finished_at", ""),
            "records": entry.get("records", 0),
            "success_rate": entry.get("success_rate", 0.0),
            "status": health.status,
            "reasons": health.reasons,
            "baseline_records": health.baseline_records,
            "structure_changed": health.structure_changed,
            "run_id": entry.get("run_id", ""),
        }
        if len(seen) >= limit:
            break
    return list(seen.values())


def today_count() -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(1 for entry in load_history(limit=10_000) if str(entry.get("finished_at", "")).startswith(today))


def export_history(path: Path) -> tuple[bool, str]:
    entries = load_history(limit=10_000)
    if not entries:
        return False, "No run history to export."
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(entries, handle, indent=2, ensure_ascii=False)
        return True, f"Wrote {len(entries)} runs to {path.name}."
    except OSError as exc:
        return False, f"Could not export history: {exc}"
