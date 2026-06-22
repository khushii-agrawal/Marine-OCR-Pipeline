import argparse
import csv
import json
import re
from pathlib import Path

import fitz


PROJECT_ROOT = Path(__file__).resolve().parents[1]

CLASS_NAMES = [
    "spare_parts_table",
    "drawing_material_list",
    "drawing_only",
    "index_page",
    "repair_kit",
]

LAYOUT_ALIASES = {
    "spare_parts_table": "spare_parts_table",
    "standard_parts_table": "spare_parts_table",
    "standard_spare_parts_table": "spare_parts_table",
    "drawing_material_list": "drawing_material_list",
    "drawing_title_block": "drawing_only",
    "drawing_only": "drawing_only",
    "matrix_cross_reference": "index_page",
    "index_page": "index_page",
    "accessory_kit_equipment_list": "repair_kit",
    "repair_kit": "repair_kit",
}


def clean_token(value):
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return re.sub(r"_+", "_", value).strip("_") or "document"


def normalize_layout(value):
    key = str(value or "").strip().lower()
    if key not in LAYOUT_ALIASES:
        raise ValueError(f"Unsupported layout label: {value!r}")
    return LAYOUT_ALIASES[key]


def manifest_sources(manifest_path):
    manifest_path = Path(manifest_path)
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    for fixture in manifest.get("fixtures", []):
        pdf_path = PROJECT_ROOT / fixture["pdf"]
        if fixture.get("page_labels"):
            for idx, page_label in enumerate(fixture["page_labels"], start=1):
                yield {
                    "fixture_id": f"{fixture['fixture_id']}__label{idx}",
                    "pdf_path": pdf_path,
                    "layout": normalize_layout(page_label["layout"]),
                    "source": "manifest_page_labels",
                    "pages": expand_pages(page_label["pages"]),
                }
            continue
        yield {
            "fixture_id": fixture["fixture_id"],
            "pdf_path": pdf_path,
            "layout": normalize_layout(fixture["primary_layout"]),
            "source": "manifest",
            "pages": None,
        }


def folder_sources(fixtures_dir):
    fixtures_dir = Path(fixtures_dir)
    seen = set()
    for pdf_path in fixtures_dir.rglob("*.pdf"):
        if pdf_path in seen:
            continue
        seen.add(pdf_path)
        rel = pdf_path.relative_to(fixtures_dir)
        if len(rel.parts) < 2:
            continue
        layout = normalize_layout(rel.parts[0])
        yield {
            "fixture_id": clean_token(pdf_path.stem),
            "pdf_path": pdf_path,
            "layout": layout,
            "source": "folder",
            "pages": None,
        }


def collect_sources(fixtures_dir, manifest_path):
    sources = []
    if manifest_path and Path(manifest_path).exists():
        sources.extend(manifest_sources(manifest_path))
    folder_items = list(folder_sources(fixtures_dir))
    if folder_items:
        # Prefer explicit physical fixture folders when present; they are closer
        # to the user's page-level labeling workflow than the coarse manifest.
        return folder_items
    return sources


def render_pdf_pages(source, output_dir, dpi, overwrite=False, max_pages=None):
    output_dir = Path(output_dir).resolve()
    pdf_path = source["pdf_path"]
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    layout_dir = output_dir / source["layout"]
    layout_dir.mkdir(parents=True, exist_ok=True)
    rows = []

    with fitz.open(pdf_path) as doc:
        page_numbers = source.get("pages") or list(range(1, len(doc) + 1))
        if max_pages is not None:
            page_numbers = page_numbers[:max_pages]
        for page_no in page_numbers:
            if page_no < 1 or page_no > len(doc):
                continue
            page_idx = page_no - 1
            filename = f"{clean_token(source['fixture_id'])}__p{page_no:04d}.png"
            out_path = layout_dir / filename
            if overwrite or not out_path.exists():
                page = doc.load_page(page_idx)
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                pix.save(out_path)
            rows.append({
                "image_path": project_relative(out_path),
                "label": source["layout"],
                "fixture_id": source["fixture_id"],
                "source_pdf": project_relative(pdf_path),
                "page_no": page_no,
                "source": source["source"],
            })
    return rows


def expand_pages(value):
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        pages = []
        for item in value:
            pages.extend(expand_pages(item))
        return sorted(set(pages))
    pages = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = [int(piece.strip()) for piece in part.split("-", 1)]
            pages.extend(range(start, end + 1))
        else:
            pages.append(int(part))
    return sorted(set(pages))


def write_labels_csv(rows, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "labels.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["image_path", "label", "fixture_id", "source_pdf", "page_no", "source"])
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def project_relative(path):
    path = Path(path).resolve()
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def main():
    parser = argparse.ArgumentParser(description="Render fixture PDFs into class-labeled training PNGs.")
    parser.add_argument("--fixtures-dir", default=str(PROJECT_ROOT / "tests" / "fixtures"))
    parser.add_argument("--manifest", default=str(PROJECT_ROOT / "tests" / "fixtures" / "fixture_manifest.json"))
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "training_data"))
    parser.add_argument("--dpi", type=int, default=120)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-pages-per-pdf", type=int, default=None)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    output_dir = output_dir.resolve()
    for class_name in CLASS_NAMES:
        (output_dir / class_name).mkdir(parents=True, exist_ok=True)

    sources = collect_sources(args.fixtures_dir, args.manifest)
    if not sources:
        raise SystemExit("No fixture PDFs found. Populate tests/fixtures or provide fixture_manifest.json.")

    all_rows = []
    for source in sources:
        print(f"Rendering {source['fixture_id']} as {source['layout']}: {source['pdf_path']}")
        all_rows.extend(render_pdf_pages(source, output_dir, args.dpi, args.overwrite, args.max_pages_per_pdf))

    csv_path = write_labels_csv(all_rows, output_dir)
    counts = {class_name: 0 for class_name in CLASS_NAMES}
    for row in all_rows:
        counts[row["label"]] += 1
    print(f"Wrote {len(all_rows)} page images")
    print(f"Labels: {csv_path}")
    print("Class counts:", counts)


if __name__ == "__main__":
    main()
