import os
import re
import sys
from pathlib import Path

import fitz
import openpyxl


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.append(str((SCRIPT_DIR / "local_engine").resolve()))

from ocr_extractor import OCRExtractor
from pdf_converter import pdf_page_to_image


PDF_PATH = PROJECT_ROOT / "Test" / "Test 16" / "5H _blowUpDiagram (1).pdf"
TEMPLATE_PATH = PROJECT_ROOT / "template" / "Spares_Capture_Template_Ver12 2.xlsm"
OUTPUT_PATH = Path(os.environ.get(
    "TEST16_OUTPUT_PATH",
    PROJECT_ROOT / "output" / "Test16_Carrier_5H_extracted.xlsm",
))

COMPONENT = "Compressor/Condensing Units"
SUB_COMPONENT_DEFAULT = "Carrier 5H Freon"
MANUFACTURER = "CARRIER"
MODEL = "5H Freon"
MANUAL_PDF_NAME = "5H _blowUpDiagram (1).pdf"
DEFAULT_UOM = "Pcs"

START_PAGE = int(os.environ.get("TEST16_START_PAGE", "1"))
END_PAGE = int(os.environ.get("TEST16_END_PAGE", "0"))

PART_RE = re.compile(r"\b(?:[A-Z0-9]{2,}[-][A-Z0-9]+|\d{2}[A-Z]{2}\d+[-]\d+|[A-Z]{2}\d+[A-Z]*[-]\d+)\b")
SECTION_RE = re.compile(r"\b([A-Z][A-Z /&().'-]{4,}\s+GROUP(?:\s+\(CONT'D\.\))?)\b")


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).strip(" -|")


def titleish(value):
    text = clean_text(value)
    if not text:
        return ""
    fixes = {
        "ldc": "LDC",
        "o.d.": "O.D.",
        "i.d.": "I.D.",
        "nema": "NEMA",
        "hp": "HP",
    }
    words = []
    for token in text.split():
        key = token.lower()
        if key in fixes:
            words.append(fixes[key])
        elif token.isupper() and len(token) <= 4:
            words.append(token)
        else:
            words.append(token[:1].upper() + token[1:].lower())
    return clean_text(" ".join(words))


def normalize_part(value):
    text = clean_text(value).upper()
    text = text.replace("5H4O", "5H40").replace("5H12O", "5H120")
    text = text.replace("O9RH", "09RH").replace("O9RA", "09RA")
    text = text.replace("EP29VC", "EP29VC").replace("EP29ZC", "EP29ZC")
    return text


def ocr_items(pdf_path, page_no, extractor):
    image = pdf_page_to_image(str(pdf_path), page_no - 1, dpi=180)
    height, width = image.shape[:2]
    items = []
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
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
            "cx": (x0 + x1) / 2,
            "cy": (y0 + y1) / 2,
            "rel_x": ((x0 + x1) / 2) / width,
            "rel_y": ((y0 + y1) / 2) / height,
            "conf": conf,
        })
    return items


def group_rows(items, y_tolerance=14):
    rows = []
    current = []
    current_y = None
    for item in sorted(items, key=lambda item: (item["cy"], item["cx"])):
        if current_y is None or abs(item["cy"] - current_y) <= y_tolerance:
            current.append(item)
            current_y = item["cy"] if current_y is None else (current_y + item["cy"]) / 2
        else:
            current.sort(key=lambda item: item["cx"])
            rows.append(current)
            current = [item]
            current_y = item["cy"]
    if current:
        current.sort(key=lambda item: item["cx"])
        rows.append(current)
    return rows


def join_items(items, left=0.0, right=1.0):
    values = [
        item["text"]
        for item in sorted(items, key=lambda item: item["rel_x"])
        if left <= item["rel_x"] <= right
    ]
    return clean_text(" ".join(values))


def page_text(items):
    return "\n".join(join_items(row) for row in group_rows(items))


def detect_section(row_text, current):
    match = SECTION_RE.search(row_text.upper())
    if not match:
        return current
    section = titleish(match.group(1).replace("(CONT'D.)", "").replace("CONT'D.", ""))
    return section or current


def item_no_from_row(row):
    value = join_items(row, 0.04, 0.115)
    match = re.search(r"\b(?:NI|\d{1,3}(?:/\d{1,3})?)\b", value, re.IGNORECASE)
    return match.group(0).upper() if match else ""


def first_part_from_row(row):
    text = join_items(row, 0.50, 0.68)
    match = PART_RE.search(text.upper())
    if not match:
        match = PART_RE.search(join_items(row, 0.0, 1.0).upper())
    return normalize_part(match.group(0)) if match else ""


def usage_from_row(row):
    text = join_items(row, 0.66, 0.88)
    hits = re.findall(r"\b1\b", text)
    return f"Unit usage count: {len(hits)}" if hits else ""


def extract_page(items, page_no):
    text = page_text(items).upper()
    history_rows = history_chart_rows(items, page_no)
    if history_rows:
        return history_rows

    if "PART NAME" not in text and "REPLACEMENT" not in text:
        return []

    records = []
    current_section = SUB_COMPONENT_DEFAULT
    pending_item = ""
    pending_name = ""

    for row in group_rows([item for item in items if 0.10 <= item["rel_y"] <= 0.94]):
        line = join_items(row)
        upper = line.upper()
        current_section = detect_section(line, current_section)
        if re.search(r"\b(?:ITEM|PART NAME|REPLACEMENT|UNIT USAGE|PAGE|LITHO)\b", upper):
            continue

        item_no = item_no_from_row(row)
        name = titleish(join_items(row, 0.12, 0.48))
        ldc = join_items(row, 0.46, 0.53).upper()
        part = first_part_from_row(row)
        details = usage_from_row(row)

        if item_no and name and not part:
            pending_item = item_no
            pending_name = name
            continue

        if part:
            final_name = name or pending_name
            final_item = item_no or pending_item
            if not final_name:
                final_name = "Replacement Part"
            if ldc and re.fullmatch(r"[A-Z]{1,3}", ldc):
                details = clean_text(f"LDC: {ldc}; {details}".strip("; "))
            records.append({
                "sub_component": current_section,
                "pos": final_item,
                "name": final_name,
                "part": part,
                "details": details,
                "page": page_no,
            })
            if item_no:
                pending_item = item_no
                pending_name = name
            continue

        if item_no and name:
            records.append({
                "sub_component": current_section,
                "pos": item_no,
                "name": name,
                "part": "",
                "details": clean_text(f"LDC: {ldc}" if ldc else ""),
                "page": page_no,
            })

    return records


def history_chart_rows(items, page_no):
    text = page_text(items).upper()
    if "PACKAGE HISTORY CHART" not in text or "PACKAGE CONSISTS" not in text:
        return []

    records = []
    candidate_items = [
        item for item in sorted(items, key=lambda item: (item["rel_y"], item["rel_x"]))
        if 0.39 <= item["rel_y"] <= 0.48
    ]
    for item in candidate_items:
        match = PART_RE.search(item["text"].upper())
        if not match:
            continue
        part = normalize_part(match.group(0))
        tail = clean_text(item["text"][match.end():])
        if not tail:
            same_line = [
                other for other in candidate_items
                if other is not item
                and abs(other["rel_y"] - item["rel_y"]) <= 0.012
                and other["rel_x"] > item["rel_x"]
            ]
            tail = join_items(same_line, item["rel_x"], min(item["rel_x"] + 0.22, 1.0))
        name = titleish(tail or "Package Component")
        records.append({
            "sub_component": "Service Oil Pump Package History Chart",
            "pos": str(len(records) + 1),
            "name": name,
            "part": part,
            "details": "Package consists of",
            "page": page_no,
        })
    return records


def to_template_row(record):
    return [
        COMPONENT,
        record["sub_component"],
        MANUFACTURER,
        MODEL,
        record["name"],
        record["part"],
        "",
        record["pos"],
        "",
        "",
        "",
        record["details"],
        record["page"],
        MANUAL_PDF_NAME,
        "",
        DEFAULT_UOM,
        "",
        "",
        "Yes",
        "",
        "",
    ]


def write_workbook(rows):
    wb = openpyxl.load_workbook(TEMPLATE_PATH, keep_vba=True)
    ws = wb.active
    try:
        for row_idx, row in enumerate(rows, start=3):
            for col_idx, value in enumerate(row, start=1):
                ws.cell(row_idx, col_idx).value = value
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        candidates = [
            OUTPUT_PATH,
            OUTPUT_PATH.with_name(f"{OUTPUT_PATH.stem}_fallback{OUTPUT_PATH.suffix}"),
            PROJECT_ROOT / OUTPUT_PATH.name,
        ]
        last_error = None
        for candidate in candidates:
            try:
                wb.save(candidate)
                return candidate
            except PermissionError as exc:
                last_error = exc
                print(f"Could not write {candidate}: {exc}")
        raise last_error
    finally:
        wb.close()


def main():
    doc = fitz.open(PDF_PATH)
    end_page = END_PAGE or len(doc)
    pages = list(range(START_PAGE, min(end_page, len(doc)) + 1))
    doc.close()

    extractor = OCRExtractor()
    records = []
    counts_by_page = {}
    for page_no in pages:
        items = ocr_items(PDF_PATH, page_no, extractor)
        page_records = extract_page(items, page_no)
        counts_by_page[page_no] = len(page_records)
        records.extend(page_records)
        print(f"Page {page_no}: {len(page_records)} rows")

    rows = [to_template_row(record) for record in records]
    saved_path = write_workbook(rows)
    print(f"Rows written: {len(rows)}")
    print("Rows by page:", counts_by_page)
    print("Zero-row pages:", [page for page, count in counts_by_page.items() if count == 0])
    print(f"Output: {saved_path}")


if __name__ == "__main__":
    main()
