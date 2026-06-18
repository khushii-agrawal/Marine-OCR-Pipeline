import os
import re
import sys
from pathlib import Path

import cv2
import fitz
import openpyxl


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.append(str((SCRIPT_DIR / "local_engine").resolve()))

from ocr_extractor import OCRExtractor
from pdf_converter import pdf_page_to_image


PDF_PATH = PROJECT_ROOT / "Test" / "Test 19" / "Life boat spares 1 (1).pdf"
TEMPLATE_PATH = PROJECT_ROOT / "template" / "Spares_Capture_Template_Ver12 2.xlsm"
OUTPUT_PATH = Path(os.environ.get(
    "TEST19_OUTPUT_PATH",
    PROJECT_ROOT / "output" / "Test19_Life_boat_spares_pages3_39.xlsm",
))

COMPONENT = "Life Boat Spares"
SUB_COMPONENT = "BUKH Diesel Engine Spare Parts"
MANUFACTURER = "BUKH"
MODEL = "DV 36/48"
MANUAL_PDF_NAME = "Life boat spares 1 (1).pdf"
DEFAULT_UOM = "Pcs"

START_PAGE = int(os.environ.get("TEST19_START_PAGE", "3"))
END_PAGE = int(os.environ.get("TEST19_END_PAGE", "39"))

ALPHA_CODE_RE = re.compile(r"[0O]\d{2}[A-Z]\d{2,4}(?:-\d)?|\d{3}[A-Z]\d{2,4}(?:-\d)?", re.IGNORECASE)
NUMERIC_CODE_RE = re.compile(r"\d{8}")
HEADER_RE = re.compile(
    r"\b(?:POS|PART|QTY|DESCRIPTION|BESKRIVELSE|BENENNUNG|DV\s*\d|PAGE|SIDE)\b",
    re.IGNORECASE,
)
NOTE_RE = re.compile(
    r"^(?:UP TO|FROM|BIS|VON|JUSQU|DE\s+\d|VED BESTILLING|ORDER|SPARE PART|ERSATZ|NOTE)\b",
    re.IGNORECASE,
)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).strip(" -|")


def titleish(value):
    text = clean_text(value)
    if not text:
        return ""
    fixes = {
        "cpl.": "cpl.",
        "cpl": "cpl.",
        "o-ring": "O-ring",
        "bsp": "BSP",
        "rg": "RG",
        "m6": "M6",
        "m8": "M8",
        "m10": "M10",
        "m14": "M14",
        "dv": "DV",
    }
    words = []
    for token in text.split():
        key = token.lower().strip(",.;")
        if key in fixes:
            replacement = fixes[key]
            suffix = token[len(token.rstrip(",.;")):]
            words.append(replacement + suffix if not replacement.endswith(suffix) else replacement)
        elif token.isupper() and len(token) <= 4:
            words.append(token)
        else:
            words.append(token[:1].upper() + token[1:].lower())
    return clean_text(" ".join(words))


def normalize_code(value):
    text = clean_text(value).upper()
    text = text.replace(" ", "")
    text = text.replace("O", "0")
    text = text.replace("I", "1")
    return text.strip(".,;:()[]")


def split_part_area(value):
    compact = normalize_code(value)
    best_match = None
    for match in ALPHA_CODE_RE.finditer(compact):
        candidate = match.group(0)
        # Prefer codes that start after a glued item number, e.g. 14500C2416 -> 500C2416.
        if best_match is None or match.start() > best_match.start():
            best_match = match
    if best_match is None:
        for match in NUMERIC_CODE_RE.finditer(compact):
            if best_match is None or match.start() > best_match.start():
                best_match = match

    if best_match is None:
        pos_match = re.search(r"\b\d{1,3}\b", clean_text(value))
        return (pos_match.group(0) if pos_match else ""), ""

    part = best_match.group(0)
    prefix = compact[:best_match.start()]
    pos = ""
    pos_match = re.search(r"(\d{1,3})$", prefix)
    if pos_match:
        pos = pos_match.group(1)
        if pos.startswith("0") and len(pos) > 1:
            pos = pos.lstrip("0")
    return pos, part


def ocr_rotated_items(pdf_path, page_no, extractor, dpi=200, rotation="ccw"):
    image = pdf_page_to_image(str(pdf_path), page_no - 1, dpi=dpi)
    if rotation == "ccw":
        image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    elif rotation == "cw":
        image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
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


def group_rows(items, y_tolerance=8):
    filtered = [item for item in items if 0.04 <= item["rel_y"] <= 0.97]
    rows = []
    current = []
    current_y = None
    for item in sorted(filtered, key=lambda item: (item["cy"], item["cx"])):
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


def text_in(row, left, right):
    return clean_text(" ".join(
        item["text"]
        for item in sorted(row, key=lambda item: (item["rel_y"], item["rel_x"]))
        if left <= item["rel_x"] <= right
    ))


def row_text(row):
    return clean_text(" ".join(item["text"] for item in sorted(row, key=lambda item: item["rel_x"])))


def detect_model(items):
    text = " ".join(item["text"] for item in items).upper()
    has_dv10 = "DV10" in text.replace(" ", "") or "DV 10/20" in text
    has_dv36 = "DV36" in text.replace(" ", "") or "DV 36/48" in text
    if has_dv10 and has_dv36:
        return "DV 10/20; DV 36/48"
    if has_dv10:
        return "DV 10/20"
    return MODEL


def part_area(row):
    candidates = [
        item for item in row
        if 0.515 <= item["rel_x"] <= 0.615
    ]
    if not candidates:
        return ""
    return clean_text(" ".join(item["text"] for item in sorted(candidates, key=lambda item: item["rel_x"])))


def explicit_pos(row):
    candidates = [
        item["text"] for item in row
        if 0.50 <= item["rel_x"] <= 0.56 and re.fullmatch(r"\d{1,3}\)?", clean_text(item["text"]))
    ]
    if not candidates:
        return ""
    return clean_text(candidates[0]).rstrip(")")


def english_name(row):
    english = text_in(row, 0.705, 0.795)
    english = english.replace("crption", "").strip()
    if not english or len(re.sub(r"[^A-Za-z]", "", english)) < 2:
        english = text_in(row, 0.615, 0.705)
    if not english or len(re.sub(r"[^A-Za-z]", "", english)) < 2:
        english = text_in(row, 0.795, 0.865)
    english = clean_text(english)
    english = re.sub(r"^\d+\)?\s*", "", english)
    english = re.sub(r"\b(?:ME|SME|QTY|NO\.?)\b", "", english, flags=re.IGNORECASE)
    return clean_text(english)


def is_header_or_note(line):
    text = row_text(line)
    if not text:
        return True
    if HEADER_RE.search(text) and not re.search(r"\b\d{3}[A-Z]\d{3,4}\b", normalize_code(text)):
        return True
    name = english_name(line)
    return bool(name and NOTE_RE.search(name))


def looks_like_data_name(name):
    if not name or len(re.sub(r"[^A-Za-z]", "", name)) < 2:
        return False
    if re.fullmatch(r"(?:description|descriptior|beschreibung|benennung|beskrivelse)", name, re.IGNORECASE):
        return False
    if NOTE_RE.search(name):
        return False
    if re.fullmatch(r"[\d\s.,/()-]+", name):
        return False
    return True


def append_name(current, more):
    more = clean_text(more)
    if not current or not more or not looks_like_data_name(more):
        return
    if more.lower() in current["name"].lower():
        return
    current["name"] = clean_text(f"{current['name']} {more}")


def extract_page(items, page_no):
    model = detect_model(items)
    records = []
    current = None

    for row in group_rows(items):
        if is_header_or_note(row):
            continue

        area = part_area(row)
        pos_from_area, part = split_part_area(area)
        pos = pos_from_area or explicit_pos(row)
        name = english_name(row)

        has_data_anchor = bool(part or (pos and looks_like_data_name(name)))
        if has_data_anchor:
            if current and looks_like_data_name(current["name"]):
                records.append(current)
            current = {
                "sub_component": SUB_COMPONENT,
                "model": model,
                "pos": pos,
                "part": part,
                "name": titleish(name or "Spare part"),
                "page": page_no,
            }
            continue

        if current and looks_like_data_name(name):
            append_name(current, titleish(name))
            continue

        # Some catalogue rows have a missing/unclear part number and position.
        # Keep them as separate rows when they look like standalone spare names.
        if looks_like_data_name(name) and 0.60 <= min((item["rel_x"] for item in row), default=1.0) <= 0.75:
            if current and looks_like_data_name(current["name"]):
                records.append(current)
            current = {
                "sub_component": SUB_COMPONENT,
                "model": model,
                "pos": "",
                "part": "",
                "name": titleish(name),
                "page": page_no,
            }

    if current and looks_like_data_name(current["name"]):
        records.append(current)

    # De-duplicate accidental OCR duplicates while preserving rows without part numbers.
    deduped = []
    seen = set()
    for record in records:
        key = (record["page"], record["pos"], record["part"], record["name"].lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def short_code_from_row(row):
    area = text_in(row, 0.385, 0.455)
    compact = normalize_code(area)
    match = re.search(r"(?:\d{3}[A-Z]\d{2,4}|\d{4,6})", compact)
    return match.group(0) if match else ""


def extract_short_code_page(items, page_no):
    model = detect_model(items)
    records = []
    seen = set()
    for row in group_rows(items, y_tolerance=10):
        text = row_text(row)
        if HEADER_RE.search(text) or NOTE_RE.search(text):
            continue
        part = short_code_from_row(row)
        if not part:
            continue
        name = text_in(row, 0.205, 0.30)
        if not looks_like_data_name(name):
            name = text_in(row, 0.305, 0.38)
        if not looks_like_data_name(name):
            name = text_in(row, 0.145, 0.205)
        if not looks_like_data_name(name):
            name = text_in(row, 0.055, 0.135)
        if not looks_like_data_name(name):
            continue
        record = {
            "sub_component": SUB_COMPONENT,
            "model": model,
            "pos": "",
            "part": part,
            "name": titleish(name),
            "page": page_no,
        }
        key = (record["part"], record["name"].lower())
        if key in seen:
            continue
        seen.add(key)
        records.append(record)
    return records


def to_template_row(record):
    details = ""
    return [
        COMPONENT,
        record["sub_component"],
        MANUFACTURER,
        record["model"],
        record["name"],
        record["part"],
        "",
        record["pos"],
        "",
        "",
        "",
        details,
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
    end_page = min(END_PAGE, len(doc))
    pages = list(range(START_PAGE, end_page + 1))
    doc.close()

    extractor = OCRExtractor()
    records = []
    counts_by_page = {}
    for page_no in pages:
        items = ocr_rotated_items(PDF_PATH, page_no, extractor)
        page_records = extract_page(items, page_no)
        retry_items = ocr_rotated_items(PDF_PATH, page_no, extractor, dpi=300, rotation="ccw") if len(page_records) < 3 else []
        retry_records = extract_page(retry_items, page_no) if retry_items else []
        cw_items = ocr_rotated_items(PDF_PATH, page_no, extractor, dpi=300, rotation="cw") if max(len(page_records), len(retry_records)) < 3 else []
        cw_records = extract_short_code_page(cw_items, page_no) if cw_items else []
        page_records = max([page_records, retry_records, cw_records], key=len)
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
