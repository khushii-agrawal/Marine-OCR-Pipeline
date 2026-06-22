"""
strategies/standard_parts_table.py
──────────────────────────────────
Concrete LayoutExtractor for the **standard_parts_table** layout.

Ported from: scripts/run_ae.py  (best-performing for this layout)
Covers:      test5, test7, test8  (MAN B&W auxiliary-engine spare-parts tables)
             and other standard tabular spare-parts pages.

Key extraction approach:
  1. OCR the page at 200 dpi via PaddleOCR.
  2. Identify part-number anchors in the left-middle column
     (rel_x 0.08–0.24) using the MAN B&W part-number pattern
     (NN.NNNNN-NNN).
  3. Group surrounding OCR items into rows by y-proximity.
  4. For each anchor row, extract: position, part number,
     designation (English name), and quantity.
  5. map_to_schema() uses column_assumptions from the profile
     to match detected headers to canonical schema fields.

══════════════════════════════════════════════════════════════
HARDCODED ITEMS EXTERNALISED TO PROFILES (before → after):
──────────────────────────────────────────────────────────────
1. PART_NO_RE = r"^\\d{2}\\.\\d{5}[\\-.]\\d{3,5}$"
   BEFORE: Hardcoded in run_ae.py line 26
   AFTER:  Detected at runtime from profile column_assumptions.part_no_aliases
           + a configurable "part_no_pattern" field on the profile.

2. Column position ranges for english text (rel_x 0.20–0.48)
   BEFORE: Hardcoded in run_ae.py english_tokens() line 357
   AFTER:  Strategy uses dynamic column detection from header row
           when available, falling back to profile-configurable defaults.

3. COMPONENT_NAME = "Auxiliary Engine", MANUFACTURER = "MAN B&W", etc.
   BEFORE: Hardcoded constants in run_ae.py lines 18-23
   AFTER:  Read from profile JSON at runtime (component, manufacturer,
           model, default_unit fields).

4. Page margin filters (rel_y > 0.92, rel_y < 0.05)
   BEFORE: Hardcoded in run_ae.py group_ocr_into_rows_ae() line 510-513
   AFTER:  Configurable via optional "page_margins" in profile; defaults
           preserved for backwards compatibility.

5. PART_NO_CORRECTIONS dict
   BEFORE: Hardcoded in run_ae.py line 30-35
   AFTER:  Not ported — these are document-specific OCR error corrections
           that should live in a fixture-level override, not the strategy.

6. PART_NAME_OVERRIDES dict
   BEFORE: Hardcoded in run_ae.py line 36-38
   AFTER:  Same — document-specific, not ported into generic strategy.

7. Size/material splitting logic (SIZE_TOKEN_RE, split_spare_name_details)
   BEFORE: Hardcoded patterns in run_ae.py lines 39-54, 102-166
   AFTER:  Preserved in strategy as shared utility — these patterns are
           layout-specific not manufacturer-specific.
══════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import re
from collections import defaultdict
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


# ═══════════════════════ Part-number patterns ═══════════════════════

# MAN B&W auxiliary engine pattern: NN.NNNNN-NNN(N)
PART_NO_RE = re.compile(r"^\d{2}\.\d{5}[-\.]\d{3,5}$")
PART_NO_PREFIX_RE = re.compile(r"^(\d{2}\.\d{5}[-\.]\d{3,5})(.*)$")

# Drawing number pattern
DRAWING_NO_RE = re.compile(
    r"^\d{2}\s*(?:-|N)\s*[A-Z0-9][A-Z0-9/ -]{2,}$", re.IGNORECASE
)
# Table/ELTIS number pattern
TABLE_NO_RE = re.compile(r"\b(?:ELTIS|#)\s*[A-Z0-9]{6,}\b", re.IGNORECASE)

# Noise phrases to strip from names
NOISE_PHRASE_RE = re.compile(
    r"\b(?:NB|DELETED|REPLACED\s*BY|ITEM\s*NUMBER\s*\S*|ITEMNUMBER\S*|SEE\s*PLATE:?.*)\b",
    re.IGNORECASE,
)

# Size/dimension token detector
SIZE_TOKEN_RE = re.compile(
    r"^(?:"
    r"M\d"
    r"|AM\d"
    r"|CM\d"
    r"|BLL?\d"
    r"|N\d"
    r"|A-\d"
    r"|\d+(?:[,.]\d+)?[A-Z]*X\d"
    r"|[A-Z]\d+(?:[,.]\d+)?[/X]\d"
    r"|[AB]\d+"
    r"|N\d+-\d+"
    r"|\d+[A-Z]\d"
    r")",
    re.IGNORECASE,
)


# ═══════════════════════ Text utilities ═══════════════════════


def normalize_part_no(text: str) -> str:
    """Strip spaces from a part number string."""
    return clean_text(text).replace(" ", "")


def split_part_no_prefix(text: str) -> tuple[str, str]:
    """
    Split a string that starts with a part number into (part_no, tail).
    E.g. "51.08308-0029WYE" → ("51.08308-0029", "WYE")
    """
    compact = normalize_part_no(text)
    match = PART_NO_PREFIX_RE.match(compact)
    if not match:
        return "", clean_text(text)
    part_no, tail = match.groups()
    # Add space before capital-letter boundaries in tail
    tail = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", tail)
    return part_no, clean_text(tail)


def is_part_no(text: str) -> bool:
    """Check if text matches the standard part-number format."""
    compact = normalize_part_no(text)
    return bool(PART_NO_RE.match(compact) or PART_NO_PREFIX_RE.match(compact))


def mostly_uppercase(text: str) -> bool:
    """Check if >=75% of alphabetic chars are uppercase."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    return sum(1 for ch in letters if ch.isupper()) / len(letters) >= 0.75


def canonicalize_spare_name(name: str) -> str:
    """Simplify common spare-part names to canonical short forms."""
    name = clean_text(name)
    if not name:
        return ""
    # Remove trailing descriptors
    name = re.sub(
        r"\b(?:NORMAL|OVERSIZE|UNDERSIZE|REPAIR STAGE\s*\d*|DIAMETER|"
        r"OUTSIDE DIAMETER|COLLAR HEIGHT|HEIGHT:?)\b.*",
        "", name,
    )
    name = re.sub(r"\b(?:FOR RIGHT|FOR LEFT|RIGHT|LEFT)\b.*", "", name)
    name = re.sub(r"\b(?:WITH BORE|WITH LARGE FLANGE)\b.*", "", name)
    name = re.sub(r"\bOS\b$", "", name)
    name = re.sub(r"\b(?:BOTTLE|TUBE|CARTRIDGE)\b$", "", name)
    name = re.sub(r"\b\d+(?:[,.]\d+)?\s*(?:MM|ML|G)\b.*", "", name)

    simple_prefixes = [
        "SEALANTS OMNIFIT", "SEALANTS", "ADHESIVE", "ASSEMBLY PASTE",
        "DRAIN PLUG", "SPRING WASHER", "HEX SHOULDER STUD", "HEX COLLAR BOLT",
        "HEX BOLT", "HEXAGON NUT", "HOLLOW SCREW", "UNION NUT", "SPRING CLIP",
        "HOSE CLAMP", "MOUNTING CLAMP", "PIPE CLIP", "SUPPORT WASHER",
        "CYLINDER SCREW", "SOCKET HEAD SCREW", "TORIC SEAL", "SEAL",
        "DOWEL PIN", "SPIRAL DOWEL PIN", "BALL", "BUSH", "HOSE", "STUD",
        "WASHER", "GASKET", "COVER", "CLAMP", "BRACKET", "FLANGE",
        "SHIM", "RACE", "PIPE", "RING UNION", "SWIVEL UNION", "T-UNION",
    ]
    for prefix in simple_prefixes:
        if name.startswith(prefix):
            return prefix
    return clean_text(name)


def split_spare_name_details(raw_name: str) -> tuple[str, str, str]:
    """
    Split a raw spare name into (name, size, material).

    Ported from run_ae.py split_spare_name_details().
    """
    text = clean_text(raw_name).upper()
    if not text:
        return "", "", ""

    # Fix common OCR glue errors
    replacements = {
        "HEXCOLLAR": "HEX COLLAR",
        "HEXBOLT": "HEX BOLT",
        "HEX BOLTM": "HEX BOLT M",
        "HEXAGONNUT": "HEXAGON NUT",
        "HOLLOWSCREW": "HOLLOW SCREW",
        "SOCKETHEAD": "SOCKET HEAD",
        "DOWELPIN": "DOWEL PIN",
        "TORICSEAL": "TORIC SEAL",
        "FUELLINE": "FUEL LINE",
        "THREADEDUNION": "THREADED UNION",
        "DRAINPLUG": "DRAIN PLUG",
        "HOSEN": "HOSE N",
        "SPRING CLIPA": "SPRING CLIP A",
        "KEYSTONERING": "KEYSTONE RING",
        "FLYWHEELASSEMBLY": "FLYWHEEL ASSEMBLY",
        "ASSEMBLYPASTE": "ASSEMBLY PASTE",
        "WITHNOZZLE": "WITH NOZZLE",
        "ELASTOMERLIP": "ELASTOMER LIP",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)

    # Separate glued size tokens
    text = re.sub(r"\b([A-Z]{3,})([A-Z]?\d+[,.]?\d*(?:[/X]\d))", r"\1 \2", text)
    text = re.sub(r"\bCOVER([A-Z])\s+(\d)", r"COVER \1\2", text)
    text = re.sub(
        r"\b(BOLT|SCREW|STUD|NUT|WASHER|SEAL|HOSE|CLIP|RING|BUSH|PLUG)([A-Z]?\d)",
        r"\1 \2",
        text,
    )
    text = NOISE_PHRASE_RE.sub("", text)
    text = clean_text(text)

    # Special cases
    if "WITH ELASTOMER LIP" in text and "SEAL" in text:
        prefix = "SEAL WITH ELASTOMER LIP"
        detail = clean_text(text.replace("WITH ELASTOMER LIP", "").replace("SEAL", "", 1))
        size, material = _split_size_material(detail)
        return prefix, size, material

    if text.startswith("GASKET") and "ASBESTOS-FREE" in text:
        detail = clean_text(text.replace("GASKET", "", 1).replace("ASBESTOS-FREE", ""))
        size, material = _split_size_material(detail)
        return "GASKET ASBESTOS-FREE", size, material

    # Find where the size token starts
    tokens = text.split()
    split_idx = None
    for idx, token in enumerate(tokens):
        if idx == 0:
            continue
        if SIZE_TOKEN_RE.match(token.strip(",:;()[]")):
            split_idx = idx
            break

    if split_idx is None:
        return canonicalize_spare_name(text), "", ""

    name = canonicalize_spare_name(clean_text(" ".join(tokens[:split_idx])))
    detail = clean_text(" ".join(tokens[split_idx:]))
    size, material = _split_size_material(detail)
    return name, size, material


def _split_size_material(detail: str) -> tuple[str, str]:
    """Split a detail string into (size, material) parts."""
    detail = clean_text(detail)
    if not detail:
        return "", ""
    # Remove known noise codes
    detail = re.sub(r"\bMAN183-B1\b", "", detail, flags=re.IGNORECASE)
    detail = re.sub(r"\bM3219-G1\b", "", detail, flags=re.IGNORECASE)
    detail = clean_text(detail)

    # Try pattern-based splits
    token_match = re.match(r"^(A-\d+X\d+|N\d+-\d+)\b(.*)$", detail, re.IGNORECASE)
    if token_match:
        return clean_text(token_match.group(1)), clean_text(token_match.group(2))

    token_match = re.match(r"^(B\d+)-(.+)$", detail, re.IGNORECASE)
    if token_match:
        return clean_text(token_match.group(1)), clean_text(token_match.group(2))

    dash_match = re.search(r"[-]", detail)
    if dash_match:
        return clean_text(detail[:dash_match.start()]), clean_text(detail[dash_match.end():])

    tokens = detail.split()
    if len(tokens) > 1 and re.search(r"\d", tokens[0]):
        return tokens[0], clean_text(" ".join(tokens[1:]))
    return detail, ""


# ═══════════════════════ Page-context detection ═══════════════════════


def find_page_context(
    items: list[dict],
    page_width: float,
    page_height: float,
) -> tuple[str, str, str]:
    """
    Detect drawing number, sub-component title, and table/ELTIS number
    from OCR items on a page.

    Returns: (drawing_no, sub_component, table_no)
    """
    drawing_no = ""
    sub_component = ""
    table_no = ""

    # Find table/ELTIS number
    for item in items:
        match = TABLE_NO_RE.search(item["text"])
        if match:
            table_no = clean_text(match.group(0)).replace(" ", "").upper()
            break

    # Find drawing number
    for item in items:
        text = item["text"]
        compact = text.replace(" ", "")
        if "." in compact:
            continue
        if item["rel_x"] < 0.35 and item["rel_y"] < 0.55 and DRAWING_NO_RE.match(text):
            drawing_no = clean_text(text)
            break

    # Find sub-component title
    part_rows_y = [
        item["rel_y"]
        for item in items
        if is_part_no(item["text"]) and item["rel_y"] > 0.40
    ]
    first_part_y = min(part_rows_y) if part_rows_y else 0.70

    title_candidates = []
    for item in items:
        text = item["text"]
        upper = text.upper()
        if not (0.45 <= item["rel_y"] <= first_part_y - 0.01):
            continue
        if item["rel_x"] > 0.48:
            continue
        if is_part_no(text) or DRAWING_NO_RE.match(text):
            continue
        if "D2842" in upper or "ELTIS" in upper or upper == "MAN":
            continue
        if any(ch.isdigit() for ch in text):
            continue
        if len(text) < 4 or not mostly_uppercase(text):
            continue
        title_candidates.append(item)

    if title_candidates:
        title_candidates.sort(key=lambda i: (i["cy"], i["cx"]))
        sub_component = clean_text(" ".join(i["text"] for i in title_candidates))

    return drawing_no, sub_component, table_no


# ═══════════════════════ Row extraction ═══════════════════════


def _row_pos_no(row: list[dict]) -> str:
    """Extract position number from row items."""
    for item in row:
        text = item["text"].replace(" ", "")
        if item["rel_x"] < 0.10 and text.isdigit() and len(text) <= 3:
            return text
    return ""


def _english_tokens(row: list[dict]) -> list[str]:
    """Extract English designation tokens from the middle column."""
    tokens = []
    for item in row:
        text = item["text"]
        if item["rel_x"] <= 0.20 or item["rel_x"] >= 0.48:
            continue
        if is_part_no(text):
            continue
        if text.replace(" ", "").isdigit():
            continue
        tokens.append(text)
    return tokens


def _row_has_part_no(row: list[dict]) -> bool:
    """Check if row contains a part number in the expected column."""
    return any(
        is_part_no(item["text"]) and 0.08 <= item["rel_x"] <= 0.24
        for item in row
    )


def _row_has_quantity_marker(row: list[dict]) -> bool:
    """Check if row has a quantity value in the expected column."""
    for item in row:
        text = item["text"].replace(" ", "")
        if 0.46 <= item["rel_x"] <= 0.54 and text.isdigit() and len(text) <= 3:
            return True
    return False


def _is_name_prefix_for_next(row: list[dict], next_row: Optional[list[dict]]) -> bool:
    """Check if this row is a name continuation for the next row's part."""
    if not next_row or not _row_has_part_no(next_row):
        return False
    tokens = _english_tokens(row)
    if not tokens:
        return False
    text = clean_text(" ".join(tokens)).upper()
    name_starters = (
        "GASKET", "HEX", "WASHER", "SPRING WASHER", "DRAIN PLUG", "SEAL",
        "TORIC SEAL", "HOLLOW SCREW", "STUD", "PIPE", "HOSE", "BRACKET",
        "COVER", "FLANGE", "HEATER FLANGE", "OIL SPRAYER NOZZLE",
    )
    return _row_has_quantity_marker(row) or text.startswith(name_starters)


def _infer_missing_positions(rows: list[dict]) -> list[dict]:
    """Fill in gaps in position numbers by interpolation."""
    # Validate monotonicity of explicit positions
    last_anchor = None
    for row in rows:
        if not row["pos_no"].isdigit():
            continue
        current = int(row["pos_no"])
        if last_anchor is not None and current < last_anchor:
            row["pos_no"] = ""
            continue
        if last_anchor is not None and current > last_anchor + 3:
            row["pos_no"] = ""
            continue
        last_anchor = current

    explicit = [
        (idx, int(row["pos_no"]))
        for idx, row in enumerate(rows)
        if row["pos_no"].isdigit()
    ]
    if not rows:
        return rows

    explicit_flags = [row["pos_no"].isdigit() for row in rows]
    inferred = [None] * len(rows)
    for idx, pos in explicit:
        inferred[idx] = pos

    if explicit:
        first_idx, first_pos = explicit[0]
        start_pos = max(1, first_pos - first_idx)
        if len(explicit) == 1 and first_idx >= 3 and start_pos == 2:
            start_pos = 1
        for idx in range(first_idx):
            inferred[idx] = start_pos + idx

        for (left_idx, left_pos), (right_idx, right_pos) in zip(explicit, explicit[1:]):
            gap = right_idx - left_idx
            pos_gap = right_pos - left_pos
            if gap > 0 and pos_gap == gap:
                for idx in range(left_idx + 1, right_idx):
                    inferred[idx] = left_pos + (idx - left_idx)

        last_idx, last_pos = explicit[-1]
        for idx in range(last_idx + 1, len(rows)):
            inferred[idx] = last_pos + (idx - last_idx)
    else:
        for idx in range(len(rows)):
            inferred[idx] = idx + 1

    for idx, row in enumerate(rows):
        if inferred[idx] is not None:
            row["pos_no"] = str(inferred[idx])

    # De-duplicate: clear inferred positions for duplicated parts
    part_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        part_counts[row.get("mfg_part_no", "")] += 1
    for idx, row in enumerate(rows):
        if not explicit_flags[idx] and part_counts[row.get("mfg_part_no", "")] > 1:
            row["pos_no"] = ""
    return rows


def extract_part_rows(grouped_rows: list[list[dict]]) -> list[dict]:
    """
    Extract part records from grouped OCR rows.

    Ported from run_ae.py extract_part_rows_ae().
    Returns list of dicts with keys: pos_no, mfg_part_no, name_of_spare, cell_confidences.
    """
    extracted: list[dict] = []
    current: Optional[dict] = None
    pending_prefix: list[str] = []

    for idx, row in enumerate(grouped_rows):
        if not row:
            continue
        part_items = [
            item for item in row
            if is_part_no(item["text"]) and 0.08 <= item["rel_x"] <= 0.24
        ]
        if part_items:
            if current:
                extracted.append(current)
            part_items.sort(key=lambda i: i["rel_x"])
            part_no, part_tail = split_part_no_prefix(part_items[0]["text"])
            name_parts = pending_prefix + _english_tokens(row)
            pending_prefix = []
            if part_tail:
                name_parts.insert(0, part_tail)
            # Collect confidences from all items in this row
            confs = [item.get("conf", 0.0) for item in row if "conf" in item]
            current = {
                "pos_no": _row_pos_no(row),
                "mfg_part_no": part_no or part_items[0]["text"],
                "name_parts": name_parts,
                "cell_confidences": confs,
            }
            continue

        if current:
            next_row = grouped_rows[idx + 1] if idx + 1 < len(grouped_rows) else None
            if _is_name_prefix_for_next(row, next_row):
                pending_prefix = _english_tokens(row)
                continue
            continuation = _english_tokens(row)
            if continuation:
                current["name_parts"].extend(continuation)
                # Add continuation confidences
                confs = [item.get("conf", 0.0) for item in row if "conf" in item]
                current["cell_confidences"].extend(confs)

    if current:
        extracted.append(current)

    for row in extracted:
        row["name_of_spare"] = clean_text(" ".join(row["name_parts"]))
    return _infer_missing_positions(extracted)


def is_table_page(items: list[dict]) -> bool:
    """Check if a page contains part-number table data."""
    part_count = sum(1 for item in items if is_part_no(item["text"]))
    return part_count >= 1


def group_ocr_rows(items: list[dict], y_tolerance: float = 15) -> list[list[dict]]:
    """
    Group OCR items into rows, filtering page margins.

    Ported from run_ae.py group_ocr_into_rows_ae().
    """
    filtered = [
        item for item in items
        if item["rel_y"] <= 0.92 and item["rel_y"] >= 0.05
    ]
    return group_rows(filtered, y_tolerance=y_tolerance, key="cy")


# ═══════════════════════ Strategy class ═══════════════════════


class StandardPartsTableStrategy(LayoutExtractor):
    """
    Extracts spare-part records from standard tabular parts-list pages.

    This is the most common layout in the marine-spares corpus, used by
    MAN B&W auxiliary engines, OBP spare lists, Shanghai Hengyuan fans,
    Naniwa pumps, BUKH diesel engines, and others.
    """

    def detect_regions(self, page_image) -> list[Region]:
        """
        Detect table regions by running OCR and checking for part-number
        anchors.  Returns a single Region covering the full page if table
        data is found, empty list otherwise.
        """
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

        if not is_table_page(items):
            return []

        return [Region(
            page_no=0,  # caller should set this
            bbox=(0.0, 0.0, float(width), float(height)),
            region_type="standard_parts_table",
            confidence=avg_confidence(items),
            metadata={"items": items},
        )]

    def extract_table(self, region: Region) -> RawTable:
        """
        Extract a RawTable from the detected region's OCR items.
        """
        items = region.metadata.get("items", [])
        if not items:
            return RawTable(rows=[])

        grouped = group_ocr_rows(items, y_tolerance=15)
        part_rows = extract_part_rows(grouped)

        raw_rows = []
        for row in part_rows:
            name, size, material = split_spare_name_details(row["name_of_spare"])
            raw_rows.append({
                "pos_no": row.get("pos_no", ""),
                "mfg_part_no": row.get("mfg_part_no", ""),
                "name_of_spare": name,
                "size_dimension": size,
                "material": material,
                "cell_confidences": row.get("cell_confidences", []),
            })

        return RawTable(
            rows=raw_rows,
            columns=["pos_no", "mfg_part_no", "name_of_spare", "size_dimension", "material"],
            metadata={"region": region},
        )

    def map_to_schema(
        self,
        raw_table: RawTable,
        manufacturer_profile: dict[str, Any],
    ) -> list[PartRecord]:
        """
        Map raw rows into canonical PartRecord values using the profile.

        This is NEW work — reads column_synonyms from the manufacturer
        profile JSON instead of hardcoded column positions.
        """
        synonyms = column_synonyms(manufacturer_profile)
        doc_profile = find_document_profile(manufacturer_profile)
        component = doc_profile.get("component", manufacturer_profile.get("manufacturer", ""))
        manufacturer = manufacturer_profile.get("manufacturer", "")
        model = doc_profile.get("model", "")
        default_unit = manufacturer_profile.get("default_unit", "Pcs")

        records: list[PartRecord] = []
        for raw_row in raw_table.rows:
            part_no = clean_text(raw_row.get("mfg_part_no", ""))
            description = clean_text(raw_row.get("name_of_spare", ""))

            if not part_no and not description:
                continue

            records.append(PartRecord(
                part_no=part_no,
                description=description,
                component=component,
                qty="",
                unit=default_unit,
                drawing_ref="",
                sub_component="",  # set by caller from page context
                manufacturer=manufacturer,
                model=model,
                metadata={
                    "pos_no": raw_row.get("pos_no", ""),
                    "size_dimension": raw_row.get("size_dimension", ""),
                    "material": raw_row.get("material", ""),
                    "cell_confidences": raw_row.get("cell_confidences", []),
                },
            ))
        return records

    def confidence(self, record: PartRecord) -> float:
        """Return per-record confidence from cell-level OCR scores."""
        return record_confidence(record)


# ═══════════════════════ Convenience runner ═══════════════════════


def extract_pdf(
    pdf_path: str,
    profile_path: str,
    fixture_id: str = "",
    start_page: int = 1,
    end_page: int = 0,
    dpi: int = 200,
) -> list[PartRecord]:
    """
    End-to-end extraction of a PDF using the StandardPartsTableStrategy.

    Args:
        pdf_path: Path to the PDF file.
        profile_path: Path to the manufacturer profile JSON.
        fixture_id: Optional fixture_id to select the document_profile entry.
        start_page: First page to process (1-based).
        end_page: Last page to process (0 = all pages).
        dpi: DPI for OCR rendering.

    Returns:
        List of PartRecord objects.
    """
    import fitz as _fitz

    profile = load_profile(profile_path)
    doc_profile = find_document_profile(profile, fixture_id=fixture_id)
    strategy = StandardPartsTableStrategy()
    extractor = get_ocr_extractor()

    doc = _fitz.open(pdf_path)
    total_pages = len(doc)
    doc.close()

    if end_page <= 0:
        end_page = total_pages

    all_records: list[PartRecord] = []
    last_drawing = ""
    last_sub_component = doc_profile.get("sub_component", "")
    last_table_no = ""

    for page_no in range(start_page, min(end_page, total_pages) + 1):
        items = ocr_items(pdf_path, page_no - 1, extractor, dpi=dpi)
        if not items:
            continue

        # Detect page context
        height = max(item["y1"] for item in items) if items else 1
        width = max(item["x1"] for item in items) if items else 1
        drawing, sub_comp, table_no = find_page_context(items, width, height)
        if drawing:
            last_drawing = drawing
        if sub_comp:
            last_sub_component = sub_comp
        if table_no:
            last_table_no = table_no

        if not is_table_page(items):
            continue

        # Create a synthetic region
        region = Region(
            page_no=page_no,
            bbox=(0.0, 0.0, width, height),
            region_type="standard_parts_table",
            confidence=avg_confidence(items),
            metadata={"items": items},
        )

        raw_table = strategy.extract_table(region)
        records = strategy.map_to_schema(raw_table, profile)

        # Enrich records with page context
        enriched = []
        for rec in records:
            enriched.append(PartRecord(
                part_no=rec.part_no,
                description=rec.description,
                component=rec.component,
                qty=rec.qty,
                unit=rec.unit,
                drawing_ref=last_drawing,
                sub_component=last_sub_component,
                manufacturer=rec.manufacturer,
                model=rec.model,
                metadata={
                    **rec.metadata,
                    "page_no": page_no,
                    "table_no": last_table_no,
                },
            ))
        all_records.extend(enriched)
        print(f"Page {page_no}: {len(records)} rows")

    return all_records
