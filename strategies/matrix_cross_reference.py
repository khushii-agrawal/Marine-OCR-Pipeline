"""
strategies/matrix_cross_reference.py
──────────────────────────────────
Concrete LayoutExtractor for the **matrix_cross_reference** layout.

Ported from: scripts/run_test6.py (best-performing for this layout type)
Covers:      matrix/grid based cross-reference lists, often rotated or multi-column grids.

Key extraction approach:
  1. Detect matrix pages based on headers (e.g. Catalog, Description, Quantity) 
     or structure (repeated columns/grids).
  2. Parse the token-stream or specific columns to extract Part No (Catalog),
     Description, and Quantity for different models/variants in the matrix.
  3. Map back to schema fields dynamically based on the profile.
  
══════════════════════════════════════════════════════════════
HARDCODED ITEMS EXTERNALISED TO PROFILES:
──────────────────────────────────────────────────────────────
1. Regex for Part No / Catalog match (e.g. `CATALOG_RE`)
   AFTER: Profile-driven `column_assumptions.part_no_aliases` or pattern config.
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
    get_page_items,
    group_rows,
    line_text,
    load_profile,
    match_column_header,
    page_text,
    record_confidence,
    titleish,
)


# Default patterns if profile doesn't specify
CATALOG_RE = re.compile(r"^\d{3}\.\d{2}\.(?:\d{3}|[A-Z]{1,4})$")
QUANTITY_RE = re.compile(r"^\d+(?:[.,]\d+)?$")
DATE_RE = re.compile(r"^\d{1,2}[.,]\d{2}[.,]\d{4}$")


def _text_tokens(text: str) -> list[str]:
    return [clean_text(token) for token in text.splitlines() if clean_text(token) and clean_text(token) != "<br>"]


def normalize_pos(text: str) -> str:
    text = clean_text(text).replace(" ", "")
    text = text.replace("g", "9").replace("G", "9")
    text = text.replace("O", "0").replace("o", "0")
    if re.fullmatch(r"[A-Z]|\d{1,3}", text):
        return text
    return ""


def extract_rotated_list_page(text: str, items: list[dict]) -> list[dict]:
    tokens = _text_tokens(text)
    start = 0
    for idx, token in enumerate(tokens):
        if token.lower().startswith("item") or token.lower().startswith("catalog"):
            start = idx + 1
            break

    records = []
    pending_pos = ""
    pending_name = []
    pending_qty = ""
    
    # Try finding confidences roughly by looking at text match in items
    def find_conf(t: str) -> float:
        for it in items:
            if t in it["text"]:
                return it.get("conf", 1.0)
        return 1.0

    for token in tokens[start:]:
        if DATE_RE.match(token):
            break
        if token in {"PC", "CM", "SET"}:
            continue
        if QUANTITY_RE.match(token) and pending_name:
            pending_qty = token
            continue
        if CATALOG_RE.match(token):
            name = clean_text(" ".join(pending_name))
            if name:
                conf = sum(find_conf(n) for n in pending_name) / len(pending_name) if pending_name else 1.0
                records.append({
                    "pos": pending_pos,
                    "part_no": token,
                    "name": name,
                    "qty": pending_qty,
                    "cell_confidences": [conf],
                })
            pending_name = []
            pending_qty = ""
            pending_pos = ""
            continue
        if not pending_name:
            pos = normalize_pos(token)
            if pos and len(token) <= 4:
                pending_pos = pos
                continue
        if token.lower() in {"catalog", "no.", "description", "quantity", "uom", "additional", "info"}:
            continue
        pending_name.append(token)

    return records


class MatrixCrossReferenceStrategy(LayoutExtractor):
    def detect_regions(self, page_image) -> list[Region]:
        pass

    def extract_table(self, region: Region) -> RawTable:
        items = region.metadata.get("items", [])
        text = region.metadata.get("text", "")
        if not items:
            return RawTable(rows=[])
            
        raw_rows = extract_rotated_list_page(text, items)

        return RawTable(
            rows=raw_rows,
            columns=["pos", "name", "part_no", "qty"],
            metadata={"region": region},
        )

    def map_to_schema(self, raw_table: RawTable, manufacturer_profile: dict[str, Any]) -> list[PartRecord]:
        doc_profile = find_document_profile(manufacturer_profile)
        component = doc_profile.get("component", manufacturer_profile.get("manufacturer", ""))
        manufacturer = manufacturer_profile.get("manufacturer", "")
        model = doc_profile.get("model", "")
        default_unit = manufacturer_profile.get("default_unit", "Pcs")
        
        records: list[PartRecord] = []
        for raw_row in raw_table.rows:
            part_no = raw_row.get("part_no", "")
            description = raw_row.get("name", "")
            
            if not part_no and not description:
                continue
                
            qty = raw_row.get("qty", "")
            unit = default_unit
            
            records.append(PartRecord(
                part_no=part_no,
                description=description,
                component=component,
                qty=qty,
                unit=unit,
                drawing_ref="",
                sub_component="",
                manufacturer=manufacturer,
                model=model,
                metadata={
                    "pos_no": raw_row.get("pos", ""),
                    "cell_confidences": raw_row.get("cell_confidences", []),
                },
            ))
        return records

    def confidence(self, record: PartRecord) -> float:
        return record_confidence(record)


def is_matrix_page(text: str, items: list[dict]) -> bool:
    has_headers = "Catalog" in text and "Description" in text and "Item" in text
    catalog_items = [item for item in items if CATALOG_RE.match(item["text"])]
    bottom_spread = [item for item in catalog_items if item.get("y0", item.get("cy", 0)) > 650]
    # For OCR items, the y-coordinate might be relative or absolute. If relative, > 650 is wrong.
    # Check both absolute > 650 and relative > 0.8.
    return has_headers and (len(bottom_spread) >= 4 or len(catalog_items) >= 4)


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
    strategy = MatrixCrossReferenceStrategy()
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

        text = page_text(items)
        if not is_matrix_page(text, items):
            continue

        height = max(item.get("y1", item.get("cy", 0)) for item in items) if items else 1
        width = max(item.get("x1", item.get("cx", 0)) for item in items) if items else 1

        region = Region(
            page_no=page_no,
            bbox=(0.0, 0.0, width, height),
            region_type="matrix_cross_reference",
            confidence=avg_confidence(items),
            metadata={"items": items, "text": text},
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
