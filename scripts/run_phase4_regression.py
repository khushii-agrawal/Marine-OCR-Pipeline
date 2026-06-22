"""Run Phase 4 mapper/validator over strategy fixture outputs."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.schema_mapper import map_record
from core.validator import validate_record


STRATEGY_MODULES = {
    "standard_parts_table": "strategies.standard_parts_table",
    "drawing_material_list": "strategies.drawing_material_list",
    "accessory_kit_equipment_list": "strategies.accessory_kit_equipment_list",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_by_test_number(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for fixture in manifest.get("fixtures", []):
        fixture_id = fixture.get("fixture_id", "")
        test_id = fixture_id.split("_", 1)[0]
        if test_id:
            mapping[f"{test_id}.pdf"] = fixture
    return mapping


def _record_to_raw_fields(record: Any) -> dict[str, Any]:
    values = asdict(record) if is_dataclass(record) else dict(record)
    metadata = values.pop("metadata", {}) or {}
    if "cell_confidences" in metadata:
        values["cell_confidences"] = metadata["cell_confidences"]
    return values


def run_regression(max_pages: int | None, dpi: int) -> dict[str, Any]:
    schema = _load_json(ROOT / "schema" / "spares_capture_schema.json")
    manifest = _load_json(ROOT / "tests" / "fixtures" / "fixture_manifest.json")
    fixture_lookup = _manifest_by_test_number(manifest)

    totals: dict[str, Counter] = defaultdict(Counter)
    files: list[dict[str, Any]] = []

    for pdf_path in sorted((ROOT / "tests" / "fixtures").rglob("*.pdf")):
        layout = pdf_path.parent.name
        module_name = STRATEGY_MODULES.get(layout)
        fixture = fixture_lookup.get(pdf_path.name)
        if module_name is None or fixture is None:
            files.append({"pdf": str(pdf_path.relative_to(ROOT)), "status": "skipped", "reason": "no strategy/profile mapping"})
            continue

        profile_path = ROOT / fixture["profile"]
        profile = _load_json(profile_path)
        manufacturer = profile.get("manufacturer") or profile.get("profile_id") or profile_path.stem
        fixture_id = fixture.get("fixture_id", "")

        try:
            module = importlib.import_module(module_name)
            records = module.extract_pdf(
                pdf_path=str(pdf_path),
                profile_path=str(profile_path),
                fixture_id=fixture_id,
                start_page=1,
                end_page=max_pages or 0,
                dpi=dpi,
            )
        except Exception as exc:
            files.append({
                "pdf": str(pdf_path.relative_to(ROOT)),
                "manufacturer": manufacturer,
                "status": "failed",
                "error": str(exc),
            })
            continue

        tier_counts = Counter()
        for record in records:
            mapped = map_record(_record_to_raw_fields(record), profile, schema)
            validation = validate_record(mapped["record"], profile, schema)
            tier_counts[validation["tier"]] += 1

        totals[manufacturer].update(tier_counts)
        files.append({
            "pdf": str(pdf_path.relative_to(ROOT)),
            "manufacturer": manufacturer,
            "layout": layout,
            "status": "ok",
            "records": sum(tier_counts.values()),
            "tiers": dict(tier_counts),
        })

    return {"files": files, "manufacturers": {k: dict(v) for k, v in totals.items()}}


def _format_percentages(counts: dict[str, int]) -> str:
    total = sum(counts.values())
    if total == 0:
        return "High 0.0%, Medium 0.0%, Low 0.0% (0 records)"
    return ", ".join(
        f"{tier} {(counts.get(tier, 0) / total) * 100:.1f}%"
        for tier in ["High", "Medium", "Low"]
    ) + f" ({total} records)"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=None, help="Optional page cap per PDF for smoke runs.")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--json-output", type=Path, default=None, help="Optional path for detailed JSON results.")
    args = parser.parse_args()

    results = run_regression(max_pages=args.max_pages, dpi=args.dpi)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("Per-manufacturer Phase 4 gate results:")
    for manufacturer, counts in sorted(results["manufacturers"].items()):
        print(f"- {manufacturer}: {_format_percentages(counts)}")

    failures = [item for item in results["files"] if item["status"] == "failed"]
    if failures:
        print("\nFailures:")
        for item in failures:
            print(f"- {item['pdf']}: {item['error']}")


if __name__ == "__main__":
    main()
