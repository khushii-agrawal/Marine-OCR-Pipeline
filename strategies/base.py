"""
strategies/base.py – Abstract base class and shared utilities for layout strategies.

Phase 2 established the LayoutExtractor ABC and data classes.
Phase 3 adds shared helpers used across all concrete strategy implementations.
"""

from __future__ import annotations

import json
import re
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

import fitz
import numpy as np

# ---------------------------------------------------------------------------
# Ensure the local_engine package is importable from strategies/
# ---------------------------------------------------------------------------
_LOCAL_ENGINE = Path(__file__).resolve().parents[1] / "scripts" / "local_engine"
if str(_LOCAL_ENGINE) not in sys.path:
    sys.path.insert(0, str(_LOCAL_ENGINE))

from ocr_extractor import OCRExtractor  # noqa: E402
from pdf_converter import pdf_page_to_image  # noqa: E402


# ═══════════════════════ Data classes ═══════════════════════


@dataclass(frozen=True)
class Region:
    page_no: int
    bbox: tuple[float, float, float, float]
    region_type: str = "unknown"
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawTable:
    rows: list[dict[str, Any]]
    columns: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PartRecord:
    part_no: str
    description: str
    component: str
    qty: str = ""
    unit: str = ""
    drawing_ref: str = ""
    sub_component: str = ""
    manufacturer: str = ""
    model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ═══════════════════════ Abstract base ═══════════════════════


class LayoutExtractor(ABC):
    @abstractmethod
    def detect_regions(self, page_image) -> list[Region]:
        """Locate candidate extraction regions on a rendered page image."""

    @abstractmethod
    def extract_table(self, region: Region) -> RawTable:
        """Extract a raw table-like structure from a detected region."""

    @abstractmethod
    def map_to_schema(self, raw_table: RawTable, manufacturer_profile: dict[str, Any]) -> list[PartRecord]:
        """Map raw extracted rows into canonical PartRecord values."""

    @abstractmethod
    def confidence(self, record: PartRecord) -> float:
        """Return a per-record confidence score from 0.0 to 1.0."""


# ═══════════════════════ Profile helpers ═══════════════════════


def load_profile(profile_path: str | Path) -> dict[str, Any]:
    """Load and return a manufacturer profile JSON file."""
    path = Path(profile_path)
    if not path.exists():
        raise FileNotFoundError(f"Profile not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def column_synonyms(profile: dict[str, Any]) -> dict[str, list[str]]:
    """
    Extract the column_assumptions mapping from a profile.

    Returns a dict like:
        {
            "part_no": ["PART NO", "ORDER NO", ...],
            "description": ["DESCRIPTION", "DESIGNATION", ...],
            ...
        }
    """
    assumptions = profile.get("column_assumptions", {})
    mapping = {}
    for key, aliases in assumptions.items():
        # Strip the _aliases suffix to get canonical field name
        canonical = re.sub(r"_aliases$", "", key)
        mapping[canonical] = aliases
    return mapping


def find_document_profile(profile: dict[str, Any], fixture_id: str = "", pdf_name: str = "") -> dict[str, Any]:
    """Find the matching document_profile entry by fixture_id or pdf name."""
    for doc_profile in profile.get("document_profiles", []):
        if fixture_id and doc_profile.get("fixture_id") == fixture_id:
            return doc_profile
        if pdf_name and pdf_name in doc_profile.get("manual_pdf_name", ""):
            return doc_profile
    # Return first entry as fallback
    profiles = profile.get("document_profiles", [{}])
    return profiles[0] if profiles else {}


# ═══════════════════════ Text utilities ═══════════════════════


def clean_text(text: Any) -> str:
    """Normalize whitespace, strip boundary chars."""
    text = re.sub(r"\s+", " ", str(text or "").strip())
    return text.strip(" -|")


def titleish(text: str) -> str:
    """Convert text to title-like casing preserving short uppercase tokens."""
    text = clean_text(text)
    if not text:
        return ""
    words = []
    for token in text.split():
        if token.isupper() and len(token) <= 4:
            words.append(token)
        else:
            words.append(token[:1].upper() + token[1:].lower())
    return clean_text(" ".join(words))


# ═══════════════════════ OCR / PDF helpers ═══════════════════════


_SHARED_EXTRACTOR: Optional[OCRExtractor] = None


def get_ocr_extractor() -> OCRExtractor:
    """Lazily initialise and return a shared OCRExtractor instance."""
    global _SHARED_EXTRACTOR
    if _SHARED_EXTRACTOR is None:
        _SHARED_EXTRACTOR = OCRExtractor()
    return _SHARED_EXTRACTOR


def ocr_items(pdf_path: str | Path, page_idx: int, extractor: Optional[OCRExtractor] = None, dpi: int = 200) -> list[dict]:
    """
    Run PaddleOCR on a PDF page and return a list of item dicts.

    Each item contains:
        text, x0, y0, x1, y1, cx, cy, rel_x, rel_y, conf
    """
    if extractor is None:
        extractor = get_ocr_extractor()
    image = pdf_page_to_image(str(pdf_path), page_idx, dpi=dpi)
    height, width = image.shape[:2]
    items: list[dict] = []
    for box, (text, conf) in extractor.extract_text(image):
        text = clean_text(text)
        if not text:
            continue
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        items.append({
            "text": text,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "cx": (x0 + x1) / 2,
            "cy": (y0 + y1) / 2,
            "rel_x": ((x0 + x1) / 2) / width,
            "rel_y": ((y0 + y1) / 2) / height,
            "conf": conf,
        })
    return items


def embedded_items(page: fitz.Page) -> list[dict]:
    """
    Extract selectable text words from a PyMuPDF page as item dicts.

    Each item contains the same keys as ocr_items output.
    """
    width = page.rect.width
    height = page.rect.height
    items: list[dict] = []
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
            "conf": 1.0,  # embedded text is assumed high-confidence
        })
    return items


def get_page_items(pdf_path: str | Path, page_no: int, extractor: Optional[OCRExtractor] = None, dpi: int = 200, min_embedded: int = 8) -> Tuple[list[dict], str]:
    """
    Get items for a page: try embedded text first, fall back to OCR.

    Args:
        pdf_path: Path to PDF.
        page_no: 1-based page number.
        extractor: Optional OCRExtractor instance.
        dpi: DPI for OCR rendering.
        min_embedded: Minimum embedded items to avoid OCR fallback.

    Returns:
        (items, mode) where mode is "embedded" or "ocr".
    """
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_no - 1]
        items = embedded_items(page)
        if len(items) >= min_embedded:
            return items, "embedded"
    finally:
        doc.close()
    return ocr_items(pdf_path, page_no - 1, extractor, dpi), "ocr"


# ═══════════════════════ Row-grouping utilities ═══════════════════════


def group_rows(items: list[dict], y_tolerance: float = 15, key: str = "cy") -> list[list[dict]]:
    """
    Group items into rows based on vertical proximity.

    Args:
        items: List of item dicts (must have 'cy' and 'cx' keys).
        y_tolerance: Max y-distance to consider items on the same row.
        key: The y-coordinate key to group on ('cy', 'rel_y', 'y0', etc.).

    Returns:
        List of rows, each row sorted left-to-right.
    """
    if not items:
        return []
    sort_x = "cx" if key in ("cy", "y0") else "rel_x"
    sorted_items = sorted(items, key=lambda item: (item[key], item[sort_x]))
    rows: list[list[dict]] = []
    current: list[dict] = [sorted_items[0]]
    current_y = sorted_items[0][key]

    for item in sorted_items[1:]:
        if abs(item[key] - current_y) <= y_tolerance:
            current.append(item)
            current_y = (current_y + item[key]) / 2
        else:
            current.sort(key=lambda i: i[sort_x])
            rows.append(current)
            current = [item]
            current_y = item[key]

    if current:
        current.sort(key=lambda i: i[sort_x])
        rows.append(current)
    return rows


def join_items(items: list[dict], left: float = 0.0, right: float = 1.0, key: str = "rel_x") -> str:
    """Join text of items within a horizontal range, sorted left-to-right."""
    values = [
        item["text"]
        for item in sorted(items, key=lambda i: i[key])
        if left <= item[key] <= right
    ]
    return clean_text(" ".join(values))


def line_text(line: list[dict]) -> str:
    """Join a row of items into a single text string."""
    return clean_text(" ".join(item["text"] for item in sorted(line, key=lambda i: i.get("rel_x", i.get("cx", 0)))))


def page_text(items: list[dict], y_tolerance: float = 15, key: str = "cy") -> str:
    """Join all items into page-level text, grouped by rows."""
    return "\n".join(line_text(row) for row in group_rows(items, y_tolerance, key))


# ═══════════════════════ Column-matching utilities ═══════════════════════


def match_column_header(header_text: str, synonyms: dict[str, list[str]]) -> Optional[str]:
    """
    Match a detected column header to a canonical field name using synonyms.

    Args:
        header_text: The OCR'd header text.
        synonyms: Output of column_synonyms() – canonical_name → [alias, ...].

    Returns:
        The canonical field name (e.g. 'part_no') or None if no match.
    """
    normalized = clean_text(header_text).upper()
    for canonical, aliases in synonyms.items():
        for alias in aliases:
            if alias.upper() in normalized or normalized in alias.upper():
                return canonical
    return None


# ═══════════════════════ Confidence scoring ═══════════════════════


def avg_confidence(items: list[dict]) -> float:
    """Compute average OCR confidence from a list of items."""
    confs = [item.get("conf", 0.0) for item in items if "conf" in item]
    return sum(confs) / len(confs) if confs else 0.0


def record_confidence(record: PartRecord) -> float:
    """Compute confidence for a PartRecord from its metadata."""
    confs = record.metadata.get("cell_confidences", [])
    if not confs:
        return record.metadata.get("avg_confidence", 0.5)
    return sum(confs) / len(confs)
