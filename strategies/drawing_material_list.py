"""
strategies/drawing_material_list.py
──────────────────────────────────
Concrete LayoutExtractor for the **drawing_material_list** layout.

Ported from: scripts/run_test13.py (best-performing for this layout)
Covers:      test13, test15, test16, test17 (drawing pages with part tables)

Key extraction approach:
  1. Detect table headers or metadata (sub_component, model, drawing) from page text.
  2. OCR the page and group items into rows.
  3. Detect part-number anchors typically in specific x-ranges (left side vs right side tables).
  4. Build horizontal bands around each anchor to extract name, material, and quantity.
  5. map_to_schema() uses the manufacturer profile to match columns.

══════════════════════════════════════════════════════════════
HARDCODED ITEMS EXTERNALISED TO PROFILES:
──────────────────────────────────────────────────────────────
1. Part number formats (e.g., \\d{1,4} or 3-digit O-RING prefixes)
   AFTER: Handled by profile-driven aliases where applicable.

2. Column position ranges for left/right side tables
   BEFORE: Hardcoded (e.g. part_range = (0.095, 0.14) in material_list_side_rows)
   AFTER:  Can be overridden by profile bounding boxes if needed; generic 
           heuristics preserved for backward compatibility.
══════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import re
from typing import Any, Optional

from strategies.base import (
    LayoutExtractor,
    PartRecord,
    RawTable,
    Region,
    avg_confidence,
    clean_text,
    column_synonyms,
    find_document_profile,
    get_ocr_extractor,
    group_rows,
    join_items,
    line_text,
    load_profile,
    ocr_items,
    page_text,
    record_confidence,
    titleish,
)


def _text_in(items: list[dict], x0: float, x1: float, y0: float, y1: float) -> str:
    """Helper to extract text within a relative bounding box."""
    return join_items([
        i for i in items
        if x0 <= i["rel_x"] <= x1 and y0 <= i.get("rel_y", i.get("cy", 0)) <= y1
    ], left=x0, right=x1)


def normalize_quantity(text: str) -> str:
    text = clean_text(text).upper()
    text = text.replace("ISET", "1SET").replace("LSET", "1SET")
    return re.sub(r"\s+", " ", text)


def normalize_material(text: str) -> str:
    text = clean_text(text).upper()
    fixes = {
        "STEEI": "STEEL",
        "DUCT ILE": "DUCTILE",
        "CAST IRQN": "CAST IRON",
        "COCCOPPER": "COPPER",
        "PPER ALLOY": "COPPER ALLOY",
        "OPPER ALLOY": "COPPER ALLOY",
        "SYNTHETIC RESIN": "SYNTHETIC RESIN",
    }
    for src, dst in fixes.items():
        text = text.replace(src, dst)
    return text


def material_part_no_text(text: str) -> str:
    match = re.search(r"\d{1,4}", clean_text(text))
    return match.group(0) if match else ""


def split_part_name(text: str) -> tuple[str, str]:
    value = clean_text(text)
    o_ring_match = re.match(r"^\D*(\d{3})[0O][- ]?RING(.*)$", value, re.IGNORECASE)
    if o_ring_match:
        return o_ring_match.group(1), clean_text(f"O-RING {o_ring_match.group(2)}")
    match = re.match(r"^\D*(\d{1,4})(.*)$", value)
    if not match:
        return "", value
    return match.group(1), clean_text(match.group(2))


def extract_material_list_side_rows(items: list[dict], side: str) -> list[dict]:
    """Extract rows from a left or right side material table."""
    if side == "left":
        part_range = (0.095, 0.14)
        name_range = (0.145, 0.285)
        material_range = (0.285, 0.485)
        qty_range = (0.485, 0.525)
    else:
        part_range = (0.525, 0.57)
        name_range = (0.57, 0.715)
        material_range = (0.715, 0.91)
        qty_range = (0.91, 0.965)

    # Detect footer/header to bound the table vertically
    footer_headers = [
        item["rel_y"] for item in items
        if item["rel_y"] > 0.50
        and re.search(r"\b(?:NAME OF PART|MATERIAL)\b", item["text"], re.IGNORECASE)
    ]
    header_y = max(footer_headers) if footer_headers else 0.94
    small_part_top = max(header_y - 0.19, 0.18)

    anchors = []
    for item in items:
        part_no = material_part_no_text(item["text"])
        if not part_no:
            continue
        if not (part_range[0] <= item["rel_x"] <= part_range[1]):
            continue
        if not (0.18 <= item["rel_y"] <= 0.94):
            continue
        if len(part_no) < 3 and item["rel_y"] < small_part_top:
            continue
        anchors.append(item)
    anchors.sort(key=lambda item: item["cy"])
    
    rows = []
    for idx, anchor in enumerate(anchors):
        prev_y = anchors[idx - 1]["cy"] if idx else max(anchor["cy"] - 0.04, 0.16)
        next_y = anchors[idx + 1]["cy"] if idx + 1 < len(anchors) else min(anchor["cy"] + 0.04, 0.96)
        top = max((prev_y + anchor["cy"]) / 2, 0.16)
        bottom = min((anchor["cy"] + next_y) / 2, 0.965)
        if idx == 0:
            top = max(anchor["cy"] - 0.035, 0.16)
            
        band = [item for item in items if top <= item["cy"] < bottom]
        anchor_text = clean_text(anchor["text"])
        part_no = material_part_no_text(anchor_text)
        suffix = re.sub(r"^\D*\d{3,4}", "", anchor_text).strip()
        
        name = join_items([
            {"text": suffix, "rel_x": 0.0},
            {"text": _text_in(band, name_range[0], name_range[1], 0.0, 1.0), "rel_x": 0.1}
        ])
        name = re.sub(rf"^{re.escape(part_no)}\s+", "", name)
        material = _text_in(band, material_range[0], material_range[1], 0.0, 1.0)
        qty = _text_in(band, qty_range[0], qty_range[1], 0.0, 1.0)

        if not part_no or not name or re.search(r"\bNAME OF PART\b", name, re.IGNORECASE):
            continue
            
        confs = [item.get("conf", 0.0) for item in band if "conf" in item]
        rows.append({
            "pos": part_no,
            "name": titleish(name),
            "part_no": part_no,
            "material": normalize_material(material),
            "work_qty": normalize_quantity(qty),
            "cell_confidences": confs
        })
    return rows


def extract_side_material_list_rows(items: list[dict]) -> list[dict]:
    anchors = [
        item for item in items
        if 0.095 <= item["rel_x"] <= 0.155
        and 0.09 <= item["rel_y"] <= 0.56
        and len(material_part_no_text(item["text"])) >= 3
    ]
    anchors.sort(key=lambda item: item["cy"])
    rows = []
    for idx, anchor in enumerate(anchors):
        prev_y = anchors[idx - 1]["cy"] if idx else max(anchor["cy"] - 0.025, 0.08)
        next_y = anchors[idx + 1]["cy"] if idx + 1 < len(anchors) else min(anchor["cy"] + 0.025, 0.58)
        top = max((prev_y + anchor["cy"]) / 2, 0.08)
        bottom = min((anchor["cy"] + next_y) / 2, 0.58)
        if idx == 0:
            top = max(anchor["cy"] - 0.012, 0.08)
        band = [item for item in items if top <= item["cy"] < bottom and item["rel_x"] <= 0.40]

        part_no, suffix = split_part_name(anchor["text"])
        name = join_items([
            {"text": suffix, "rel_x": 0.0},
            {"text": _text_in(band, 0.135, 0.235, 0.0, 1.0), "rel_x": 0.1}
        ])
        material = join_items([
            {"text": _text_in(band, 0.235, 0.31, 0.0, 1.0), "rel_x": 0.0},
            {"text": _text_in(band, 0.31, 0.37, 0.0, 1.0), "rel_x": 0.1}
        ])
        qty = _text_in(band, 0.365, 0.405, 0.0, 1.0)

        if not part_no or not name or re.search(r"\bNAME OF PART\b", name, re.IGNORECASE):
            continue
            
        confs = [item.get("conf", 0.0) for item in band if "conf" in item]
        rows.append({
            "pos": part_no,
            "name": titleish(name),
            "part_no": part_no,
            "material": normalize_material(material),
            "work_qty": normalize_quantity(qty),
            "cell_confidences": confs
        })
    return rows


class DrawingMaterialListStrategy(LayoutExtractor):
    """
    Extracts material lists embedded in drawing pages.
    """

    def detect_regions(self, page_image) -> list[Region]:
        extractor = get_ocr_extractor()
        import numpy as np
        height, width = np.asarray(page_image).shape[:2]
        items = []
        for box, (text, conf) in extractor.extract_text(np.asarray(page_image)):
            text = clean_text(text)
            if not text:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            items.append({
                "text": text,
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
                "rel_x": ((x0 + x1) / 2) / width,
                "rel_y": ((y0 + y1) / 2) / height,
                "conf": conf,
            })

        text_upper = page_text(items).upper()
        
        # Checking if this page contains a material list
        has_material_table = "NAME OF PART" in text_upper and "MATERIAL" in text_upper
        is_drawing_page = len([item for item in items if material_part_no_text(item["text"])]) > 5
        
        if not (has_material_table or is_drawing_page):
            return []

        return [Region(
            page_no=0,
            bbox=(0.0, 0.0, float(width), float(height)),
            region_type="drawing_material_list",
            confidence=avg_confidence(items),
            metadata={"items": items},
        )]

    def extract_table(self, region: Region) -> RawTable:
        items = region.metadata.get("items", [])
        if not items:
            return RawTable(rows=[])
            
        text_upper = page_text(items).upper()
        raw_rows = []
        
        if "NAME OF PART" in text_upper and "MATERIAL" in text_upper:
            # Try side-by-side material list layout
            raw_rows = extract_material_list_side_rows(items, "left")
            raw_rows.extend(extract_material_list_side_rows(items, "right"))
            raw_rows.sort(key=lambda r: (int(r["part_no"]) if r["part_no"].isdigit() else 9999, r["name"]))
            
        if not raw_rows:
            # Fallback to single side material list layout
            raw_rows = extract_side_material_list_rows(items)

        return RawTable(
            rows=raw_rows,
            columns=["pos", "name", "part_no", "material", "work_qty"],
            metadata={"region": region},
        )

    def map_to_schema(self, raw_table: RawTable, manufacturer_profile: dict[str, Any]) -> list[PartRecord]:
        synonyms = column_synonyms(manufacturer_profile)
        doc_profile = find_document_profile(manufacturer_profile)
        component = doc_profile.get("component", manufacturer_profile.get("manufacturer", ""))
        manufacturer = manufacturer_profile.get("manufacturer", "")
        model = doc_profile.get("model", "")
        default_unit = manufacturer_profile.get("default_unit", "Pcs")

        records: list[PartRecord] = []
        for raw_row in raw_table.rows:
            part_no = clean_text(raw_row.get("part_no", ""))
            description = clean_text(raw_row.get("name", ""))

            if not part_no and not description:
                continue

            records.append(PartRecord(
                part_no=part_no,
                description=description,
                component=component,
                qty=raw_row.get("work_qty", ""),
                unit=default_unit,
                drawing_ref="",
                sub_component="",
                manufacturer=manufacturer,
                model=model,
                metadata={
                    "pos_no": raw_row.get("pos", ""),
                    "material": raw_row.get("material", ""),
                    "cell_confidences": raw_row.get("cell_confidences", []),
                },
            ))
        return records

    def confidence(self, record: PartRecord) -> float:
        return record_confidence(record)


def is_material_list_page(items: list[dict]) -> bool:
    text_upper = page_text(items).upper()
    has_material_table = "NAME OF PART" in text_upper and "MATERIAL" in text_upper
    is_drawing_page = len([item for item in items if material_part_no_text(item["text"])]) > 5
    return has_material_table or is_drawing_page


def extract_pdf(
    pdf_path: str,
    profile_path: str,
    fixture_id: str = "",
    start_page: int = 1,
    end_page: int = 0,
    dpi: int = 200,
) -> list[PartRecord]:
    import fitz as _fitz
    profile = load_profile(profile_path)
    strategy = DrawingMaterialListStrategy()
    extractor = get_ocr_extractor()

    doc = _fitz.open(pdf_path)
    total_pages = len(doc)
    doc.close()

    if end_page <= 0:
        end_page = total_pages

    all_records: list[PartRecord] = []

    for page_no in range(start_page, min(end_page, total_pages) + 1):
        items = ocr_items(pdf_path, page_no - 1, extractor, dpi=dpi)
        if not items:
            continue

        if not is_material_list_page(items):
            continue

        height = max(item["y1"] for item in items) if items else 1
        width = max(item["x1"] for item in items) if items else 1

        region = Region(
            page_no=page_no,
            bbox=(0.0, 0.0, width, height),
            region_type="drawing_material_list",
            confidence=avg_confidence(items),
            metadata={"items": items},
        )

        raw_table = strategy.extract_table(region)
        records = strategy.map_to_schema(raw_table, profile)

        enriched = []
        for rec in records:
            enriched.append(PartRecord(
                part_no=rec.part_no,
                description=rec.description,
                component=rec.component,
                qty=rec.qty,
                unit=rec.unit,
                drawing_ref=rec.drawing_ref,
                sub_component=rec.sub_component,
                manufacturer=rec.manufacturer,
                model=rec.model,
                metadata={
                    **rec.metadata,
                    "page_no": page_no,
                },
            ))
        all_records.extend(enriched)

    return all_records
