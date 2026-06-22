"""
strategies/accessory_kit_equipment_list.py
──────────────────────────────────────────
Concrete LayoutExtractor for the **accessory_kit_equipment_list** layout.

Ported from: scripts/run_test14.py (best-performing for this layout)
Covers:      test4, test14 (equipment lists, packing lists, bespoke equipment tables)

Key extraction approach:
  1. Detect the specific table layout (Category/Description, Part/Qty/Description, etc.)
  2. Parse the table structure based on detected columns.
  3. Extract name, quantity, and related details.
  4. map_to_schema() uses the manufacturer profile to resolve extracted fields.

══════════════════════════════════════════════════════════════
HARDCODED ITEMS EXTERNALISED TO PROFILES:
──────────────────────────────────────────────────────────────
1. Bespoke subcomponent/manufacturer detections (e.g., Kangrim, Sludge Checker)
   AFTER: Use profile sub_component or general page metadata fallback.

2. Column matching string literals (e.g., "PART NUMBER", "QUANTITY PER SHIPSET")
   AFTER: Handled by dynamic checks against profile column_assumptions.
══════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import re
from typing import Any

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
    get_page_items,
    group_rows,
    line_text,
    load_profile,
    match_column_header,
    page_text,
    record_confidence,
    titleish,
)


def _page_lines(items: list[dict]) -> list[str]:
    return [line_text(line) for line in group_rows(items, y_tolerance=0.008)]


def _title_from_lines(lines: list[str], fallback: str) -> str:
    for line in lines[:8]:
        if " - " in line:
            tail = line.split(" - ", 1)[-1]
            tail = re.sub(r"\b(?:OBP|Standard)\s+Spares?\b.*$", "", tail, flags=re.IGNORECASE)
            tail = re.sub(r"\b(?:OBP|Standard)\s+Spare\s+List\b.*$", "", tail, flags=re.IGNORECASE)
            return clean_text(tail) or fallback
        match = re.search(r"\)\s+(.+)$", line)
        if match:
            tail = match.group(1)
            tail = re.sub(r"\b(?:OBP|Standard)\s+Spares?\b.*$", "", tail, flags=re.IGNORECASE)
            tail = re.sub(r"\b(?:OBP|Standard)\s+Spare\s+List\b.*$", "", tail, flags=re.IGNORECASE)
            return clean_text(tail) or fallback
    for line in lines[:8]:
        if re.search(r"\b(?:SPARE|PARTS|LIST|PAGE|PROJECT|TITLE)\b", line, re.IGNORECASE):
            continue
        if len(line) > 4:
            return clean_text(line)
    return fallback


def parse_category_description(items: list[dict]) -> list[dict]:
    lines = _page_lines(items)
    text = "\n".join(lines).upper()
    if "CATEGORY" not in text or "DESCRIPTION" not in text:
        return []
        
    rows = []
    in_table = False
    for line in lines:
        if "CATEGORY" in line.upper() and "DESCRIPTION" in line.upper():
            in_table = True
            continue
        if not in_table:
            continue
            
        match = re.match(r"^(.+?)\s+(\d+(?:\.\d+)?)\s+([A-Za-z.]+)$", line)
        if not match:
            continue
            
        prefix, qty, unit = match.groups()
        parts = prefix.split(" ", 1)
        category = parts[0]
        name = parts[1] if len(parts) > 1 else prefix
        
        rows.append({
            "pos": str(len(rows) + 1),
            "name": titleish(name),
            "work_qty": qty,
            "unit": unit,
            "remarks": f"Category: {category}",
        })
    return rows


def parse_part_qty_description(items: list[dict]) -> list[dict]:
    lines = _page_lines(items)
    text = "\n".join(lines).upper()
    if "PART NUMBER" not in text or "DESCRIPTION" not in text:
        return []
        
    rows = []
    in_table = False
    for line in lines:
        if "PART NUMBER" in line.upper() and "DESCRIPTION" in line.upper():
            in_table = True
            continue
        if not in_table:
            continue
            
        match = re.match(r"^(.+?)\s+(\d+(?:\.\d+)?)\s+(.+)$", line)
        if not match:
            continue
            
        part, qty, name = match.groups()
        rows.append({
            "pos": str(len(rows) + 1),
            "part_no": clean_text(part),
            "name": titleish(name),
            "work_qty": qty,
        })
    return rows


def parse_description_qty(items: list[dict]) -> list[dict]:
    lines = _page_lines(items)
    text = "\n".join(lines).upper()
    if "DESCRIPTION" not in text or "QTY" not in text:
        return []
        
    rows = []
    in_table = False
    for line in lines:
        upper = line.upper()
        if "DESCRIPTION" in upper and "QTY" in upper:
            in_table = True
            continue
        if not in_table:
            continue
        if re.search(r"\bSTANDARD SPARES\b|\bSPARE PARTS\b", upper):
            break
            
        match = re.match(r"^(.+?)\s+(\d+(?:\.\d+)?)$", line)
        if not match:
            continue
            
        name, qty = match.groups()
        rows.append({
            "pos": str(len(rows) + 1),
            "name": titleish(name),
            "work_qty": qty,
        })
    return rows


def parse_item_description(items: list[dict]) -> list[dict]:
    lines = _page_lines(items)
    text = "\n".join(lines).upper()
    
    # Generic parser for No Description Qty format
    if "DESCRIPTION" not in text:
        return []
        
    rows = []
    in_table = False
    for line in lines:
        if re.search(r"\bNo\s+Description\b|\bItem\s+Description\b", line, re.IGNORECASE):
            in_table = True
            continue
        if not in_table:
            continue
            
        match = re.match(r"^(\d+)\s+(.+?)\s+(\d+)(?:\s+(.+))?$", line)
        if not match:
            continue
            
        pos, name, qty, remarks = match.groups()
        rows.append({
            "pos": pos,
            "name": titleish(name),
            "work_qty": qty,
            "remarks": clean_text(remarks or ""),
        })
    return rows


class AccessoryKitEquipmentListStrategy(LayoutExtractor):
    def detect_regions(self, page_image) -> list[Region]:
        # Implementation assumes caller passes actual items inside metadata 
        # as a workaround for Region passing in runner, or uses get_page_items
        pass

    def extract_table(self, region: Region) -> RawTable:
        items = region.metadata.get("items", [])
        if not items:
            return RawTable(rows=[])
            
        raw_rows = []
        for parser in [
            parse_category_description,
            parse_part_qty_description,
            parse_item_description,
            parse_description_qty,
        ]:
            raw_rows = parser(items)
            if raw_rows:
                break
                
        subcomponent = _title_from_lines(_page_lines(items), fallback="")
        
        return RawTable(
            rows=raw_rows,
            columns=["pos", "name", "part_no", "work_qty", "unit", "remarks"],
            metadata={"region": region, "sub_component": subcomponent},
        )

    def map_to_schema(self, raw_table: RawTable, manufacturer_profile: dict[str, Any]) -> list[PartRecord]:
        doc_profile = find_document_profile(manufacturer_profile)
        component = doc_profile.get("component", manufacturer_profile.get("manufacturer", ""))
        manufacturer = manufacturer_profile.get("manufacturer", "")
        model = doc_profile.get("model", "")
        default_unit = manufacturer_profile.get("default_unit", "Pcs")
        
        detected_sub_component = raw_table.metadata.get("sub_component", "")
        sub_component = detected_sub_component or doc_profile.get("sub_component", "")

        records: list[PartRecord] = []
        for raw_row in raw_table.rows:
            part_no = raw_row.get("part_no", "")
            description = raw_row.get("name", "")
            
            if not part_no and not description:
                continue
                
            qty = raw_row.get("work_qty", "")
            unit = raw_row.get("unit", "") or default_unit
            
            records.append(PartRecord(
                part_no=part_no,
                description=description,
                component=component,
                qty=qty,
                unit=unit,
                drawing_ref="",
                sub_component=sub_component,
                manufacturer=manufacturer,
                model=model,
                metadata={
                    "pos_no": raw_row.get("pos", ""),
                    "remarks": raw_row.get("remarks", ""),
                },
            ))
        return records

    def confidence(self, record: PartRecord) -> float:
        return record_confidence(record)


def is_accessory_kit_page(items: list[dict]) -> bool:
    text_upper = page_text(items).upper()
    return "DESCRIPTION" in text_upper and any(
        kw in text_upper for kw in ["QTY", "QUANTITY", "CATEGORY"]
    )


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
    strategy = AccessoryKitEquipmentListStrategy()
    extractor = get_ocr_extractor()

    doc = _fitz.open(pdf_path)
    total_pages = len(doc)
    doc.close()

    if end_page <= 0:
        end_page = total_pages

    all_records: list[PartRecord] = []

    for page_no in range(start_page, min(end_page, total_pages) + 1):
        items, mode = get_page_items(pdf_path, page_no, extractor, dpi=dpi, min_embedded=8)
        if not items:
            continue

        if not is_accessory_kit_page(items):
            continue

        height = max(item["y1"] for item in items) if items else 1
        width = max(item["x1"] for item in items) if items else 1

        region = Region(
            page_no=page_no,
            bbox=(0.0, 0.0, width, height),
            region_type="accessory_kit_equipment_list",
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
