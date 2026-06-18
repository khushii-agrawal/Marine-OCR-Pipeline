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

PDF_PATH = PROJECT_ROOT / "Test" / "Test 17" / "Emergency diesel engine 1 1.pdf"
TEMPLATE_PATH = PROJECT_ROOT / "template" / "Spares_Capture_Template_Ver12 2.xlsm"
OUTPUT_PATH = Path(os.environ.get(
    "TEST17_OUTPUT_PATH",
    PROJECT_ROOT / "output" / "Test17_Emergency_diesel_engine_extracted.xlsm",
))

COMPONENT = "Emergency diesel engine"
MANUFACTURER = "MAN"
MODEL = "D 2840 LE"
MANUAL_PDF_NAME = "Emergency diesel engine 1 1.pdf"
DEFAULT_UOM = "Pcs"

START_PAGE = int(os.environ.get("TEST17_START_PAGE", "9"))
END_PAGE = int(os.environ.get("TEST17_END_PAGE", "181"))

PART_RE = re.compile(r"\b(?:\d{2}\.\d{5}-\d{3,4}|\d{2}\s+\d{5}-\d{3,4}|\d\.\d{5}-\d{3,4}|\d{7}-\d{3,4})\b")

def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).strip(" -|")

def titleish(value):
    text = clean_text(value)
    if not text:
        return ""
    words = []
    for token in text.split():
        if token.isupper() and len(token) <= 4:
            words.append(token)
        else:
            words.append(token[:1].upper() + token[1:].lower())
    return clean_text(" ".join(words))

def normalize_part(value):
    text = clean_text(value)
    match = PART_RE.search(text)
    if not match:
        return ""
    part = re.sub(r"\s+", " ", match.group(0))
    if re.fullmatch(r"\d{2}\s+\d{5}-\d{3,4}", part):
        part = part.replace(" ", ".")
    elif re.fullmatch(r"\d{7}-\d{3,4}", part):
        part = f"{part[:2]}.{part[2:]}"
    elif re.fullmatch(r"\d\.\d{5}-\d{3,4}", part):
        part = f"5{part}"
    return part

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
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
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

def header_from_row(row):
    text_all = join_items(row)
    if PART_RE.search(text_all):
        return ""
    if re.search(r"\b(?:GROUPE|GRUPPE|PLANCHE|TAFEL|FIGS?\.?|ITEMS?\s+NO|ONLY\s+FOR|SEE\s+GROUP)\b", text_all, re.IGNORECASE):
        return ""

    english = join_items(row, 0.36, 0.53)
    english = re.sub(r"\bD\s*2840\b", " ", english, flags=re.IGNORECASE)
    english = re.sub(r"\b2840\b", " ", english)
    english = re.sub(r"\bLE\b", " ", english)
    english = clean_text(english.replace("|", " "))
    english = re.sub(r"\bCr[ae]nkease\b", "Crankcase", english, flags=re.IGNORECASE)
    if not english:
        return ""
    if re.search(r"\b(?:GUPP|TAF|GRUPO|LAMIN|FOLD|VISCOUS\s+AIR\s+CLEANER|ALL\s+GASKETS|INCLUDED\s+IN\s+SET|NO-?COOLED\s+EXHAUST|WATER-?COOLED\s+EXHAUST)\b", english, re.IGNORECASE):
        return ""
    if re.search(r"\d", english):
        return ""

    lower = english.lower()
    if lower in {"or", "of", "ou", "oder", "ovvero"}:
        return ""
    if re.fullmatch(r"[\d\s.,x/()-]+", english):
        return ""
    if re.search(r"\b(?:or|of|ou)\b$", lower):
        english = re.sub(r"\b(?:or|of|ou)\b$", "", english, flags=re.IGNORECASE)
    if len(re.sub(r"[^A-Za-z]", "", english)) < 4:
        return ""
    return titleish(english)


def header_candidates(items):
    candidates = []
    for row in group_rows(items, y_tolerance=12):
        rel_y = sum(item["rel_y"] for item in row) / len(row)
        if rel_y < 0.075:
            continue
        y = sum(item["cy"] for item in row) / len(row)
        row_text = join_items(row)
        has_model_title = bool(re.search(r"\b(?:D\s*)?2840\b", row_text, re.IGNORECASE))
        if not has_model_title and any(PART_RE.search(item["text"]) and abs(item["cy"] - y) <= 160 for item in items):
            continue
        title = header_from_row(row)
        if title:
            candidates.append((y, title))
    return candidates


def latest_header_before(headers, cy, fallback):
    current = fallback
    for header_y, title in headers:
        if header_y < cy:
            current = title
        else:
            break
    return current


def row_band(items, anchor, prev_anchor, next_anchor):
    if prev_anchor:
        top = (prev_anchor["cy"] + anchor["cy"]) / 2
    else:
        top = max(anchor["cy"] - 32, 0)
    if next_anchor:
        bottom = (anchor["cy"] + next_anchor["cy"]) / 2
    else:
        bottom = anchor["cy"] + 42
    if bottom - top < 12:
        top = anchor["cy"] - 8
        bottom = anchor["cy"] + 18
    return [item for item in items if top <= item["cy"] < bottom]


def text_in_band(band, left, right):
    values = []
    for item in sorted(band, key=lambda item: (item["cy"], item["cx"])):
        if not (left <= item["rel_x"] <= right):
            continue
        value = clean_text(item["text"].replace("|", " "))
        if not value or PART_RE.search(value):
            continue
        values.append(value)
    return clean_text(" ".join(values))


def extract_position(band, anchor):
    nearby = [
        item for item in band
        if 0.045 <= item["rel_x"] <= 0.105
        and abs(item["cy"] - anchor["cy"]) <= 18
        and not PART_RE.search(item["text"])
    ]
    text = " ".join(item["text"] for item in sorted(nearby, key=lambda item: item["cx"]))
    match = re.search(r"\b\d{1,3}\b", text)
    return match.group(0) if match else ""


def extract_name(band, current_subcomponent):
    name = text_in_band(band, 0.38, 0.52)
    if not name or len(re.sub(r"[^A-Za-z]", "", name)) < 3:
        name = text_in_band(band, 0.37, 0.61)
    name = re.sub(r"^\b(?:or|of|ou)\b\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\b(?:oder|ovvero)\b", "", name, flags=re.IGNORECASE)
    name = name.replace("Screwconnection", "Screw connection")
    name = re.sub(r"\b(Screw connection)\s+Gasket.*$", r"\1", name, flags=re.IGNORECASE)
    name = clean_text(name)
    if re.match(r"^(?:Nos?\.?\s*)?\d+\s*-\s*\d+$", name, re.IGNORECASE):
        name = f"{current_subcomponent}, with parts fig. {name}"
    return titleish(name)


def extract_details(band):
    qty = text_in_band(band, 0.325, 0.37)
    qty = re.sub(r"\b(?:D\s*2840|LE)\b", "", qty, flags=re.IGNORECASE)
    qty = clean_text(qty)
    if not qty:
        return ""
    if re.search(r"\d", qty) and len(qty) <= 20:
        return f"Qty/details: {qty}"
    return ""


def part_anchors(items):
    anchors = []
    for item in items:
        part = normalize_part(item["text"])
        if not part:
            continue
        if not (0.095 <= item["rel_x"] <= 0.19):
            continue
        if item["rel_y"] < 0.08 or item["rel_y"] > 0.96:
            continue
        anchored = dict(item)
        anchored["part"] = part
        anchors.append(anchored)
    anchors.sort(key=lambda item: (item["cy"], item["cx"]))
    return anchors


def extract_page(items, page_no, previous_subcomponent):
    records = []
    current_subcomponent = previous_subcomponent
    headers = header_candidates(items)
    anchors = part_anchors(items)

    for idx, anchor in enumerate(anchors):
        current_subcomponent = latest_header_before(headers, anchor["cy"], current_subcomponent)
        band = row_band(
            items,
            anchor,
            anchors[idx - 1] if idx else None,
            anchors[idx + 1] if idx + 1 < len(anchors) else None,
        )
        name = extract_name(band, current_subcomponent)
        if not name:
            continue
        records.append({
            "sub_component": current_subcomponent,
            "pos": extract_position(band, anchor),
            "name": name,
            "part": anchor["part"],
            "details": extract_details(band),
            "page": page_no,
        })

    return records, current_subcomponent

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
    current_subcomponent = "Emergency Diesel Engine"
    
    for page_no in pages:
        items = ocr_items(PDF_PATH, page_no, extractor)
        page_records, current_subcomponent = extract_page(items, page_no, current_subcomponent)
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
