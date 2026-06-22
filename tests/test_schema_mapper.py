import json
from pathlib import Path

from core.schema_mapper import map_record


def _schema():
    return json.loads(Path("schema/spares_capture_schema.json").read_text(encoding="utf-8"))


def test_exact_column_match_maps_to_canonical_field():
    profile = {
        "manufacturer": "MAN B&W",
        "default_unit": "Pcs",
        "column_synonyms": {
            "part_no": ["PART NO"],
            "description": ["DESCRIPTION"],
            "qty": ["QTY"],
        },
        "document_profiles": [{"component": "Auxiliary Engine", "model": "D2842LE"}],
        "part_no_pattern": r"^\d{2}\.\d{5}-\d{4}$",
    }

    result = map_record(
        {"PART NO": "51.08308-0029", "DESCRIPTION": "WYE", "QTY": "1"},
        profile,
        _schema(),
    )

    assert result["record"]["part_no"] == "51.08308-0029"
    assert result["record"]["description"] == "WYE"
    assert result["record"]["component"] == "Auxiliary Engine"
    assert result["resolution_log"]["part_no"]["method"] == "exact"


def test_fuzzy_column_match_maps_header_ocr_error():
    profile = {
        "column_synonyms": {"part_no": ["PART NUMBER"], "description": ["DESCRIPTION"]},
        "document_profiles": [{"component": "Pump"}],
        "part_no_pattern": r"^A-\d{3}$",
    }

    result = map_record(
        {"PART NUM8ER": "A-123", "DESCRIPTION": "Impeller"},
        profile,
        _schema(),
    )

    assert result["record"]["part_no"] == "A-123"
    assert result["resolution_log"]["part_no"]["method"] == "fuzzy"


def test_column_assumptions_profile_format_is_supported():
    profile = {
        "column_assumptions": {
            "part_no_aliases": ["ORDER NO"],
            "description_aliases": ["DESIGNATION"],
            "qty_aliases": ["QUANTITY"],
        },
        "document_profiles": [{"component": "Main Engine"}],
        "part_no_pattern": r"^\d{3}\.\d{2}\.\d{3}$",
    }

    result = map_record(
        {"ORDER NO": "011.04.001", "DESIGNATION": "Oil pan", "QUANTITY": "1"},
        profile,
        _schema(),
    )

    assert result["record"]["part_no"] == "011.04.001"
    assert result["record"]["description"] == "Oil pan"
    assert result["resolution_log"]["qty"]["method"] == "exact"


def test_profile_defaults_fill_metadata_fields():
    profile = {
        "manufacturer": "BUKH",
        "default_unit": "Pcs",
        "column_synonyms": {"part_no": ["PART NO"], "description": ["DESCRIPTION"]},
        "document_profiles": [{"component": "Life Boat Spares", "model": "DV 36/48"}],
        "part_no_pattern": r"^\d{3}[A-Z]\d{4}$",
    }

    result = map_record(
        {"PART NO": "033D0201", "DESCRIPTION": "Gasket"},
        profile,
        _schema(),
    )

    assert result["record"]["manufacturer"] == "BUKH"
    assert result["record"]["component"] == "Life Boat Spares"
    assert result["record"]["unit"] == "Pcs"
    assert result["resolution_log"]["model"]["method"] == "profile_default"


def test_part_no_ocr_substitution_repairs_only_after_pattern_failure():
    profile = {
        "column_synonyms": {"part_no": ["PART NO"], "description": ["DESCRIPTION"]},
        "document_profiles": [{"component": "Auxiliary Engine"}],
        "part_no_pattern": r"^\d{2}\.\d{5}-\d{4}$",
        "known_ocr_substitutions": {"O": "0", "I": "1"},
    }

    result = map_record(
        {"PART NO": "51.O8308-0029", "DESCRIPTION": "WYE"},
        profile,
        _schema(),
    )

    assert result["record"]["part_no"] == "51.08308-0029"
    assert result["resolution_log"]["part_no"]["ocr_substitution"]["method"] == "conditional"


def test_valid_part_no_does_not_receive_ocr_substitution():
    profile = {
        "column_synonyms": {"part_no": ["PART NO"], "description": ["DESCRIPTION"]},
        "document_profiles": [{"component": "Auxiliary Engine"}],
        "part_no_pattern": r"^\d{2}\.\d{5}-\d{4}$",
        "known_ocr_substitutions": {"O": "0", "I": "1"},
    }

    result = map_record(
        {"PART NO": "51.08308-0029", "DESCRIPTION": "O RING"},
        profile,
        _schema(),
    )

    assert result["record"]["part_no"] == "51.08308-0029"
    assert result["record"]["description"] == "O RING"
    assert result["resolution_log"]["part_no"]["ocr_substitution"]["method"] == "not_applied"


def test_description_does_not_receive_ocr_substitutions():
    profile = {
        "column_synonyms": {"part_no": ["PART NO"], "description": ["DESCRIPTION"]},
        "document_profiles": [{"component": "Auxiliary Engine"}],
        "part_no_pattern": r"^\d{2}\.\d{5}-\d{4}$",
        "known_ocr_substitutions": {"O": "0", "I": "1"},
    }

    result = map_record(
        {"PART NO": "51.08308-0029", "DESCRIPTION": "OIL COOLER"},
        profile,
        _schema(),
    )

    assert result["record"]["description"] == "OIL COOLER"
