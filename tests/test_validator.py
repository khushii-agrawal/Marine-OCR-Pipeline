import json
from pathlib import Path

from core.validator import validate_record


def _schema():
    return json.loads(Path("schema/spares_capture_schema.json").read_text(encoding="utf-8"))


def _profile(**overrides):
    profile = {"part_no_pattern": r"^\d{2}\.\d{5}-\d{4}$"}
    profile.update(overrides)
    return profile


def _record(**overrides):
    record = {
        "part_no": "51.08308-0029",
        "description": "WYE",
        "component": "Auxiliary Engine",
        "qty": "1",
        "unit": "Pcs",
    }
    record.update(overrides)
    return record


def test_high_confidence_record_auto_commits():
    result = validate_record(_record(ocr_confidence=0.96), _profile(), _schema())

    assert result["tier"] == "High"
    assert result["action"] == "auto_commit"


def test_medium_confidence_record_commits_with_flag():
    result = validate_record(_record(ocr_confidence=0.88), _profile(), _schema())

    assert result["tier"] == "Medium"
    assert result["action"] == "commit_flag"
    assert "ocr_confidence_medium_range" in result["reasons"]


def test_confidence_exactly_092_is_medium_not_high():
    result = validate_record(_record(ocr_confidence=0.92), _profile(), _schema())

    assert result["tier"] == "Medium"
    assert result["action"] == "commit_flag"


def test_cell_confidences_are_averaged_when_record_confidence_absent():
    result = validate_record(
        _record(cell_confidences=[0.94, 0.96, 0.95]),
        _profile(),
        _schema(),
    )

    assert result["tier"] == "High"
    assert round(result["confidence"], 3) == 0.95


def test_one_non_critical_missing_field_is_medium():
    result = validate_record(
        _record(ocr_confidence=0.96, unit=""),
        _profile(recommended_fields=["unit"]),
        _schema(),
    )

    assert result["tier"] == "Medium"
    assert result["missing_non_critical"] == ["unit"]


def test_missing_required_field_is_low():
    result = validate_record(_record(ocr_confidence=0.96, description=""), _profile(), _schema())

    assert result["tier"] == "Low"
    assert result["action"] == "hold_review"
    assert result["missing_required"] == ["description"]


def test_part_no_pattern_failure_is_low():
    result = validate_record(_record(ocr_confidence=0.96, part_no="bad"), _profile(), _schema())

    assert result["tier"] == "Low"
    assert "part_no_pattern_mismatch" in result["reasons"]


def test_fallback_pattern_is_three_or_more_characters():
    valid = validate_record(
        _record(part_no="ABC", ocr_confidence=0.96),
        {},
        _schema(),
    )
    invalid = validate_record(
        _record(part_no="AB", ocr_confidence=0.96),
        {},
        _schema(),
    )

    assert valid["tier"] == "High"
    assert invalid["tier"] == "Low"
    assert "part_no_pattern_mismatch" in invalid["reasons"]


def test_low_confidence_record_is_low():
    result = validate_record(_record(ocr_confidence=0.70), _profile(), _schema())

    assert result["tier"] == "Low"
    assert "ocr_confidence_below_0.75" in result["reasons"]
