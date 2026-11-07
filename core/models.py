"""Typed data structures shared by the inspector, adapters, engine and UI."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Field catalogue
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    description: str
    core: bool = False          # collected by default when available
    pii: bool = True            # personal-data shaped, shown in the consent notice


FIELD_CATALOGUE: tuple[FieldSpec, ...] = (
    FieldSpec("agent_name", "Agent name", "Full name of the professional listed on the profile.", core=True),
    FieldSpec("brokerage_name", "Brokerage name", "Firm or agency the professional is listed under.", core=True),
    FieldSpec("phone", "Public phone", "Phone number printed on the public profile.", core=True),
    FieldSpec("email", "Public email", "Email address printed on the public profile.", core=True),
    FieldSpec("street_address", "Office address", "Street portion of the listed office address.", core=True),
    FieldSpec("city", "City", "City from the listed office address.", core=True),
    FieldSpec("state", "State / region", "State or region code from the listed address.", core=True),
    FieldSpec("postal_code", "Postal code", "Postal or ZIP code from the listed address.", core=False),
    FieldSpec("country", "Country", "Country from the listed address, when published.", core=False),
    FieldSpec("website", "Website", "Public website listed for the agent or office.", core=False),
    FieldSpec("profile_url", "Profile URL", "Canonical URL of the source profile page.", core=True),
    FieldSpec("source_url", "Source URL", "Directory page the record was collected from.", core=True),
)

FIELD_BY_KEY: dict[str, FieldSpec] = {f.key: f for f in FIELD_CATALOGUE}
FIELD_KEYS: tuple[str, ...] = tuple(f.key for f in FIELD_CATALOGUE)
CORE_FIELD_KEYS: tuple[str, ...] = tuple(f.key for f in FIELD_CATALOGUE if f.core)
# Provenance columns appended to every export. Not user-selectable.
META_KEYS: tuple[str, ...] = ("collected_at", "extraction_method", "adapter_id", "quality_flags")

EXPORT_HEADERS: dict[str, str] = {f.key: f.label for f in FIELD_CATALOGUE} | {
    "collected_at": "Collected (UTC)",
    "extraction_method": "Extraction method",
    "adapter_id": "Adapter",
    "quality_flags": "Quality flags",
}


def pretty_key(key: str) -> str:
    """Turn a user-named extra field into a readable column header."""
    text = str(key or "").replace("_", " ").replace("-", " ").strip()
    if not text:
        return "Field"
    if text.islower() or text.isupper():
        return text.title()
    return text


def header_for(key: str) -> str:
    return EXPORT_HEADERS.get(key) or pretty_key(key)


# ---------------------------------------------------------------------------
# Raw vs. clean records
# ---------------------------------------------------------------------------
@dataclass
class RawRecord:
    """What an adapter observed, before normalization or validation."""

    fields: dict[str, Any] = field(default_factory=dict)
    profile_url: str | None = None
    source_url: str = ""
    method: str = "heuristic"
    warnings: list[str] = field(default_factory=list)

    def get(self, key: str) -> Any:
        return self.fields.get(key)


@dataclass
class Record:
    """A normalized record ready for review and export."""

    agent_name: str = ""
    brokerage_name: str = ""
    phone: str = ""
    email: str = ""
    street_address: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = ""
    website: str = ""
    profile_url: str = ""
    source_url: str = ""
    collected_at: str = ""
    extraction_method: str = ""
    adapter_id: str = ""
    quality_flags: list[str] = field(default_factory=list)
    duplicate_of: str = ""
    phone_digits: str = ""      # internal, used for matching; not exported
    # Arbitrary columns the operator chose to keep (title, licence number, ...).
    extra: dict[str, str] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        row = {k: getattr(self, k) for k in FIELD_KEYS}
        for key, value in (self.extra or {}).items():
            row.setdefault(str(key), value)
        row["collected_at"] = self.collected_at
        row["extraction_method"] = self.extraction_method
        row["adapter_id"] = self.adapter_id
        row["quality_flags"] = "; ".join(self.quality_flags)
        return row

    def has_contact(self) -> bool:
        return bool(self.phone or self.email)


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------
@dataclass
class InspectionReport:
    url: str = ""
    final_url: str = ""
    host: str = ""
    status_code: int = 0
    page_title: str = ""
    robots_allowed: bool = True
    robots_detail: str = ""

    # structured data
    jsonld_blocks: int = 0
    jsonld_types: list[str] = field(default_factory=list)
    jsonld_fields: list[str] = field(default_factory=list)

    # repeated listing blocks
    container_selector: str = ""
    container_signature: str = ""
    container_count: int = 0
    profile_link_count: int = 0
    profile_link_samples: list[str] = field(default_factory=list)
    profile_link_pattern: str = ""

    # embedded JSON payloads
    embedded_record_count: int = 0
    embedded_payloads: list[str] = field(default_factory=list)

    # navigation
    pagination_type: str = "none"       # next_link | numbered | query_param | none
    pagination_pages: int = 0
    pagination_param: str = ""
    load_more_detected: bool = False
    sitemap_found: bool = False

    # capability / preview
    requires_javascript: bool = False
    estimated_records: int = 0
    available_fields: list[str] = field(default_factory=list)
    suggested_fields: dict[str, str] = field(default_factory=dict)
    sample_records: list[dict[str, Any]] = field(default_factory=list)
    detected_layers: list[str] = field(default_factory=list)
    recommended_adapter: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Browser rendering / visual extraction
# ---------------------------------------------------------------------------
@dataclass
class ColumnProfile:
    """One detected "column" inside a repeated listing row.

    A column is identified by its position path inside the row, not by CSS, so an
    operator can choose it by looking at example values rather than selectors.
    """

    index: int
    path: str
    tag: str
    sample_values: list[str] = field(default_factory=list)
    fill_rate: float = 0.0
    href_rate: float = 0.0
    kind_hint: str = "unknown"          # name|first_name|last_name|phone|email|address|city|state|postal|title|url|text
    label: str = ""
    header: str = ""                    # table header or nearby label, when one was found

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StructureModel:
    """A detected repeated region: the row template plus its columns."""

    container_selector: str = ""
    row_selector: str = ""
    row_count: int = 0
    columns: list[ColumnProfile] = field(default_factory=list)
    signature: str = ""
    heading: str = ""
    score: float = 0.0
    method: str = "browser-dom"          # browser-dom | link-cluster
    headers: list[str] = field(default_factory=list)

    def column(self, index: int | None) -> ColumnProfile | None:
        if index is None:
            return None
        for column in self.columns:
            if column.index == index:
                return column
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BrowserInspection:
    """Result of opening a page in a real browser and analysing the rendered DOM."""

    url: str = ""
    final_url: str = ""
    title: str = ""
    status_code: int = 0
    rendered: bool = False
    rendered_html: str = ""
    row_count: int = 0
    structure: StructureModel | None = None
    candidates: list[StructureModel] = field(default_factory=list)
    jsonld_types: list[str] = field(default_factory=list)
    jsonld_record_count: int = 0
    embedded_keys: list[str] = field(default_factory=list)
    embedded_record_count: int = 0
    embedded_payloads: list[str] = field(default_factory=list)
    link_cluster_count: int = 0
    profile_link_count: int = 0
    available_fields: list[str] = field(default_factory=list)
    suggested_mapping: dict[str, int] = field(default_factory=dict)
    sample_records: list[dict[str, Any]] = field(default_factory=list)
    detected_layers: list[str] = field(default_factory=list)
    page_text_length: int = 0
    robots_allowed: bool = True
    robots_detail: str = ""
    pagination: dict[str, Any] = field(default_factory=dict)
    blocked: bool = False
    block_reason: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("rendered_html", None)  # large; never serialised
        data["structure"] = self.structure.to_dict() if self.structure else None
        data["candidates"] = [c.to_dict() for c in self.candidates]
        return data


# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------
@dataclass
class QualityReport:
    attempted: int = 0
    extracted: int = 0
    unique: int = 0
    duplicates_removed: int = 0
    missing_names: int = 0
    with_any_contact: int = 0
    contact_share: float = 0.0
    success_rate: float = 0.0
    field_fill: dict[str, float] = field(default_factory=dict)
    issue_counts: dict[str, int] = field(default_factory=dict)
    level: str = "ok"                    # ok | watch | broken
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@dataclass
class HealthReport:
    status: str = "ok"                   # ok | watch | broken | first_run
    reasons: list[str] = field(default_factory=list)
    baseline_records: int | None = None
    baseline_fill: dict[str, float] = field(default_factory=dict)
    structure_changed: bool = False
    runs_compared: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------
@dataclass
class MethodAttempt:
    """One extraction strategy that was tried, and the honest outcome."""

    method: str
    label: str
    status: str = "pending"             # ok | empty | failed | skipped
    records: int = 0
    detail: str = ""
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractionDiagnosis:
    """Why a run used the method it did, or why it produced nothing.

    The UI shows this verbatim so an operator can tell the difference between
    "the site refused access", "the page is JavaScript-only", "the mapping is
    wrong" and "the pagination stalled".
    """

    attempts: list[MethodAttempt] = field(default_factory=list)
    selected_method: str = ""
    selected_label: str = ""
    failure_code: str = ""              # see core.diagnostics.CODES
    summary: str = ""
    recommendations: list[str] = field(default_factory=list)
    blocked: bool = False
    blocked_reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.selected_method) and not self.failure_code

    def add(self, method: str, label: str, status: str, records: int = 0,
            detail: str = "", seconds: float = 0.0) -> MethodAttempt:
        attempt = MethodAttempt(method, label, status, records, detail, round(seconds, 2))
        self.attempts.append(attempt)
        return attempt

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
@dataclass
class RunResult:
    run_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    url: str = ""
    adapter_id: str = ""
    adapter_label: str = ""
    selected_fields: list[str] = field(default_factory=list)
    inspection: InspectionReport = field(default_factory=InspectionReport)
    records: list[Record] = field(default_factory=list)
    quality: QualityReport = field(default_factory=QualityReport)
    health: HealthReport = field(default_factory=HealthReport)
    diagnosis: ExtractionDiagnosis = field(default_factory=ExtractionDiagnosis)
    pages_fetched: int = 0
    profiles_attempted: int = 0
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    # Free-form facts about the delivered dataset (filters applied, cleaning mode,
    # page cap reached). Written into the Excel Run Summary and shown in the UI.
    export_context: dict[str, str] = field(default_factory=dict)
    screenshots: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "url": self.url,
            "adapter_id": self.adapter_id,
            "adapter_label": self.adapter_label,
            "selected_fields": self.selected_fields,
            "records": self.quality.unique,
            "attempted": self.quality.attempted,
            "duplicates_removed": self.quality.duplicates_removed,
            "success_rate": round(self.quality.success_rate, 4),
            "field_fill": {k: round(v, 4) for k, v in self.quality.field_fill.items()},
            "level": self.quality.level,
            "health_status": self.health.status,
            "health_reasons": self.health.reasons,
            "structure_signature": self.inspection.container_signature,
            "container_selector": self.inspection.container_selector,
            "pages_fetched": self.pages_fetched,
            "profiles_attempted": self.profiles_attempted,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "errors": self.errors[:20],
            "warnings": (self.inspection.warnings + self.quality.warnings + self.health.reasons)[:30],
            "export_context": self.export_context,
            "screenshots": self.screenshots,
            "diagnosis": self.diagnosis.to_dict() if self.diagnosis.attempts else {},
        }


def new_run_id(url: str) -> str:
    """Stable-ish, sortable, human-readable identifier."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:6]
    return f"{stamp}-{digest}"


def signature(*parts: str) -> str:
    joined = "␟".join(p for p in parts if p)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]


def json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, Iterable):
        return [json_safe(v) for v in value]
    return str(value)


def to_json(obj: Any, **kwargs: Any) -> str:
    return json.dumps(json_safe(obj), ensure_ascii=False, **kwargs)
