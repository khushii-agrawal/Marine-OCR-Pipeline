"""Map raw strategy fields into the canonical spare-parts schema."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz, process


FUZZY_THRESHOLD = 82
PART_NO_FALLBACK_PATTERN = r"^.{3,}$"

_BUILTIN_SYNONYMS: dict[str, list[str]] = {
    "part_no": [
        "part_no",
        "part no",
        "part no.",
        "mfg_part_no",
        "mfg part no",
        "order no",
        "bestell-nr",
        "identnr",
        "code no",
        "pt.-no",
    ],
    "description": [
        "description",
        "designation",
        "name",
        "name_of_spare",
        "name of spare",
        "part name",
        "benennung",
        "bezeichnung",
    ],
    "qty": ["qty", "quantity", "work_qty", "stck.", "amount"],
    "unit": ["unit", "uom"],
    "drawing_ref": ["drawing_ref", "drawing", "drawing no", "table", "figure"],
    "component": ["component"],
    "sub_component": ["sub_component", "sub component", "assembly", "section"],
    "manufacturer": ["manufacturer", "maker"],
    "model": ["model", "type"],
}

_ASSUMPTION_KEYS = {
    "part_no": "part_no",
    "description": "description",
    "qty": "qty",
    "quantity": "qty",
    "unit": "unit",
    "uom": "unit",
    "drawing_ref": "drawing_ref",
    "drawing": "drawing_ref",
    "table_ref": "drawing_ref",
    "position": "position",
    "pos": "position",
    "material": "material",
    "component": "component",
    "sub_component": "sub_component",
    "manufacturer": "manufacturer",
    "model": "model",
}

_SUBSTITUTION_FIELDS = {"part_no", "qty", "drawing_ref"}


@dataclass(frozen=True)
class Resolution:
    source: str
    method: str
    score: float | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "method": self.method,
            "score": self.score,
            "note": self.note,
        }


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _schema_fields(schema: dict[str, Any]) -> list[str]:
    properties = schema.get("properties", {})
    return list(properties.keys())


def _canonical_assumption_key(key: str) -> str:
    base = re.sub(r"_aliases$", "", key)
    return _ASSUMPTION_KEYS.get(base, base)


def build_synonym_map(manufacturer_profile: dict[str, Any], schema: dict[str, Any]) -> dict[str, list[str]]:
    """Return canonical field -> aliases, accepting both profile formats."""
    fields = _schema_fields(schema)
    synonyms: dict[str, list[str]] = {
        field: list(dict.fromkeys([field, *_BUILTIN_SYNONYMS.get(field, [])]))
        for field in fields
    }

    for field, aliases in manufacturer_profile.get("column_synonyms", {}).items():
        if field in synonyms and isinstance(aliases, list):
            synonyms[field].extend(str(alias) for alias in aliases)

    for key, aliases in manufacturer_profile.get("column_assumptions", {}).items():
        field = _canonical_assumption_key(key)
        if field in synonyms and isinstance(aliases, list):
            synonyms[field].extend(str(alias) for alias in aliases)

    return {field: list(dict.fromkeys(values)) for field, values in synonyms.items()}


def _resolve_key(raw_key: str, synonyms: dict[str, list[str]]) -> tuple[str | None, Resolution]:
    normalized_raw = _norm(raw_key)
    if not normalized_raw:
        return None, Resolution(raw_key, "unresolved", note="empty source column")

    for field, aliases in synonyms.items():
        if normalized_raw == _norm(field) or normalized_raw in {_norm(alias) for alias in aliases}:
            return field, Resolution(raw_key, "exact", 100.0)

    candidates: list[tuple[str, str]] = []
    for field, aliases in synonyms.items():
        for alias in aliases:
            candidates.append((field, alias))

    best = process.extractOne(raw_key, [alias for _, alias in candidates], scorer=fuzz.WRatio)
    if best is None:
        return None, Resolution(raw_key, "unresolved", note="no synonym candidates")

    alias, score, index = best
    field = candidates[index][0]
    if score >= FUZZY_THRESHOLD:
        return field, Resolution(raw_key, "fuzzy", float(score), note=f"matched alias '{alias}'")
    return None, Resolution(raw_key, "unresolved", float(score), note=f"best alias '{alias}' below threshold")


def _profile_default(manufacturer_profile: dict[str, Any], field: str) -> str:
    doc_profile = (manufacturer_profile.get("document_profiles") or [{}])[0]
    if field == "unit":
        return str(manufacturer_profile.get("default_unit", "") or "")
    return str(doc_profile.get(field, manufacturer_profile.get(field, "")) or "")


def _part_pattern(manufacturer_profile: dict[str, Any]) -> str:
    validation = manufacturer_profile.get("validation", {})
    return (
        manufacturer_profile.get("part_no_pattern")
        or validation.get("part_no_pattern")
        or PART_NO_FALLBACK_PATTERN
    )


def _matches_pattern(value: Any, pattern: str) -> bool:
    text = str(value or "").strip()
    return bool(text and re.fullmatch(pattern, text))


def _apply_substitutions(value: str, substitutions: dict[str, str]) -> str:
    updated = value
    for src, dst in substitutions.items():
        updated = updated.replace(str(src), str(dst))
    return updated


def _maybe_apply_ocr_substitutions(
    field: str,
    value: Any,
    manufacturer_profile: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    text = str(value or "").strip()
    substitutions = manufacturer_profile.get("known_ocr_substitutions", {}) or {}
    if not text or field not in _SUBSTITUTION_FIELDS or not substitutions:
        return text, None

    if field == "part_no":
        pattern = _part_pattern(manufacturer_profile)
        if _matches_pattern(text, pattern):
            return text, {"method": "not_applied", "reason": "part_no already matched pattern"}
        substituted = _apply_substitutions(text, substitutions)
        if substituted != text and _matches_pattern(substituted, pattern):
            return substituted, {"method": "conditional", "reason": "part_no matched after substitution"}
        return text, {"method": "not_applied", "reason": "substitution did not repair pattern"}

    if field == "qty":
        if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
            return text, {"method": "not_applied", "reason": "qty already numeric"}
        substituted = _apply_substitutions(text, substitutions)
        if re.fullmatch(r"\d+(?:[.,]\d+)?", substituted):
            return substituted, {"method": "conditional", "reason": "qty matched after substitution"}
        return text, {"method": "not_applied", "reason": "substitution did not repair numeric value"}

    substituted = _apply_substitutions(text, substitutions)
    if substituted != text:
        return substituted, {"method": "applied", "reason": "numeric/reference field"}
    return text, None


def map_record(raw_fields: dict, manufacturer_profile: dict, schema: dict) -> dict:
    """
    Map one raw extraction row into the canonical schema.

    Returns:
        {
            "record": {canonical fields...},
            "resolution_log": {
                "part_no": {"source": "...", "method": "exact|fuzzy|default|unresolved", ...},
                ...
            }
        }
    """
    synonyms = build_synonym_map(manufacturer_profile, schema)
    fields = _schema_fields(schema)
    mapped = {field: "" for field in fields}
    resolution_log: dict[str, dict[str, Any]] = {}

    for raw_key, raw_value in raw_fields.items():
        if raw_key in {"cell_confidences", "ocr_confidence", "metadata"}:
            continue
        field, resolution = _resolve_key(str(raw_key), synonyms)
        if field is None or field not in mapped:
            continue
        if mapped[field]:
            continue
        mapped[field] = str(raw_value or "").strip()
        resolution_log[field] = resolution.as_dict()

    for field in fields:
        if not mapped.get(field):
            default_value = _profile_default(manufacturer_profile, field)
            if default_value:
                mapped[field] = default_value
                resolution_log[field] = Resolution(field, "profile_default").as_dict()

    for field in fields:
        value, substitution_log = _maybe_apply_ocr_substitutions(field, mapped.get(field, ""), manufacturer_profile)
        mapped[field] = value
        if substitution_log:
            resolution_log.setdefault(field, Resolution(field, "unresolved").as_dict())
            resolution_log[field]["ocr_substitution"] = substitution_log

    if "cell_confidences" in raw_fields:
        mapped["cell_confidences"] = raw_fields.get("cell_confidences")
    if "ocr_confidence" in raw_fields:
        mapped["ocr_confidence"] = raw_fields.get("ocr_confidence")

    for field in fields:
        resolution_log.setdefault(field, Resolution(field, "unresolved").as_dict())

    return {"record": mapped, "resolution_log": resolution_log}
