"""Confidence gating for canonical spare-part records."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.schema_mapper import PART_NO_FALLBACK_PATTERN


HIGH_CONFIDENCE_THRESHOLD = 0.92
MEDIUM_CONFIDENCE_THRESHOLD = 0.75


@dataclass(frozen=True)
class ValidationResult:
    tier: str
    action: str
    confidence: float
    part_no_pattern_ok: bool
    missing_required: list[str] = field(default_factory=list)
    missing_non_critical: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "action": self.action,
            "confidence": self.confidence,
            "part_no_pattern_ok": self.part_no_pattern_ok,
            "missing_required": self.missing_required,
            "missing_non_critical": self.missing_non_critical,
            "reasons": self.reasons,
        }


def _required_fields(schema: dict[str, Any]) -> list[str]:
    explicit = schema.get("required", [])
    if explicit:
        return list(explicit)
    return [
        field
        for field, spec in schema.get("properties", {}).items()
        if isinstance(spec, dict) and spec.get("required") is True
    ]


def _record_confidence(record: dict[str, Any], ocr_confidence: float | None = None) -> float:
    if ocr_confidence is not None:
        return float(ocr_confidence)
    if record.get("ocr_confidence") is not None:
        return float(record["ocr_confidence"])
    confidences = record.get("cell_confidences") or []
    values = [float(conf) for conf in confidences if conf is not None]
    if values:
        return sum(values) / len(values)
    metadata = record.get("metadata") or {}
    meta_confidences = metadata.get("cell_confidences") or []
    values = [float(conf) for conf in meta_confidences if conf is not None]
    if values:
        return sum(values) / len(values)
    return 0.0


def _part_pattern(profile: dict[str, Any]) -> str:
    validation = profile.get("validation", {})
    return profile.get("part_no_pattern") or validation.get("part_no_pattern") or PART_NO_FALLBACK_PATTERN


def _part_no_pattern_ok(part_no: Any, profile: dict[str, Any]) -> bool:
    text = str(part_no or "").strip()
    return bool(text and re.fullmatch(_part_pattern(profile), text))


def validate_record(
    record: dict[str, Any],
    manufacturer_profile: dict[str, Any],
    schema: dict[str, Any],
    ocr_confidence: float | None = None,
) -> dict[str, Any]:
    """Apply the Phase 4 High/Medium/Low confidence gate."""
    confidence = _record_confidence(record, ocr_confidence)
    required = _required_fields(schema)
    missing_required = [
        field
        for field in required
        if not str(record.get(field, "") or "").strip()
    ]

    recommended = manufacturer_profile.get("recommended_fields", [])
    missing_non_critical = [
        field
        for field in recommended
        if field not in required and not str(record.get(field, "") or "").strip()
    ]

    pattern_ok = _part_no_pattern_ok(record.get("part_no", ""), manufacturer_profile)
    reasons: list[str] = []

    if confidence < MEDIUM_CONFIDENCE_THRESHOLD:
        reasons.append("ocr_confidence_below_0.75")
    if missing_required:
        reasons.append("required_field_missing")
    if not pattern_ok:
        reasons.append("part_no_pattern_mismatch")

    if reasons:
        return ValidationResult(
            tier="Low",
            action="hold_review",
            confidence=confidence,
            part_no_pattern_ok=pattern_ok,
            missing_required=missing_required,
            missing_non_critical=missing_non_critical,
            reasons=reasons,
        ).as_dict()

    if confidence > HIGH_CONFIDENCE_THRESHOLD and not missing_non_critical:
        return ValidationResult(
            tier="High",
            action="auto_commit",
            confidence=confidence,
            part_no_pattern_ok=pattern_ok,
            missing_required=[],
            missing_non_critical=[],
            reasons=["high_confidence_all_required_present_pattern_match"],
        ).as_dict()

    if confidence <= HIGH_CONFIDENCE_THRESHOLD:
        reasons.append("ocr_confidence_medium_range")
    if len(missing_non_critical) == 1:
        reasons.append("one_non_critical_field_missing")
    elif len(missing_non_critical) > 1:
        reasons.append("multiple_non_critical_fields_missing")

    return ValidationResult(
        tier="Medium",
        action="commit_flag",
        confidence=confidence,
        part_no_pattern_ok=pattern_ok,
        missing_required=[],
        missing_non_critical=missing_non_critical,
        reasons=reasons or ["medium_confidence_gate"],
    ).as_dict()
