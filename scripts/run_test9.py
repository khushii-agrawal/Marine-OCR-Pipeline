import os
import re
import sys
from pathlib import Path

import fitz
import openpyxl


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.append(str(SCRIPT_DIR / "local_engine"))

from ocr_extractor import OCRExtractor
from pdf_converter import pdf_page_to_image


PDF_PATH = PROJECT_ROOT / "test" / "Test 9" / "13k obp spare - full list rev 4 (2) (1).pdf"
TEMPLATE_PATH = PROJECT_ROOT / "template" / "Spares_Capture_Template_Ver12 2.xlsm"
OUTPUT_PATH = PROJECT_ROOT / "output" / "Test9_OBP_spares_extracted.xlsm"

COMPONENT = "Mooring Winches & Windlass"
MANUAL_PDF_NAME = "13k obp spare - full list rev 4 (2) (1).pdf"
MODEL = "PIL 13K TEU Hudong Shipyard New Builds"
DEFAULT_UOM = "Pcs"

START_PAGE = int(os.environ.get("TEST9_START_PAGE", "1"))
END_PAGE = int(os.environ.get("TEST9_END_PAGE", "0"))

POS_RE = re.compile(r"^(?:\d{1,3}|[A-Z]+-?\d+(?:-\d+)?|[A-Z]-\d-\d+)$", re.IGNORECASE)
NOISE_RE = re.compile(
    r"^(?:NO\.?|NAME|SKETCH|MATERIAL|MATE-|RIAL|SUPPLY|PER|SHIP|DRAWING|PART|"
    r"REMARKS|PAGE|SHIP|BOX|SPARE|WORK|STANDARD|ADDITIONAL|QTY|UNIT|TYPE|"
    r"APARE|PARTS|LIST|DESCRIPTION|COMPONENT|S/N|SYMBOL)$",
    re.IGNORECASE,
)


def clean_text(text):
    text = re.sub(r"\s+", " ", str(text or "").strip())
    text = text.replace("|", "\\")
    return text.strip(" -")


def titleish(text):
    text = clean_text(text)
    if not text:
        return ""
    fixes = {
        "o-ring": "O-Ring",
        "o ring": "O-Ring",
        "vr": "VR",
        "nt": "NT",
        "mce": "MCE",
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


def extracted_pdf_name(subcomponent):
    token = re.sub(r"[^A-Za-z0-9]+", "", subcomponent)
    return f"OBP_{token or 'Spares'}.pdf"


def embedded_items(page):
    width = page.rect.width
    height = page.rect.height
    items = []
    for word in page.get_text("words"):
        text = clean_text(word[4])
        if not text:
            continue
        x0, y0, x1, y1 = map(float, word[:4])
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
        })
    return items


def ocr_items(pdf_path, page_idx, extractor):
    image = pdf_page_to_image(str(pdf_path), page_idx, dpi=170)
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
        })
    return items


def group_lines(items, y_tolerance=0.009):
    rows = []
    current = []
    current_y = None
    for item in sorted(items, key=lambda item: (item["rel_y"], item["rel_x"])):
        if current_y is None or abs(item["rel_y"] - current_y) <= y_tolerance:
            current.append(item)
            current_y = item["rel_y"] if current_y is None else (current_y + item["rel_y"]) / 2
        else:
            current.sort(key=lambda item: item["rel_x"])
            rows.append(current)
            current = [item]
            current_y = item["rel_y"]
    if current:
        current.sort(key=lambda item: item["rel_x"])
        rows.append(current)
    return rows


def line_text(line):
    return clean_text(" ".join(item["text"] for item in sorted(line, key=lambda item: item["rel_x"])))


def page_text(items):
    return "\n".join(line_text(line) for line in group_lines(items, y_tolerance=0.008))


def detect_subcomponent(text):
    upper = text.upper()
    candidates = [
        ("Electrical Part", "ELECTRICAL PART"),
        ("Mechanical Part", "MECHANICAL PART"),
        ("Hydraulic Part", "HYDRAULIC PART"),
        ("Special Spare Parts", "SPECIAL SPARE PARTS LIST"),
        ("Marine Elevator", "MARINE ELEVATOR"),
        ("Electro-Hydraulic Steering Gear", "ELECTRO-HYDRAULIC STEERING GEAR"),
        ("LO Purifier", "M/E LO PURIFIERS"),
        ("Dry Chemical Powder System", "DRY CHEMICAL POWDER SYSTEM"),
    ]
    for label, marker in candidates:
        if marker in upper:
            return label
    if "SPARE PART LIST" in upper or "SPARE PARTS LIST" in upper:
        return "Spare Part List"
    return "OBP Spare List"


def detect_layout(text):
    upper = text.upper()
    if "SERIAL NUMBER" in upper and "CODE" in upper and "QUANTITY" in upper:
        return "embedded_simple"
    if "APARE PARTS LIST" in upper or "MARINE ELEVATOR" in upper:
        return "name_type_qty"
    if "PARTS NO" in upper and "SUPPLY QT" in upper:
        return "symbol_parts"
    if "COMPONENT NAME" in upper and "DESCRIPTION" in upper:
        return "component_description"
    return "spare_drawing"


def item_starts(items):
    starts = []
    for item in items:
        text = clean_text(item["text"]).strip(".")
        if item["rel_x"] > 0.18:
            continue
        if POS_RE.match(text) and not NOISE_RE.match(text):
            starts.append({**item, "text": text})
    starts.sort(key=lambda item: (item["rel_y"], item["rel_x"]))

    deduped = []
    for item in starts:
        if deduped and abs(item["rel_y"] - deduped[-1]["rel_y"]) < 0.01 and item["text"] == deduped[-1]["text"]:
            continue
        deduped.append(item)
    return deduped


def collect_band(items, start, next_y):
    top = max(0, start["rel_y"] - 0.01)
    bottom = min(0.98, next_y - 0.004, start["rel_y"] + 0.085)
    return [item for item in items if top <= item["rel_y"] < bottom]


def join_col(band, left, right):
    values = [
        item for item in band
        if left <= item["rel_x"] < right and not NOISE_RE.match(item["text"])
    ]
    lines = [line_text(line) for line in group_lines(values, y_tolerance=0.01)]
    return clean_text(" ".join(line for line in lines if line))


def first_num_col(band, left, right):
    text = join_col(band, left, right)
    match = re.search(r"\b\d+(?:\.\d+)?\b", text)
    return match.group(0) if match else text


def looks_like_code(text):
    text = clean_text(text)
    if not text:
        return False
    if re.fullmatch(r"(?i)(?:o[- ]?ring|gasket|seal|packing|button|lamp|breaker|fuse core|grease nipple)", text):
        return False
    if re.search(r"\d", text):
        return True
    return bool(re.search(r"\b(?:CL|MP|M2|PT|GV|AD|DX|LC|RXM|XB|BLA|T4|P|R)\w+", text, re.IGNORECASE))


def fallback_name_starts(items, starts):
    existing_y = [item["rel_y"] for item in starts]
    candidates = []
    for item in items:
        text = clean_text(item["text"])
        if not (0.16 <= item["rel_x"] <= 0.40 and 0.22 <= item["rel_y"] <= 0.90):
            continue
        if len(text) < 3 or NOISE_RE.match(text):
            continue
        if re.search(r"^\$?\d", text) or re.search(r"^(?:MFR|MANUFACTURER|ADDRESS|TEL|FAX|PAGE|SHIP)", text, re.IGNORECASE):
            continue
        if any(abs(item["rel_y"] - y) < 0.025 for y in existing_y):
            continue
        candidates.append({**item, "text": ""})
        existing_y.append(item["rel_y"])
    return sorted(starts + candidates, key=lambda item: (item["rel_y"], item["rel_x"]))


def parse_record_from_band(layout, band, pos, page_no, subcomponent):
    if layout == "name_type_qty":
        name = join_col(band, 0.16, 0.62)
        part = join_col(band, 0.62, 0.84)
        qty = first_num_col(band, 0.84, 0.98)
        material = ""
        drawing = ""
        remarks = ""
    elif layout == "symbol_parts":
        name = join_col(band, 0.18, 0.42)
        qty = first_num_col(band, 0.40, 0.48)
        part = join_col(band, 0.48, 0.68)
        drawing = ""
        material = ""
        remarks = join_col(band, 0.78, 0.98)
    elif layout == "component_description":
        name = join_col(band, 0.18, 0.56)
        part = join_col(band, 0.56, 0.75)
        qty = first_num_col(band, 0.84, 0.98)
        drawing = ""
        material = ""
        remarks = ""
    elif layout == "embedded_simple":
        name_left = join_col(band, 0.25, 0.43)
        name_mid = join_col(band, 0.43, 0.74)
        if name_left and name_mid and looks_like_code(name_mid) and not looks_like_code(name_left):
            name = name_left
            part = name_mid
        elif name_mid:
            part = name_left
            name = name_mid
        else:
            name = name_left or join_col(band, 0.15, 0.45)
            part = join_col(band, 0.45, 0.74)
        qty = first_num_col(band, 0.74, 0.98)
        drawing = ""
        material = ""
        remarks = ""
    else:
        name = join_col(band, 0.14, 0.35)
        material = join_col(band, 0.45, 0.60)
        qty = first_num_col(band, 0.60, 0.72)
        drawing = join_col(band, 0.72, 0.79)
        part = join_col(band, 0.79, 0.86) or drawing
        remarks = join_col(band, 0.86, 0.98)

    name = titleish(name)
    part = clean_text(part)
    if looks_like_code(name) and part and not looks_like_code(part):
        name, part = titleish(part), clean_text(name)
    material = clean_text(material).upper()
    remarks = clean_text(remarks)
    drawing = clean_text(drawing)
    qty = clean_text(qty)
    if not name or NOISE_RE.match(name):
        return None

    return {
        "component": COMPONENT,
        "sub_component": subcomponent,
        "manufacturer": "",
        "model": MODEL,
        "name": name,
        "part": part,
        "drawing": drawing,
        "pos": pos,
        "size": "",
        "material": material,
        "remarks": remarks,
        "details": f"Qty: {qty}" if qty else "",
        "page": page_no,
        "manual": MANUAL_PDF_NAME,
        "uom": DEFAULT_UOM,
        "pdf": extracted_pdf_name(subcomponent),
    }


def extract_page(items, page_no):
    text = page_text(items)
    layout = detect_layout(text)
    subcomponent = detect_subcomponent(text)
    starts = item_starts(items)
    if len(starts) <= 12:
        starts = fallback_name_starts(items, starts)
    records = []
    for idx, start in enumerate(starts):
        next_y = starts[idx + 1]["rel_y"] if idx + 1 < len(starts) else 0.97
        if next_y - start["rel_y"] < 0.008:
            continue
        band = collect_band(items, start, next_y)
        pos = start["text"] if POS_RE.match(start["text"]) else str(idx + 1)
        record = parse_record_from_band(layout, band, pos, page_no, subcomponent)
        if record:
            records.append(record)
    return records


def to_template_row(record):
    return [
        record["component"],
        record["sub_component"],
        record["manufacturer"],
        record["model"],
        record["name"],
        record["part"],
        record["drawing"],
        record["pos"],
        record["size"],
        record["material"],
        record["remarks"],
        record["details"],
        record["page"],
        record["manual"],
        "",
        record["uom"],
        record["pdf"],
        "",
        "Yes",
        "",
        "",
    ]


def write_workbook(rows):
    wb = openpyxl.load_workbook(TEMPLATE_PATH, keep_vba=True)
    ws = wb.active
    for row_idx, row in enumerate(rows, start=3):
        for col_idx, value in enumerate(row, start=1):
            ws.cell(row_idx, col_idx).value = value
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUTPUT_PATH)
    wb.close()


def main():
    doc = fitz.open(PDF_PATH)
    end_page = END_PAGE or len(doc)
    extractor = OCRExtractor()
    records = []
    for page_no in range(START_PAGE, min(end_page, len(doc)) + 1):
        page = doc[page_no - 1]
        items = embedded_items(page)
        if len(items) < 8:
            items = ocr_items(PDF_PATH, page_no - 1, extractor)
        page_records = extract_page(items, page_no)
        records.extend(page_records)
        print(f"Page {page_no}: {len(page_records)} rows")
    doc.close()

    rows = [to_template_row(record) for record in records]
    write_workbook(rows)
    print(f"Rows written: {len(rows)}")
    print(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
