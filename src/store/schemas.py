from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional


def _row_get(row: Optional[Mapping[str, Any]], key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "keys"):
        try:
            if key in row.keys():
                return row[key]
        except Exception:
            pass
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


@dataclass
class Patient:
    patient_id: str
    ward_id: str | None
    bed_id: str | None
    sex: str | None
    age: int | None
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Patient":
        return cls(
            patient_id=_row_get(row, "patient_id", ""),
            ward_id=_row_get(row, "ward_id"),
            bed_id=_row_get(row, "bed_id"),
            sex=_row_get(row, "sex"),
            age=_row_get(row, "age"),
            created_at=_row_get(row, "created_at", ""),
        )


@dataclass
class DailyLog:
    patient_id: str
    date: str
    diet: str | None
    water_ml: int | None
    sleep_hours: float | None
    symptoms_json: str | None
    patient_reported_meds_json: str | None
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "DailyLog":
        return cls(
            patient_id=_row_get(row, "patient_id", ""),
            date=_row_get(row, "date", ""),
            diet=_row_get(row, "diet"),
            water_ml=_row_get(row, "water_ml"),
            sleep_hours=_row_get(row, "sleep_hours"),
            symptoms_json=_row_get(row, "symptoms_json"),
            patient_reported_meds_json=_row_get(row, "patient_reported_meds_json"),
            created_at=_row_get(row, "created_at", ""),
        )


@dataclass
class NurseAdmin:
    patient_id: str
    timestamp: str
    vitals_json: str | None
    administered_meds_json: str | None
    notes: str | None
    nurse_id: str | None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "NurseAdmin":
        return cls(
            patient_id=_row_get(row, "patient_id", ""),
            timestamp=_row_get(row, "timestamp", ""),
            vitals_json=_row_get(row, "vitals_json"),
            administered_meds_json=_row_get(row, "administered_meds_json"),
            notes=_row_get(row, "notes"),
            nurse_id=_row_get(row, "nurse_id"),
        )


@dataclass
class Assessment:
    assessment_id: str
    patient_id: str
    timestamp: str
    route_tag: str | None
    primary_basis: str | None
    diagnosis_json: str
    audit_json: str
    reverse_json: str
    rag_evidence_json: str | None
    tool_trace_json: str | None
    gaps_json: str | None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Assessment":
        return cls(
            assessment_id=_row_get(row, "assessment_id", ""),
            patient_id=_row_get(row, "patient_id", ""),
            timestamp=_row_get(row, "timestamp", ""),
            route_tag=_row_get(row, "route_tag"),
            primary_basis=_row_get(row, "primary_basis"),
            diagnosis_json=_row_get(row, "diagnosis_json", "{}"),
            audit_json=_row_get(row, "audit_json", "{}"),
            reverse_json=_row_get(row, "reverse_json", "{}"),
            rag_evidence_json=_row_get(row, "rag_evidence_json"),
            tool_trace_json=_row_get(row, "tool_trace_json"),
            gaps_json=_row_get(row, "gaps_json"),
        )


@dataclass
class ChatSummary:
    patient_id: str
    timestamp: str
    role: str
    summary_text: str
    topic_tag: str | None
    key_flags_json: str | None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "ChatSummary":
        return cls(
            patient_id=_row_get(row, "patient_id", ""),
            timestamp=_row_get(row, "timestamp", ""),
            role=_row_get(row, "role", ""),
            summary_text=_row_get(row, "summary_text", ""),
            topic_tag=_row_get(row, "topic_tag"),
            key_flags_json=_row_get(row, "key_flags_json"),
        )


@dataclass
class PatientCard:
    card_id: str
    patient_id: str
    ward_id: str | None
    status: str
    content_md: str
    source_assessment_id: str | None
    created_at: str
    updated_at: str | None
    author_role: str | None
    card_type: str | None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "PatientCard":
        return cls(
            card_id=_row_get(row, "card_id", ""),
            patient_id=_row_get(row, "patient_id", ""),
            ward_id=_row_get(row, "ward_id"),
            status=_row_get(row, "status", ""),
            content_md=_row_get(row, "content_md", ""),
            source_assessment_id=_row_get(row, "source_assessment_id"),
            created_at=_row_get(row, "created_at", ""),
            updated_at=_row_get(row, "updated_at"),
            author_role=_row_get(row, "author_role"),
            card_type=_row_get(row, "card_type"),
        )


@dataclass
class Handover:
    handover_id: str
    patient_id: str
    ward_id: str | None
    timestamp: str
    created_by_role: str | None
    created_by_id: str | None
    sbar_md: str
    key_points_json: str | None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Handover":
        return cls(
            handover_id=_row_get(row, "handover_id", ""),
            patient_id=_row_get(row, "patient_id", ""),
            ward_id=_row_get(row, "ward_id"),
            timestamp=_row_get(row, "timestamp", ""),
            created_by_role=_row_get(row, "created_by_role"),
            created_by_id=_row_get(row, "created_by_id"),
            sbar_md=_row_get(row, "sbar_md", ""),
            key_points_json=_row_get(row, "key_points_json"),
        )


@dataclass
class HandoverRecord:
    handover_id: str
    patient_id: str
    ward_id: str | None
    created_at: str
    created_by_role: str
    sbar_md: str
    key_points_json: str | None
    related_snapshot_id: str | None
    version: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "HandoverRecord":
        return cls(
            handover_id=_row_get(row, "handover_id", ""),
            patient_id=_row_get(row, "patient_id", ""),
            ward_id=_row_get(row, "ward_id"),
            created_at=_row_get(row, "created_at", _row_get(row, "timestamp", "")),
            created_by_role=_row_get(row, "created_by_role", ""),
            sbar_md=_row_get(row, "sbar_md", ""),
            key_points_json=_row_get(row, "key_points_json"),
            related_snapshot_id=_row_get(row, "related_snapshot_id"),
            version=_row_get(row, "version", 0),
        )


@dataclass
class RiskSnapshot:
    snapshot_id: str
    patient_id: str
    ward_id: str | None
    computed_at: str
    risk_level: str
    risk_score: int
    flags_json: str
    next_actions_json: str
    rules_version: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "RiskSnapshot":
        return cls(
            snapshot_id=_row_get(row, "snapshot_id", ""),
            patient_id=_row_get(row, "patient_id", ""),
            ward_id=_row_get(row, "ward_id"),
            computed_at=_row_get(row, "computed_at", ""),
            risk_level=_row_get(row, "risk_level", ""),
            risk_score=_row_get(row, "risk_score", 0),
            flags_json=_row_get(row, "flags_json", "[]"),
            next_actions_json=_row_get(row, "next_actions_json", "[]"),
            rules_version=_row_get(row, "rules_version", ""),
        )


@dataclass
class CareCard:
    card_id: str
    patient_id: str
    ward_id: str | None
    created_at: str
    created_by_role: str
    status: str
    card_level: str
    card_type: str | None
    language: str
    title: str
    one_liner: str
    bullets_json: str
    red_flags_json: str
    followup_json: str | None
    text_md: str | None
    audio_path: str | None
    source_assessment_id: str | None
    version: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "CareCard":
        return cls(
            card_id=_row_get(row, "card_id", ""),
            patient_id=_row_get(row, "patient_id", ""),
            ward_id=_row_get(row, "ward_id"),
            created_at=_row_get(row, "created_at", ""),
            created_by_role=_row_get(row, "created_by_role", ""),
            status=_row_get(row, "status", ""),
            card_level=_row_get(row, "card_level", ""),
            card_type=_row_get(row, "card_type"),
            language=_row_get(row, "language", ""),
            title=_row_get(row, "title", ""),
            one_liner=_row_get(row, "one_liner", ""),
            bullets_json=_row_get(row, "bullets_json", "[]"),
            red_flags_json=_row_get(row, "red_flags_json", "[]"),
            followup_json=_row_get(row, "followup_json"),
            text_md=_row_get(row, "text_md"),
            audio_path=_row_get(row, "audio_path"),
            source_assessment_id=_row_get(row, "source_assessment_id"),
            version=_row_get(row, "version", 0),
        )


@dataclass
class StaffAccount:
    staff_id: str
    role: str
    ward_id: str | None
    name: str | None
    email: str | None
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "StaffAccount":
        return cls(
            staff_id=_row_get(row, "staff_id", ""),
            role=_row_get(row, "role", ""),
            ward_id=_row_get(row, "ward_id"),
            name=_row_get(row, "name"),
            email=_row_get(row, "email"),
            created_at=_row_get(row, "created_at", ""),
        )


__all__ = [
    "Patient",
    "DailyLog",
    "NurseAdmin",
    "Assessment",
    "ChatSummary",
    "PatientCard",
    "Handover",
    "HandoverRecord",
    "RiskSnapshot",
    "CareCard",
    "StaffAccount",
]
