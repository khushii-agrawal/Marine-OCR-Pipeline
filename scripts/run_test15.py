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


PDF_PATH = PROJECT_ROOT / "Test" / "Test 15" / "Extracted pages from Hydraulic winch.pdf"
TEMPLATE_PATH = PROJECT_ROOT / "template" / "Spares_Capture_Template_Ver12 2.xlsm"
OUTPUT_PATH = Path(os.environ.get(
    "TEST15_OUTPUT_PATH",
    PROJECT_ROOT / "output" / "Test15_Hydraulic_Winch_extracted.xlsm",
))

COMPONENT = "Hydraulic Winch"
MANUFACTURER = "HYDROWEGA HOLLAND B.V."
MODEL = "Draghead Winch"
MANUAL_PDF_NAME = "Extracted pages from Hydraulic winch.pdf"
DEFAULT_UOM = "Pcs"

START_PAGE = int(os.environ.get("TEST15_START_PAGE", "1"))
END_PAGE = int(os.environ.get("TEST15_END_PAGE", "0"))

CODE_RE = re.compile(r"\b\d{3}/\d{3}\.\d/\d{4}\b")
DRAWING_RE = re.compile(r"\bH\d{6,}-L[S5]\d{2,3}-\d\b", re.IGNORECASE)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).strip(" -|")


def titleish(value):
    text = clean_text(value)
    fixes = {
        "o-ring": "O-Ring",
        "v-ring": "V-Ring",
        "cil.": "Cil.",
        "bolt": "Bolt",
        "hexagon": "Hexagon",
        "head": "Head",
        "bearing": "Bearing",
        "brake": "Brake",
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


def normalize_ocr_text(value):
    text = clean_text(value)
    text = text.replace("~", " ")
    text = text.replace("7", "", 1) if text.startswith("7") and len(text) > 3 else text
    text = text.replace("0-RIng", "O-Ring").replace("0-RING", "O-Ring")
    text = text.replace("70-Ring", "V-Ring").replace("70-RIng", "V-Ring")
    text = text.replace("boLt", "bolt").replace("boct", "bolt").replace("boiT", "bolt")
    text = text.replace("heaD", "head").replace("hEAD", "head")
    text = text.replace("fLange", "flange").replace("sufport", "support")
    return clean_text(text)


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


def group_rows(items, y_tolerance=16):
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
        normalize_ocr_text(item["text"])
        for item in sorted(items, key=lambda item: item["rel_x"])
        if left <= item["rel_x"] <= right
    ]
    return clean_text(" ".join(value for value in values if value))


def page_text(items):
    return "\n".join(join_items(row) for row in group_rows(items))


def detected_subcomponent(items):
    text = page_text(items)
    for pattern in [
        r"DRAGHEAD\s+WINCH\s+[A-Z0-9 *-]+",
        r"DENOMIN\.\s*[-.:]?\s*([A-Z0-9 /.-]+)",
    ]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1) if match.lastindex else match.group(0)
            return titleish(value)
    return "Hydraulic Winch"


def dimension_or_part(value):
    text = clean_text(value).upper()
    text = text.replace("DIN", " DIN")
    text = re.sub(r"\s+", " ", text)
    return text


def hydroweg_rows(items, page_no):
    text = page_text(items).upper()
    if "DESCRIPTION" not in text and "DRAGHEAD WINCH" not in text:
        return []

    rows = []
    subcomponent = detected_subcomponent(items)
    for line in group_rows([item for item in items if 0.18 <= item["rel_y"] <= 0.96]):
        left_text = join_items(line, 0.04, 0.16)
        match = re.match(r"^(\d{1,3})\D*(\d{0,3})?", left_text)
        if not match:
            continue

        pos = match.group(1)
        qty = match.group(2) or ""
        name = titleish(join_items(line, 0.16, 0.43))
        if not name or len(name) < 2:
            continue
        if re.search(r"\b(?:DESCRIPTION|MATERIAL|DIMENSIONS|NUMBER|SHEETS)\b", name, re.IGNORECASE):
            continue

        material = dimension_or_part(join_items(line, 0.43, 0.58))
        size = dimension_or_part(join_items(line, 0.58, 0.86))
        remarks = join_items(line, 0.86, 0.98)
        part = material if DRAWING_RE.search(material) else ""
        details = f"Qty: {qty}" if qty else ""
        if size:
            details = clean_text(f"{details}; Dimensions/code: {size}".strip("; "))

        rows.append({
            "sub_component": subcomponent,
            "pos": pos,
            "qty": qty,
            "name": name,
            "part": part,
            "material": "" if part else material,
            "size": size,
            "remarks": remarks,
            "details": details,
            "page": page_no,
        })
    return rows


def distinta_rows(items, page_no):
    text = page_text(items).upper()
    if "DISTINTA" not in text and "DENOMINAZIONE" not in text:
        return []

    subcomponent = detected_subcomponent(items)
    code_items = [
        item for item in items
        if CODE_RE.search(item["text"]) and 0.20 <= item["rel_x"] <= 0.45
    ]
    code_items.sort(key=lambda item: item["cy"])
    rows = []
    for idx, code_item in enumerate(code_items):
        prev_y = code_items[idx - 1]["cy"] if idx else code_item["cy"] - 32
        next_y = code_items[idx + 1]["cy"] if idx + 1 < len(code_items) else code_item["cy"] + 32
        top = (prev_y + code_item["cy"]) / 2
        bottom = (code_item["cy"] + next_y) / 2
        band = [item for item in items if top <= item["cy"] < bottom]
        name = titleish(join_items(band, 0.40, 0.66))
        if not name or re.search(r"\b(?:DENOMINAZIONE|NOTE)\b", name, re.IGNORECASE):
            continue
        pos = join_items(band, 0.20, 0.29)
        pos_match = re.search(r"\d{1,3}", pos)
        qty = join_items(band, 0.66, 0.73)
        qty_match = re.search(r"\d+", qty)
        rows.append({
            "sub_component": subcomponent,
            "pos": pos_match.group(0) if pos_match else str(len(rows) + 1),
            "qty": qty_match.group(0) if qty_match else "",
            "name": name,
            "part": CODE_RE.search(code_item["text"]).group(0),
            "material": "",
            "size": "",
            "remarks": "",
            "details": f"Qty: {qty_match.group(0)}" if qty_match else "",
            "page": page_no,
        })
    return rows


def extract_page(items, page_no):
    rows = distinta_rows(items, page_no)
    if rows:
        return rows
    return hydroweg_rows(items, page_no)


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
        record["size"],
        record["material"],
        record["remarks"],
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
