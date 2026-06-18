"""General-purpose spare parts extraction pipeline.

This module provides a layout-agnostic extraction engine that can handle
*any* marine spare-parts PDF.  It is designed to be the fallback when none
of the specialised ``run_test*`` pipelines match.

Architecture
------------
1. **Dual-mode text extraction** – try PyMuPDF embedded text first; fall
   back to PaddleOCR on a rendered image when too few items are found.
2. **Adaptive page classification** – each page is categorised as a
   table page, a drawing/metadata page, or a skip page (cover/index).
3. **Dynamic column detection** – table headers are located and used to
   define column boundaries; a heuristic fallback is used when no
   headers are found.
4. **Context carryover** – drawing numbers, sub-components, table
   numbers, and model strings propagate from drawing pages to subsequent
   table pages.
5. **Row extraction** – OCR items are grouped into rows, multi-line
   spare names are merged, and noise/header rows are filtered.
6. **Template output** – rows are mapped to the standard 21-column
   ``Spares_Capture_Template_Ver12 2.xlsm`` format.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import fitz
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
LOCAL_ENGINE_DIR = SCRIPT_DIR / "local_engine"

for _p in (str(SCRIPT_DIR), str(LOCAL_ENGINE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ocr_extractor import OCRExtractor
from pdf_converter import pdf_page_to_image

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & patterns
# ---------------------------------------------------------------------------

DEFAULT_UOM = "Pcs"
DRAWING_PAGE_WITH_POS = "Yes"

# Combined drawing-number regex covering patterns from all existing runners.
DRAWING_NO_RE = re.compile(
    r"(?:"
    # VOLUME-I style: 0570-0100-0001
    r"\d{4}-\d{4}-\d{4}"
    r"|"
    # AE style: 05 N 300, 02-N-142
    r"\d{2}\s*[-N]\s*[A-Z0-9][A-Z0-9/ -]{2,}"
    r"|"
    # Pump/fan style: CDSG0132, C0DS0132, 4DSG1234
    r"[A-Z0-9]{1,4}D[SGH]\d{4}"
    r"|"
    # Fan style: HFBZ70A-204WX
    r"HF[A-Z]{2}\s*[-]?\s*\d{1,3}[A-Z]?(?:-\d)?\s*[-]?\s*(?:1[O0]2|2[O0](?:[24]?W?X|X))"
    r")",
    re.IGNORECASE,
)

# Part-number patterns (common across pipelines).
PART_NO_PATTERNS = [
    # MAN-style: 51.12345-6789
    re.compile(r"\b\d{2}\.\d{5}[-\.]\d{3,5}\b"),
    # Dash-separated drawing part: 0570-0100-0001-012
    re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}\b"),
    # Generic alphanumeric codes: 6206-2RZ, RXM4AB2P7
    re.compile(r"\b[A-Z]{1,4}\d{3,}[-]?\d*[A-Z]*\b", re.IGNORECASE),
]

# Table header keywords (case-insensitive).
HEADER_KEYWORDS = {
    "item no", "item", "qty", "quantity", "designation", "description",
    "name", "part no", "part number", "parts no", "code no", "code",
    "material", "remarks", "remark", "drawing", "drawing no", "drwg",
    "serial number", "pos", "pos.", "no.", "no", "supply",
    "name of spare", "name of part", "spare parts",
}

# Words that indicate a row is a repeated/noise header.
HEADER_NOISE_RE = re.compile(
    r"^(?:item\s*no\.?|qty|quantity|designation|description|part\s*no\.?"
    r"|material|remarks?|code\s*no\.?|drawing\s*no\.?|pos\.?\s*no\.?"
    r"|name\s*of\s*(?:spare|part)|serial\s*number|supply\s*per\s*ship"
    r"|spare\s*parts?\s*list)$",
    re.IGNORECASE,
)

# Tokens to skip entirely.
SKIP_TOKENS_RE = re.compile(
    r"^(?:page\s*\d+|revision|rev\.?\s*\d|date|scale|checked|approved"
    r"|drawn|www\.|http|copyright|\u00a9)$",
    re.IGNORECASE,
)

# Common OCR character corrections.
OCR_CORRECTIONS = {
    "O": "0", "o": "0", "I": "1", "l": "1",
    "S": "5", "B": "8",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PageContext:
    """Metadata propagated between pages."""
    drawing_no: str = ""
    sub_component: str = ""
    table_no: str = ""
    model: str = ""
    manufacturer: str = ""
    component: str = ""


@dataclass
class ExtractedRow:
    """A single spare-parts row ready for template output."""
    component: str = ""
    sub_component: str = ""
    manufacturer: str = ""
    model: str = ""
    name_of_spare: str = ""
    mfg_part_no: str = ""
    drawing_no: str = ""
    pos_no: str = ""
    size_dimension: str = ""
    material: str = ""
    remarks: str = ""
    other_details: str = ""
    page_no: int = 0
    manual_pdf_name: str = ""
    uom: str = DEFAULT_UOM


@dataclass
class ColumnMap:
    """Detected column boundaries as relative-x ranges."""
    pos: tuple[float, float] = (0.0, 0.08)
    name: tuple[float, float] = (0.08, 0.40)
    part_no: tuple[float, float] = (0.40, 0.60)
    material: tuple[float, float] = (0.60, 0.78)
    qty: tuple[float, float] = (0.78, 0.88)
    remarks: tuple[float, float] = (0.88, 1.0)


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    """Normalize whitespace and strip noise characters."""
    return re.sub(r"\s+", " ", str(text or "").strip()).strip(" -|")


def _embedded_items(page: fitz.Page) -> list[dict[str, Any]]:
    """Extract text items from a page with embedded fonts (PyMuPDF)."""
    width = page.rect.width or 1
    height = page.rect.height or 1
    items: list[dict[str, Any]] = []
    for word in page.get_text("words"):
        text = clean_text(word[4])
        if not text:
            continue
        x0, y0, x1, y1 = map(float, word[:4])
        items.append({
            "text": text,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "cx": (x0 + x1) / 2,
            "cy": (y0 + y1) / 2,
            "rel_x": ((x0 + x1) / 2) / width,
            "rel_y": ((y0 + y1) / 2) / height,
            "width": width,
            "height": height,
        })
    return items


def _ocr_items(
    pdf_path: str | Path,
    page_idx: int,
    extractor: OCRExtractor,
    dpi: int = 200,
    rotate: bool = False,
) -> list[dict[str, Any]]:
    """Render a page to an image and run PaddleOCR."""
    import cv2

    image = pdf_page_to_image(str(pdf_path), page_idx, dpi=dpi)
    if rotate:
        image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    height, width = image.shape[:2]
    items: list[dict[str, Any]] = []
    results = extractor.extract_text(image)
    if not results:
        return items
    for box, (text, conf) in results:
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
            "cx": (x0 + x1) / 2,
            "cy": (y0 + y1) / 2,
            "rel_x": ((x0 + x1) / 2) / width,
            "rel_y": ((y0 + y1) / 2) / height,
            "width": width,
            "height": height,
            "conf": conf,
        })
    return items


def get_page_items(
    page: fitz.Page,
    pdf_path: str | Path,
    page_idx: int,
    extractor: OCRExtractor | None,
    min_embedded_items: int = 15,
) -> list[dict[str, Any]]:
    """Get text items from a page using embedded text or OCR fallback."""
    items = _embedded_items(page)
    if len(items) >= min_embedded_items:
        return items

    # Fallback to PaddleOCR
    if extractor is None:
        return items  # no OCR available, return what we have

    # Check if page is rotated (landscape)
    is_rotated = page.rect.width > page.rect.height * 1.3
    ocr_result = _ocr_items(pdf_path, page_idx, extractor, rotate=is_rotated)
    return ocr_result if len(ocr_result) > len(items) else items


# ---------------------------------------------------------------------------
# Row grouping
# ---------------------------------------------------------------------------

def group_into_rows(
    items: list[dict[str, Any]],
    y_tolerance: float = 0.012,
    y_min_filter: float = 0.04,
    y_max_filter: float = 0.96,
) -> list[list[dict[str, Any]]]:
    """Group text items into rows by vertical proximity (relative coords)."""
    filtered = [
        item for item in items
        if y_min_filter <= item["rel_y"] <= y_max_filter
    ]
    filtered.sort(key=lambda it: (it["rel_y"], it["rel_x"]))

    rows: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_y: float | None = None

    for item in filtered:
        if current_y is None or abs(item["rel_y"] - current_y) <= y_tolerance:
            current.append(item)
            current_y = (
                item["rel_y"]
                if current_y is None
                else (current_y + item["rel_y"]) / 2
            )
        else:
            current.sort(key=lambda it: it["rel_x"])
            rows.append(current)
            current = [item]
            current_y = item["rel_y"]

    if current:
        current.sort(key=lambda it: it["rel_x"])
        rows.append(current)
    return rows


def row_text(row: list[dict[str, Any]]) -> str:
    """Join all items in a row into a single text string."""
    return clean_text(" ".join(
        item["text"] for item in sorted(row, key=lambda it: it["rel_x"])
    ))


def page_full_text(items: list[dict[str, Any]]) -> str:
    """Build the full-page text from items."""
    return "\n".join(row_text(row) for row in group_into_rows(items, y_tolerance=0.008))


# ---------------------------------------------------------------------------
# Page classification
# ---------------------------------------------------------------------------

def _has_table_headers(text: str) -> bool:
    """Check if page text contains common table header keywords."""
    lower = text.lower()
    matches = sum(1 for kw in HEADER_KEYWORDS if kw in lower)
    return matches >= 2


def _count_part_number_items(items: list[dict[str, Any]]) -> int:
    """Count items that look like part numbers."""
    count = 0
    for item in items:
        for pattern in PART_NO_PATTERNS:
            if pattern.search(item["text"]):
                count += 1
                break
    return count


def _digit_density(items: list[dict[str, Any]]) -> float:
    """Fraction of items that contain at least one digit."""
    if not items:
        return 0.0
    digit_items = sum(1 for item in items if any(ch.isdigit() for ch in item["text"]))
    return digit_items / len(items)


def classify_page(items: list[dict[str, Any]]) -> str:
    """Classify a page as 'table', 'drawing', or 'skip'.

    Returns one of: ``'table'``, ``'drawing'``, ``'skip'``.
    """
    if not items:
        return "skip"

    full_text = page_full_text(items)
    upper = full_text.upper()

    # Cover/index pages
    if len(items) < 8:
        return "skip" if not _has_table_headers(full_text) else "drawing"

    # Strong table signal: header keywords + data rows
    has_headers = _has_table_headers(full_text)
    part_count = _count_part_number_items(items)
    density = _digit_density(items)

    # Pages with "SPARE PARTS LIST" or similar are definitely table pages
    table_markers = [
        "SPARE PARTS LIST", "SPARE PART LIST", "PARTS LIST",
        "MATERIAL LIST", "ACCESSORIES FOR EACH",
        "FAN SPARE LIST",
    ]
    has_table_marker = any(marker in upper for marker in table_markers)

    if has_table_marker and (has_headers or part_count >= 2):
        return "table"

    if has_headers and part_count >= 3:
        return "table"

    if has_headers and density >= 0.30 and len(items) >= 15:
        return "table"

    # If we have many part numbers, it's likely a table even without headers
    if part_count >= 5:
        return "table"

    # Drawing page: has a drawing number or very few text items relative to page area
    if DRAWING_NO_RE.search(full_text.replace(" ", "")):
        # Could be a drawing page or a table page with drawing references.
        # If there's enough data, it's a table page with embedded drawing info.
        if has_headers or part_count >= 2 or len(items) >= 25:
            return "table"
        return "drawing"

    # Heuristic: pages with reasonable text density are potential data pages
    if len(items) >= 20 and density >= 0.25:
        return "table"

    # Default to drawing for pages with some content
    if len(items) >= 5:
        return "drawing"

    return "skip"


# ---------------------------------------------------------------------------
# Column detection
# ---------------------------------------------------------------------------

def _find_header_row(rows: list[list[dict[str, Any]]]) -> list[dict[str, Any]] | None:
    """Find the row most likely to be the table header."""
    best_row = None
    best_score = 0

    for row in rows[:12]:  # only check first 12 rows
        score = 0
        for item in row:
            text_lower = item["text"].lower().strip(".:;,")
            if text_lower in HEADER_KEYWORDS:
                score += 1
        if score > best_score:
            best_score = score
            best_row = row

    return best_row if best_score >= 2 else None


def _header_keyword_type(text: str) -> str | None:
    """Classify a header keyword into a column type."""
    lower = text.lower().strip(".:;,()")
    if lower in {"item no", "item", "pos", "pos.", "no.", "no", "serial number"}:
        return "pos"
    if lower in {"qty", "quantity", "supply"}:
        return "qty"
    if lower in {"designation", "description", "name", "name of spare", "name of part", "spare parts"}:
        return "name"
    if lower in {"part no", "part number", "parts no", "code no", "code"}:
        return "part_no"
    if lower in {"material"}:
        return "material"
    if lower in {"remarks", "remark"}:
        return "remarks"
    if lower in {"drawing", "drawing no", "drwg"}:
        return "drawing"
    return None


def detect_columns(rows: list[list[dict[str, Any]]]) -> ColumnMap:
    """Detect column boundaries from the header row, or use defaults."""
    header = _find_header_row(rows)
    if header is None:
        return _heuristic_columns(rows)

    # Map header items to column types and record their X positions
    type_positions: dict[str, list[float]] = {}
    for item in header:
        col_type = _header_keyword_type(item["text"])
        if col_type:
            type_positions.setdefault(col_type, []).append(item["rel_x"])

    if len(type_positions) < 2:
        return _heuristic_columns(rows)

    # Sort all detected columns by position
    sorted_cols = sorted(
        [(min(positions), col_type) for col_type, positions in type_positions.items()],
        key=lambda pair: pair[0],
    )

    # Build column map from detected positions
    col_map = ColumnMap()
    for idx, (pos, col_type) in enumerate(sorted_cols):
        # Right boundary: midpoint to next column, or 1.0 for the last one
        if idx + 1 < len(sorted_cols):
            right = (pos + sorted_cols[idx + 1][0]) / 2
        else:
            right = 1.0
        # Left boundary: midpoint from previous column, or 0.0 for the first one
        if idx > 0:
            left = (sorted_cols[idx - 1][0] + pos) / 2
        else:
            left = 0.0

        bounds = (max(0.0, left - 0.02), min(1.0, right + 0.02))
        if col_type == "pos":
            col_map.pos = bounds
        elif col_type == "name":
            col_map.name = bounds
        elif col_type == "part_no":
            col_map.part_no = bounds
        elif col_type == "material":
            col_map.material = bounds
        elif col_type == "qty":
            col_map.qty = bounds
        elif col_type == "remarks":
            col_map.remarks = bounds

    return col_map


def _heuristic_columns(rows: list[list[dict[str, Any]]]) -> ColumnMap:
    """Heuristic column assignment when no header row is found.

    Uses the distribution of items across the page to guess column
    boundaries.  Falls back to sensible defaults.
    """
    if not rows:
        return ColumnMap()

    # Collect all rel_x values
    all_x = [item["rel_x"] for row in rows for item in row]
    if not all_x:
        return ColumnMap()

    # Very simple: divide the page into proportional columns
    # based on typical marine spare-parts table layouts
    min_x = min(all_x)
    max_x = max(all_x)
    span = max_x - min_x
    if span < 0.2:
        return ColumnMap()

    # Default proportional splits
    return ColumnMap(
        pos=(min_x, min_x + span * 0.08),
        name=(min_x + span * 0.08, min_x + span * 0.42),
        part_no=(min_x + span * 0.42, min_x + span * 0.62),
        material=(min_x + span * 0.62, min_x + span * 0.80),
        qty=(min_x + span * 0.80, min_x + span * 0.90),
        remarks=(min_x + span * 0.90, max_x + 0.02),
    )


# ---------------------------------------------------------------------------
# Context extraction (drawing pages)
# ---------------------------------------------------------------------------

# Regex for labeled drawing numbers: DWG No., DR No., Drawing No., DRWG No., Drwg.No.
_LABELED_DRAWING_RE = re.compile(
    r"(?:DWG|DR|DRAWING|DRWG|Drwg)\s*[.]?\s*(?:No|NO|no)\s*[.:]?\s*",
    re.IGNORECASE,
)

# Patterns that identify the sub-component from surrounding text.
# e.g.  "SPARE OF F.O. PUMP"  →  "F.O. Pump"
_SUB_COMPONENT_TITLE_PATTERNS = [
    # "SPARE OF <component>" or "SPARE PARTS OF <component>"
    re.compile(
        r"SPARE\s+(?:PARTS?\s+)?(?:OF|FOR)\s+(.+)",
        re.IGNORECASE,
    ),
    # "ADDITIONAL SPARE PARTS LIST" with the component name on the same line
    re.compile(
        r"ADDITIONAL\s+SPARE\s+PARTS?\s+LIST\s*[-:]?\s*(.+)",
        re.IGNORECASE,
    ),
]

# Words / phrases to exclude when they appear as sub-component candidates.
_SUB_STOP_WORDS = {
    "designation", "name of spare", "name of part", "component name",
    "remarks", "remark", "material", "item no", "item", "qty", "quantity",
    "code no", "code", "description", "drawing", "drawing no",
    "serial number", "hyundai", "man b&w", "plate", "spare parts list",
    "spare part list", "parts list", "date", "scale", "page", "no.",
    "no", "pos", "pos.", "supply", "sketch", "spec./material",
    "yard work'g", "spare", "supply per ship",
    "additional spare parts list", "additional",
    "head office", "pohang factory", "tel", "fax", "home", "e-mail",
    "iso9001", "iso14001", "iso45001", "abs",
    "project", "shipyard", "hull no", "hull no.",
}

# Model-related label patterns.
_MODEL_LABEL_RE = re.compile(
    r"(?:MODEL|Type|TYPE)\s*(?:No\.?|NO\.?)?\s*:?\s*",
    re.IGNORECASE,
)


def _extract_labeled_drawing_no(
    items: list[dict[str, Any]],
    full_text: str,
) -> str:
    """Find a drawing number that follows a label like DWG No., DR No., etc."""

    # Strategy 1: scan the full text for "DWG No." followed by value
    for line in full_text.split("\n"):
        m = _LABELED_DRAWING_RE.search(line)
        if m:
            value = clean_text(line[m.end():])
            # Take only the first token-like value (stop at whitespace or common noise)
            value = re.split(r"\s{2,}|\|", value)[0]
            value = clean_text(value)
            if value and len(value) >= 3:
                return value

    # Strategy 2: positional — find a DWG/DR label item and grab the next
    # item to its right or below on the same row.
    sorted_items = sorted(items, key=lambda it: (it["rel_y"], it["rel_x"]))
    for idx, item in enumerate(sorted_items):
        if _LABELED_DRAWING_RE.search(item["text"]):
            # Check if the value is embedded in the same OCR box
            remainder = _LABELED_DRAWING_RE.sub("", item["text"]).strip()
            if remainder and len(remainder) >= 3:
                return clean_text(remainder)
            # Otherwise look at the next item(s) on the same y-band
            for other in sorted_items[idx + 1: idx + 5]:
                if abs(other["rel_y"] - item["rel_y"]) < 0.025:
                    candidate = clean_text(other["text"])
                    if candidate and len(candidate) >= 3:
                        # Skip if the candidate is just another label
                        if not _LABELED_DRAWING_RE.search(candidate):
                            return candidate
    return ""


def _extract_sub_component(
    items: list[dict[str, Any]],
    full_text: str,
) -> str:
    """Extract the sub-component (equipment name) from the page.

    Looks for patterns like:
    - "SPARE OF F.O. PUMP" (title block, often at the bottom of a drawing page)
    - A prominent header such as "Aux. Boiler" near the top of the page
    - Text immediately below "ADDITIONAL SPARE PARTS LIST"
    """

    # ── Strategy 1: regex patterns in full text ──────────────────────────
    for pattern in _SUB_COMPONENT_TITLE_PATTERNS:
        m = pattern.search(full_text)
        if m:
            value = clean_text(m.group(1))
            # Remove trailing noise like page numbers
            value = re.sub(r"\s+\d+\s*(?:OF|of)\s+\d+\s*$", "", value)
            value = re.sub(r"\s+(?:CODE|PAGE|SCALE)\b.*$", "", value, flags=re.IGNORECASE)
            value = clean_text(value)
            if value and len(value) >= 3:
                return value.title()

    # ── Strategy 2: look for a standalone title near the top ─────────────
    #   Many pages (like the Aux. Boiler image) have the component name as
    #   a standalone line in the upper-left, just below the header area.
    rows = group_into_rows(items, y_tolerance=0.010)
    for row in rows:
        avg_y = sum(it["rel_y"] for it in row) / len(row)
        if avg_y > 0.35:  # only look in the top third
            break

        text = row_text(row)
        lower = text.lower().strip()

        # Skip noise
        if lower in _SUB_STOP_WORDS:
            continue
        if any(lower.startswith(sw) for sw in _SUB_STOP_WORDS if len(sw) > 4):
            continue
        if len(text) < 3 or len(text) > 60:
            continue
        # Skip lines that are clearly company names / addresses
        if re.search(r"(?:CO\.,?\s*LTD|INC\.|CORP\.|factory|office|Tel|Fax|www\.|@)", text, re.IGNORECASE):
            continue
        # Skip lines that are purely numeric or dates
        if re.fullmatch(r"[\d\s./-]+", text):
            continue
        # Skip table header rows
        header_count = sum(1 for it in row if it["text"].lower().strip(".:;,") in HEADER_KEYWORDS)
        if header_count >= 2:
            continue

        # A good candidate is a short, descriptive phrase in the upper region
        # that reads like an equipment name (alphabetic-dominant)
        alpha_ratio = sum(1 for ch in text if ch.isalpha()) / max(len(text), 1)
        if alpha_ratio >= 0.60:
            return text.title() if text.isupper() else text

    # ── Strategy 3: look in the bottom title block (drawing pages) ───────
    #   Many drawings have a title block in the bottom-right containing
    #   something like "SPARE OF F.O. PUMP".
    for row in reversed(rows):
        avg_y = sum(it["rel_y"] for it in row) / len(row)
        if avg_y < 0.75:
            break
        text = row_text(row)
        for pattern in _SUB_COMPONENT_TITLE_PATTERNS:
            m = pattern.search(text)
            if m:
                value = clean_text(m.group(1))
                value = re.sub(r"\s+(?:CODE|PAGE|SCALE)\b.*$", "", value, flags=re.IGNORECASE)
                value = clean_text(value)
                if value and len(value) >= 3:
                    return value.title()

    return ""


def _extract_model(
    items: list[dict[str, Any]],
    full_text: str,
) -> str:
    """Extract model number from the page text.

    Looks for labels like MODEL:, Type:, TYPE NO.:, etc.
    """

    # Strategy 1: scan each line for model label
    for line in full_text.split("\n"):
        m = _MODEL_LABEL_RE.search(line)
        if m:
            value = clean_text(line[m.end():])
            # Take the first meaningful token
            value = re.split(r"\s{2,}|\|", value)[0]
            value = re.sub(r"\s+\d+\s*SETS?\b.*$", "", value, flags=re.IGNORECASE)
            value = re.sub(r"\s+TOTAL\b.*$", "", value, flags=re.IGNORECASE)
            value = clean_text(value)
            if value and len(value) >= 2:
                return value

    # Strategy 2: positional — find a MODEL label item and grab adjacent value
    sorted_items = sorted(items, key=lambda it: (it["rel_y"], it["rel_x"]))
    for idx, item in enumerate(sorted_items):
        if _MODEL_LABEL_RE.search(item["text"]):
            remainder = _MODEL_LABEL_RE.sub("", item["text"]).strip()
            if remainder and len(remainder) >= 2:
                return clean_text(remainder)
            for other in sorted_items[idx + 1: idx + 5]:
                if abs(other["rel_y"] - item["rel_y"]) < 0.025:
                    candidate = clean_text(other["text"])
                    if candidate and len(candidate) >= 2 and not _MODEL_LABEL_RE.search(candidate):
                        return candidate
    return ""


def extract_page_context(
    items: list[dict[str, Any]],
    prev_ctx: PageContext,
) -> PageContext:
    """Extract metadata (drawing no, sub-component, model, etc.) from a page.

    Sub-component is determined per-page — it is the equipment name that
    the spare parts on this page belong to (e.g. "F.O. Pump", "Aux. Boiler").

    Drawing number is found by searching for labeled values like
    ``DWG No. BNR-SP-01`` or ``Drawing No. 1234-5678-9012``.

    Model is always extracted from the page, never from optional user input.
    """
    ctx = PageContext(
        drawing_no=prev_ctx.drawing_no,
        sub_component=prev_ctx.sub_component,
        table_no=prev_ctx.table_no,
        model=prev_ctx.model,
        manufacturer=prev_ctx.manufacturer,
        component=prev_ctx.component,
    )

    full_text = page_full_text(items)

    # ── Drawing number ──────────────────────────────────────────────────
    # 1) First try labeled drawing numbers (DWG No., DR No., etc.)
    labeled_dwg = _extract_labeled_drawing_no(items, full_text)
    if labeled_dwg:
        ctx.drawing_no = labeled_dwg
    else:
        # 2) Fallback to the bare regex patterns
        compact_text = full_text.replace(" ", "")
        match = DRAWING_NO_RE.search(compact_text)
        if match:
            ctx.drawing_no = clean_text(match.group(0))

    # ── Table number (ELTIS-style) ──────────────────────────────────────
    table_match = re.search(
        r"\b(?:ELTIS|#)\s*[A-Z0-9]{6,}\b", full_text, re.IGNORECASE,
    )
    if table_match:
        ctx.table_no = clean_text(table_match.group(0)).replace(" ", "").upper()

    # ── Sub-component (per-page equipment name) ─────────────────────────
    sub = _extract_sub_component(items, full_text)
    if sub:
        ctx.sub_component = sub

    # ── Model (extracted from the page, mandatory) ──────────────────────
    page_model = _extract_model(items, full_text)
    if page_model:
        ctx.model = page_model

    return ctx


# ---------------------------------------------------------------------------
# Row extraction
# ---------------------------------------------------------------------------

def _is_header_row(row: list[dict[str, Any]]) -> bool:
    """Check if a row is a repeated table header."""
    texts = [item["text"].lower().strip(".:;,") for item in row]
    header_count = sum(1 for t in texts if t in HEADER_KEYWORDS)
    return header_count >= 2


def _is_noise_row(row: list[dict[str, Any]]) -> bool:
    """Check if a row contains only noise/skip tokens."""
    for item in row:
        text = clean_text(item["text"])
        if not text:
            continue
        if SKIP_TOKENS_RE.match(text):
            continue
        if len(text) <= 2 and not text.isdigit():
            continue
        return False
    return True


def _extract_column(
    row: list[dict[str, Any]],
    x_range: tuple[float, float],
) -> str:
    """Extract text from items within the given relative-x range."""
    parts = [
        item["text"]
        for item in sorted(row, key=lambda it: it["rel_x"])
        if x_range[0] <= item["rel_x"] <= x_range[1]
    ]
    return clean_text(" ".join(parts))


def _looks_like_pos_no(text: str) -> bool:
    """Check if text looks like a position/item number."""
    text = text.strip()
    if not text:
        return False
    if text.isdigit() and len(text) <= 4:
        return True
    if re.fullmatch(r"\d{1,4}[A-Za-z]?", text):
        return True
    return False


def _looks_like_part_no(text: str) -> bool:
    """Check if text contains a part number pattern."""
    for pattern in PART_NO_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _looks_like_data_row(row: list[dict[str, Any]], col_map: ColumnMap) -> bool:
    """Check if a row looks like it contains extractable data."""
    pos_text = _extract_column(row, col_map.pos)
    name_text = _extract_column(row, col_map.name)
    part_text = _extract_column(row, col_map.part_no)

    # A data row should have at least a position number OR a part number,
    # AND some name text.
    has_pos = _looks_like_pos_no(pos_text)
    has_part = _looks_like_part_no(part_text)
    has_name = len(name_text) >= 2

    return (has_pos or has_part) and has_name


def extract_rows(
    rows: list[list[dict[str, Any]]],
    col_map: ColumnMap,
    page_no: int,
    ctx: PageContext,
    manual_pdf_name: str,
) -> list[ExtractedRow]:
    """Extract spare-parts rows from grouped text rows."""
    extracted: list[ExtractedRow] = []
    current: ExtractedRow | None = None
    inferred_pos = 0

    for row in rows:
        # Skip header and noise rows
        if _is_header_row(row):
            continue
        if _is_noise_row(row):
            continue

        pos_text = _extract_column(row, col_map.pos)
        name_text = _extract_column(row, col_map.name)
        part_text = _extract_column(row, col_map.part_no)
        material_text = _extract_column(row, col_map.material)
        qty_text = _extract_column(row, col_map.qty)
        remarks_text = _extract_column(row, col_map.remarks)

        has_pos = _looks_like_pos_no(pos_text)
        has_part = _looks_like_part_no(part_text)
        has_name = len(name_text) >= 2

        # Start a new row when we see a position number or part number
        if (has_pos or has_part) and (has_name or has_part):
            # Save previous row
            if current is not None:
                extracted.append(current)

            inferred_pos += 1
            pos = pos_text if has_pos else str(inferred_pos)

            # If name_text is empty but the full row has enough text,
            # concatenate all non-classified items as the name
            if not has_name:
                all_text = row_text(row)
                # Remove the pos and part from it
                name_text = all_text
                if has_pos:
                    name_text = name_text.replace(pos_text, "", 1)
                if has_part:
                    name_text = name_text.replace(part_text, "", 1)
                name_text = clean_text(name_text)

            current = ExtractedRow(
                component=ctx.component or "Spare Parts",
                sub_component=ctx.sub_component or "",
                manufacturer=ctx.manufacturer or "",
                model=ctx.model or "",
                name_of_spare=name_text,
                mfg_part_no=part_text if has_part else "",
                drawing_no=ctx.drawing_no or "",
                pos_no=pos,
                size_dimension="",
                material=material_text,
                remarks=remarks_text,
                other_details=f"Qty: {qty_text}" if qty_text else "",
                page_no=page_no,
                manual_pdf_name=manual_pdf_name,
            )
        elif current is not None and has_name:
            # Continuation row: append name text to current row
            # Only merge if this row doesn't look like a standalone data row
            if not (has_pos and has_part):
                current.name_of_spare = clean_text(
                    f"{current.name_of_spare} {name_text}"
                )
                if material_text and not current.material:
                    current.material = material_text
                if remarks_text and not current.remarks:
                    current.remarks = remarks_text

    # Don't forget the last row
    if current is not None:
        extracted.append(current)

    return extracted

# ---------------------------------------------------------------------------
# Custom multi-layout parsers (Accessories, Side-material)
# ---------------------------------------------------------------------------

def _extract_accessories_rows(items: list[dict[str, Any]], page_no: int, ctx: PageContext, manual_pdf_name: str) -> list[ExtractedRow]:
    text = page_full_text(items).upper()
    if "ACCESSORIES FOR EACH" not in text and "ACCESSORY" not in text:
        return []
        
    rows = []
    candidates = [it for it in items if it["rel_x"] < 0.45 and not re.search(r"^(?:DESCRIPTION|ACCESSORIES|Q'? ?TY)$", it["text"], re.IGNORECASE)]
    if not candidates:
        return []
        
    for row_items in group_into_rows(candidates, y_tolerance=0.012):
        name_text = clean_text(" ".join(it["text"] for it in sorted(row_items, key=lambda x: x["rel_x"]) if it["rel_x"] < 0.35))
        if not name_text or len(name_text) < 2: continue
        if re.search(r"\b(?:SPECIFICATION|SUCTION|DELIVERY|CAPACITY|TOTAL|RULE|SCALE|DRAWING|REMARKS)\b", name_text, re.IGNORECASE): continue
        
        y_mid = sum(it["rel_y"] for it in row_items) / len(row_items)
        qty_text = clean_text(" ".join(it["text"] for it in items if 0.35 <= it["rel_x"] <= 0.45 and abs(it["rel_y"] - y_mid) < 0.015))
        
        rows.append(ExtractedRow(
            component=ctx.component or "Spare Parts",
            sub_component=ctx.sub_component or "Accessories",
            manufacturer=ctx.manufacturer or "",
            model=ctx.model or "",
            name_of_spare=name_text,
            mfg_part_no="",
            drawing_no=ctx.drawing_no or "",
            pos_no=str(len(rows) + 1),
            size_dimension="",
            material="",
            remarks="Accessories",
            other_details=f"Qty: {qty_text}" if qty_text else "",
            page_no=page_no,
            manual_pdf_name=manual_pdf_name,
        ))
    return rows

def _extract_side_material_rows(items: list[dict[str, Any]], page_no: int, ctx: PageContext, manual_pdf_name: str) -> list[ExtractedRow]:
    text = page_full_text(items).upper()
    if "NAME OF PART" not in text or "MATERIAL" not in text:
        return []
        
    def process_side(side_items: list[dict[str, Any]], x_max: float) -> list[ExtractedRow]:
        anchors = [it for it in side_items if len(re.sub(r"\D", "", it["text"])) >= 2 and _looks_like_pos_no(it["text"])]
        if not anchors: return []
        anchors.sort(key=lambda it: it["rel_y"])
        side_rows = []
        for idx, anchor in enumerate(anchors):
            prev_y = anchors[idx - 1]["rel_y"] if idx else max(anchor["rel_y"] - 0.025, 0.05)
            next_y = anchors[idx + 1]["rel_y"] if idx + 1 < len(anchors) else min(anchor["rel_y"] + 0.025, 0.95)
            top = max((prev_y + anchor["rel_y"]) / 2, 0.05)
            bottom = min((anchor["rel_y"] + next_y) / 2, 0.95)
            if idx == 0: top = max(anchor["rel_y"] - 0.015, 0.05)
            
            band = [it for it in side_items if top <= it["rel_y"] < bottom]
            pos_text = clean_text(anchor["text"])
            name_text = clean_text(" ".join(it["text"] for it in sorted(band, key=lambda x: x["rel_x"]) if anchor["rel_x"] + 0.02 < it["rel_x"] < x_max - 0.15))
            if not name_text or "NAME OF PART" in name_text.upper(): continue
            
            side_rows.append(ExtractedRow(
                component=ctx.component or "Spare Parts",
                sub_component=ctx.sub_component or "",
                manufacturer=ctx.manufacturer or "",
                model=ctx.model or "",
                name_of_spare=name_text,
                mfg_part_no="",
                drawing_no=ctx.drawing_no or "",
                pos_no=pos_text,
                size_dimension="",
                material="",
                remarks="Side material list",
                page_no=page_no,
                manual_pdf_name=manual_pdf_name,
            ))
        return side_rows

    left_items = [it for it in items if it["rel_x"] < 0.48]
    right_items = [it for it in items if it["rel_x"] >= 0.48]
    
    rows = process_side(left_items, 0.48)
    rows.extend(process_side(right_items, 1.0))
    return rows

def _extract_custom_layouts(items: list[dict[str, Any]], page_no: int, ctx: PageContext, manual_pdf_name: str) -> list[ExtractedRow]:
    rows = _extract_accessories_rows(items, page_no, ctx, manual_pdf_name)
    if rows: return rows
    
    rows = _extract_side_material_rows(items, page_no, ctx, manual_pdf_name)
    if len(rows) >= 5: return rows
    
    return []


# ---------------------------------------------------------------------------
# Fallback: simple full-row extraction for unusual layouts
# ---------------------------------------------------------------------------

def _fallback_extract_rows(
    rows: list[list[dict[str, Any]]],
    page_no: int,
    ctx: PageContext,
    manual_pdf_name: str,
) -> list[ExtractedRow]:
    """Fallback extraction when column detection produces poor results.

    This tries to extract any row that has a recognizable part number
    or position number, using the full row text as the spare name.
    """
    extracted: list[ExtractedRow] = []
    pos_counter = 0

    for row in rows:
        if _is_header_row(row) or _is_noise_row(row):
            continue

        text = row_text(row)
        if len(text) < 5:
            continue

        # Look for a part number anywhere in the row
        part_no = ""
        for pattern in PART_NO_PATTERNS:
            match = pattern.search(text)
            if match:
                part_no = match.group(0)
                break

        # Look for a position number at the start
        pos_no = ""
        first_item = row[0] if row else None
        if first_item and first_item["rel_x"] < 0.15:
            candidate = clean_text(first_item["text"])
            if _looks_like_pos_no(candidate):
                pos_no = candidate

        if not part_no and not pos_no:
            continue

        pos_counter += 1
        name = text
        # Remove the part number from the name
        if part_no:
            name = name.replace(part_no, "", 1)
        if pos_no:
            name = re.sub(r"^\s*" + re.escape(pos_no) + r"\s+", "", name)
        name = clean_text(name)

        if not name:
            continue

        extracted.append(ExtractedRow(
            component=ctx.component or "Spare Parts",
            sub_component=ctx.sub_component or "",
            manufacturer=ctx.manufacturer or "",
            model=ctx.model or "",
            name_of_spare=name,
            mfg_part_no=part_no,
            drawing_no=ctx.drawing_no or "",
            pos_no=pos_no or str(pos_counter),
            page_no=page_no,
            manual_pdf_name=manual_pdf_name,
        ))

    return extracted


# ---------------------------------------------------------------------------
# Template output
# ---------------------------------------------------------------------------

def _extracted_pdf_name(sub_component: str) -> str:
    """Generate extracted PDF name from sub-component."""
    token = re.sub(r"[^A-Za-z0-9]+", "", sub_component)
    return f"General_{token or 'Spares'}.pdf"


def to_template_row(record: ExtractedRow) -> list[object]:
    """Convert an ExtractedRow to a 21-column template row."""
    return [
        record.component,                          # A: Component Name
        record.sub_component,                       # B: Sub Component Name
        record.manufacturer,                        # C: Manufacturer
        record.model,                               # D: Model
        record.name_of_spare,                       # E: Name Of Spare
        record.mfg_part_no,                         # F: MfgPart No
        record.drawing_no,                          # G: Drwg.No
        record.pos_no,                              # H: Pos. No.
        record.size_dimension,                      # I: Size & Dimension
        record.material,                            # J: Material
        record.remarks,                             # K: Remarks
        record.other_details,                       # L: Other details if any
        record.page_no,                             # M: Page No
        record.manual_pdf_name,                     # N: Manual Pdf Name
        "",                                         # O: Referance No 1
        record.uom,                                 # P: Uom
        _extracted_pdf_name(record.sub_component),  # Q: Extracted Pdf name
        "",                                         # R: Drawing Page Without Pos.No
        DRAWING_PAGE_WITH_POS,                      # S: Drawing Page With Pos.No
        "",                                         # T: Colour Identification
        "",                                         # U: Component Linking
    ]


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------

CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[str, int, int, int], None]


def process_pdf(
    pdf_path: str | Path,
    pages_to_process: list[int],
    component: str = "",
    manufacturer: str = "",
    model: str = "",
    cancel_check: CancelCheck | None = None,
    progress_callback: ProgressCallback | None = None,
) -> list[list[object]]:
    """Run the general extraction pipeline on selected PDF pages.

    Parameters
    ----------
    pdf_path:
        Path to the input PDF.
    pages_to_process:
        1-based page numbers to extract.
    component, manufacturer:
        Optional metadata overrides from the UI.
    model:
        Extracted from page content automatically.  A user-supplied
        value here is only used as a fallback if extraction fails.
    cancel_check:
        Callable returning True when the user cancels extraction.
    progress_callback:
        Callable ``(stage, processed, total, rows)`` for UI updates.

    Returns
    -------
    list[list[object]]
        Template rows ready to write to the Excel macro workbook.
    """
    pdf_path = Path(pdf_path)
    manual_pdf_name = pdf_path.name

    # Initialize OCR extractor (lazy — only created if needed)
    extractor: OCRExtractor | None = None

    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)

    ctx = PageContext(
        component=component,
        manufacturer=manufacturer,
        model=model,  # used as fallback only; page extraction takes priority
    )

    all_records: list[ExtractedRow] = []
    processed = 0
    total = len(pages_to_process)

    try:
        for page_no in pages_to_process:
            # Cancel check
            if cancel_check and cancel_check():
                break

            page_idx = page_no - 1
            if page_idx < 0 or page_idx >= total_pages:
                processed += 1
                continue

            page = doc[page_idx]

            # Get text items (embedded or OCR)
            items = _embedded_items(page)
            if len(items) < 15:
                if extractor is None:
                    extractor = OCRExtractor()
                is_rotated = page.rect.width > page.rect.height * 1.3
                ocr_result = _ocr_items(
                    str(pdf_path), page_idx, extractor, rotate=is_rotated,
                )
                if len(ocr_result) > len(items):
                    items = ocr_result

            if not items:
                processed += 1
                if progress_callback:
                    progress_callback(
                        "Processing pages", processed, total, len(all_records),
                    )
                continue

            # Classify page
            page_type = classify_page(items)

            # Extract context from every page (drawing pages are primary,
            # but table pages may also have metadata in headers)
            page_ctx = extract_page_context(items, ctx)

            # Update running context — sub_component is always per-page
            if page_ctx.drawing_no:
                ctx.drawing_no = page_ctx.drawing_no
            if page_ctx.sub_component:
                ctx.sub_component = page_ctx.sub_component
            if page_ctx.table_no:
                ctx.table_no = page_ctx.table_no
            # Model: page extraction always wins over user fallback
            if page_ctx.model:
                ctx.model = page_ctx.model
            # Preserve user-supplied overrides for component & manufacturer only
            if component:
                ctx.component = component
            if manufacturer:
                ctx.manufacturer = manufacturer

            custom_records = _extract_custom_layouts(items, page_no, ctx, manual_pdf_name)
            if custom_records:
                all_records.extend(custom_records)
                processed += 1
                if progress_callback:
                    progress_callback("Processing pages", processed, total, len(all_records))
                continue

            if page_type == "table":
                rows = group_into_rows(items)
                col_map = detect_columns(rows)

                page_records = extract_rows(
                    rows, col_map, page_no, ctx, manual_pdf_name,
                )

                # If primary extraction found very few rows, try fallback
                if len(page_records) < 2:
                    fallback_records = _fallback_extract_rows(
                        rows, page_no, ctx, manual_pdf_name,
                    )
                    if len(fallback_records) > len(page_records):
                        page_records = fallback_records

                all_records.extend(page_records)

            elif page_type == "drawing":
                # Drawing pages: just update context, no data rows
                # But some drawing pages also have a parts table embedded
                rows = group_into_rows(items)
                if len(rows) >= 8:
                    col_map = detect_columns(rows)
                    page_records = extract_rows(
                        rows, col_map, page_no, ctx, manual_pdf_name,
                    )
                    if page_records:
                        all_records.extend(page_records)

            processed += 1
            if progress_callback:
                progress_callback(
                    "Processing pages", processed, total, len(all_records),
                )

    finally:
        doc.close()

    # Deduplication
    seen: set[tuple[int, str, str]] = set()
    deduped: list[ExtractedRow] = []
    for record in all_records:
        key = (record.page_no, record.mfg_part_no or "", record.name_of_spare)
        if key not in seen:
            seen.add(key)
            deduped.append(record)

    # Convert to template rows
    template_rows = [to_template_row(record) for record in deduped]
    return template_rows
