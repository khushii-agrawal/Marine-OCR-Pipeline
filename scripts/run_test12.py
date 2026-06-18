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


PDF_PATH = PROJECT_ROOT / "test" / "Test 12" / "M-213-M0000012-Positive Displacement Pump-1 REV1.1 (2).pdf"
TEMPLATE_PATH = PROJECT_ROOT / "template" / "Spares_Capture_Template_Ver12 2.xlsm"
OUTPUT_PATH = PROJECT_ROOT / "output" / "Test12_Positive_Displacement_Pump_extracted.xlsm"

COMPONENT = "Positive Displacement Pump"
MANUFACTURER = "NANIWA PUMP MFG.CO.,LTD."
MANUAL_PDF_NAME = "M-213-M0000012-Positive Displacement Pump-1 REV1.1 (2).pdf"
DEFAULT_UOM = "Pcs"

START_PAGE = int(os.environ.get("TEST12_START_PAGE", "1"))
END_PAGE = int(os.environ.get("TEST12_END_PAGE", "0"))

DRAWING_RE = re.compile(r"(?:[A-Z0-9]{1,4}D[SGH]\d{4}|CO?DS\d{4})", re.IGNORECASE)
MODEL_RE = re.compile(r"\bMODEL\s*:?\s*(.+?)(?:\s+\d+\s*SETS?\b|\s+TOTAL\b|$)", re.IGNORECASE)


def clean_text(text):
    text = re.sub(r"\s+", " ", str(text or "").strip())
    text = text.replace("|", "")
    return text.strip(" -")


def titleish(text):
    text = clean_text(text)
    text = re.sub(r"\bN0\b", "NO", text, flags=re.IGNORECASE)
    if not text:
        return ""
    fixes = {
        "l.o.pump": "L.O. Pump",
        "l.o.": "L.O.",
        "m/e": "M/E",
        "e/r": "E/R",
        "t/c": "T/C",
        "mgo": "MGO",
        "ulshfo": "ULSHFO",
        "f.o.": "F.O.",
        "suc.": "SUC.",
        "del.": "DEL.",
        "no.1": "No.1",
        "no.2": "No.2",
    }
    words = []
    for token in text.split():
        key = token.lower()
        bare_key = token.strip("()").lower()
        if key in fixes:
            words.append(fixes[key])
        elif bare_key in fixes:
            words.append(token.replace(token.strip("()"), fixes[bare_key]))
        else:
            words.append(token[:1].upper() + token[1:].lower())
    return clean_text(" ".join(words))


def extracted_pdf_name(subcomponent):
    token = re.sub(r"[^A-Za-z0-9]+", "", subcomponent)
    return f"PDP_{token or 'PositiveDisplacementPump'}.pdf"


def ocr_items(pdf_path, page_idx, extractor):
    image = pdf_page_to_image(str(pdf_path), page_idx, dpi=180)
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
            "rel_x0": x0 / width,
            "rel_y0": y0 / height,
            "rel_x1": x1 / width,
            "rel_y1": y1 / height,
            "rel_cx": ((x0 + x1) / 2) / width,
            "rel_cy": ((y0 + y1) / 2) / height,
        })
    return items


def group_lines(items, y_tolerance=0.012):
    rows = []
    current = []
    current_y = None
    for item in sorted(items, key=lambda item: (item["rel_y0"], item["rel_x0"])):
        if current_y is None or abs(item["rel_y0"] - current_y) <= y_tolerance:
            current.append(item)
            current_y = item["rel_y0"] if current_y is None else (current_y + item["rel_y0"]) / 2
        else:
            current.sort(key=lambda item: item["rel_x0"])
            rows.append(current)
            current = [item]
            current_y = item["rel_y0"]
    if current:
        current.sort(key=lambda item: item["rel_x0"])
        rows.append(current)
    return rows


def line_text(line):
    return clean_text(" ".join(item["text"] for item in sorted(line, key=lambda item: item["rel_x0"])))


def page_text(items):
    return "\n".join(line_text(line) for line in group_lines(items))


def normalize_model(text):
    text = clean_text(text).upper().replace("_", "-")
    text = re.sub(r"\s+", "-", text)
    text = text.replace("1ON", "10N").replace("75O", "750")
    text = text.replace("25O", "250").replace("10O", "100").replace("1OO", "100")
    text = text.replace("2ONC", "20NC")
    text = text.replace("ALTV1", "ALTV-1")
    return text


def detect_model(items, text):
    for item in items:
        value = clean_text(item["text"])
        if "MODEL" not in value.upper():
            continue
        value = re.sub(r"^.*?\bMODEL\s*:?\s*", "", value, flags=re.IGNORECASE)
        value = re.split(r"\s+(?:TOTAL|\d+\s*SETS?|VERTICAL|HORIZONTAL)\b", value, maxsplit=1, flags=re.IGNORECASE)[0]
        value = clean_text(value)
        if value:
            return normalize_model(value)
    match = MODEL_RE.search(text)
    if not match:
        return ""
    value = re.split(r"\s+(?:VERTICAL|HORIZONTAL)\b", match.group(1), maxsplit=1, flags=re.IGNORECASE)[0]
    return normalize_model(value)


def detect_metadata(items):
    text = page_text(items)
    drawing = ""
    for item in items:
        candidate = clean_text(item["text"]).upper()
        if DRAWING_RE.fullmatch(candidate):
            drawing = candidate
            break
    drawing_match = None if drawing else DRAWING_RE.search(text)
    drawing = drawing or (drawing_match.group(0).upper() if drawing_match else "")
    if not drawing:
        for item in items:
            candidate = clean_text(item["text"]).upper()
            if DRAWING_RE.fullmatch(candidate):
                drawing = candidate
                break
    if drawing == "CODS0132":
        drawing = "C0DS0132"

    model = detect_model(items, text)

    header_lines = []
    for line in group_lines([
        item for item in items
        if 0.10 <= item["rel_x0"] <= 0.42 and 0.065 <= item["rel_y0"] <= 0.145
    ], y_tolerance=0.01):
        value = line_text(line)
        value = re.sub(r"\bMODEL\s*:.*$", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\b\d+\s*SETS?\b", "", value, flags=re.IGNORECASE)
        value = clean_text(value)
        if re.search(r"\b(?:SPARE|PARTS|LIST|TOTAL|PAGE|SHIP|BOX|MASS)\b", value, re.IGNORECASE):
            continue
        if re.match(r"^(?:VERTICAL|HORIZONTAL)\b", value, re.IGNORECASE):
            continue
        if len(value) >= 3:
            header_lines.append(value)
    subcomponent = titleish(" ".join(header_lines)) or COMPONENT
    return subcomponent, model, drawing


def is_noise_token(text):
    value = clean_text(text)
    if not value:
        return True
    if re.fullmatch(r"[$#]?\d+(?:\.\d+)?", value):
        return True
    if re.fullmatch(r"[()&./-]+", value):
        return True
    if value.lower().replace(" ", "") in {"bod", "bo d", "e", "d"}:
        return True
    return False


def join_col(band, start_x, end_x):
    parts = []
    for item in sorted(band, key=lambda item: (item["rel_y0"], item["rel_x0"])):
        if start_x <= item["rel_x0"] <= end_x and not is_noise_token(item["text"]):
            parts.append(item["text"])
    value = clean_text(" ".join(parts))
    value = re.sub(r"^\d+\s+", "", value)
    value = re.sub(r"\s+", " ", value)
    return clean_text(value)


def quantity_col(band, start_x, end_x):
    parts = []
    for item in sorted(band, key=lambda item: (item["rel_y0"], item["rel_x0"])):
        value = clean_text(item["text"]).upper()
        if start_x <= item["rel_x0"] <= end_x and re.fullmatch(r"\d+|SET|SETS|1SET", value):
            parts.append(value)
    return clean_text(" ".join(parts))


KNOWN_SPARE_NAMES = [
    "(SUC. & DEL.) LIFT BOLT NUT",
    "SUC. VALVE SEAT",
    "DEL. VALVE SEAT",
    "SUC. SPRING SEAT",
    "DEL. SPRING SEAT",
    "SUBMERGED BEARING",
    "MECHANICAL SEAL",
    "BEARING METAL",
    "GLAND PACKING",
    "BALL BEARING",
    "BUCKET RING",
    "VALVE SPRING",
    "ZINC ANODE",
    "OIL SEAL",
    "SUC. VALVE",
    "DEL. VALVE",
    "LIFT BOLT",
]


def normalize_spare_name(name):
    upper = clean_text(name).upper()
    for known in KNOWN_SPARE_NAMES:
        if known in upper:
            return titleish(known)
    return titleish(name)


def part_no_text(text):
    match = re.search(r"\d{3,4}", clean_text(text))
    return match.group(0) if match else ""


def additional_spare_list_rows(items, page_no):
    text = page_text(items)
    upper_text = text.upper()
    if "ADDITIONAL" not in upper_text or "SPARE PARTS LIST" not in upper_text:
        return []

    anchors = [
        item for item in items
        if 0.775 <= item["rel_x0"] <= 0.84
        and 0.22 <= item["rel_y0"] <= 0.80
        and part_no_text(item["text"])
    ]
    anchors.sort(key=lambda item: item["rel_cy"])

    records = []
    for idx, anchor in enumerate(anchors):
        prev_y = anchors[idx - 1]["rel_cy"] if idx else 0.215
        next_y = anchors[idx + 1]["rel_cy"] if idx + 1 < len(anchors) else min(anchor["rel_cy"] + 0.075, 0.82)
        band_top = max((prev_y + anchor["rel_cy"]) / 2, 0.205)
        band_bottom = min((anchor["rel_cy"] + next_y) / 2, 0.82)
        if idx == 0:
            band_top = max(anchor["rel_cy"] - 0.06, 0.205)
        band = [item for item in items if band_top <= item["rel_cy"] < band_bottom]

        name = normalize_spare_name(join_col(band, 0.10, 0.46))
        anchor_suffix = re.sub(r"^\D*\d{3,4}", "", clean_text(anchor["text"])).strip()
        subcomponent = titleish(clean_text(f"{anchor_suffix} {join_col(band, 0.83, 0.98)}")) or COMPONENT
        work_qty = quantity_col(band, 0.635, 0.675)
        spare_qty = quantity_col(band, 0.675, 0.73)
        part_no = part_no_text(anchor["text"])

        if not name or not part_no:
            continue
        records.append({
            "sub_component": subcomponent,
            "model": "",
            "drawing": "",
            "page_no": page_no,
            "pos": str(len(records) + 1),
            "name": name,
            "part_no": part_no,
            "material": "",
            "work_qty": work_qty,
            "spare_qty": spare_qty,
            "remarks": "",
        })
    return records


def spare_list_rows(items, page_no):
    text = page_text(items)
    if "SPARE PARTS LIST" not in text.upper():
        return []

    additional_rows = additional_spare_list_rows(items, page_no)
    if additional_rows:
        return additional_rows

    subcomponent, model, drawing = detect_metadata(items)
    anchors = [
        item for item in items
        if 0.775 <= item["rel_x0"] <= 0.845
        and 0.22 <= item["rel_y0"] <= 0.78
        and re.fullmatch(r"\d{3,4}", clean_text(item["text"]))
    ]
    anchors.sort(key=lambda item: item["rel_cy"])

    records = []
    for idx, anchor in enumerate(anchors):
        prev_y = anchors[idx - 1]["rel_cy"] if idx else 0.215
        next_y = anchors[idx + 1]["rel_cy"] if idx + 1 < len(anchors) else min(anchor["rel_cy"] + 0.075, 0.79)
        band_top = max((prev_y + anchor["rel_cy"]) / 2, 0.205)
        band_bottom = min((anchor["rel_cy"] + next_y) / 2, 0.80)
        if idx == 0:
            band_top = max(anchor["rel_cy"] - 0.06, 0.205)
        band = [item for item in items if band_top <= item["rel_cy"] < band_bottom]

        name = join_col(band, 0.10, 0.46)
        name = re.sub(r"^(?:\d+\s*)?", "", name)
        name = normalize_spare_name(name)
        material = join_col(band, 0.535, 0.63)
        work_qty = quantity_col(band, 0.635, 0.675)
        spare_qty = quantity_col(band, 0.682, 0.725)
        remarks = join_col(band, 0.855, 0.965)

        if not name:
            continue
        records.append({
            "sub_component": subcomponent,
            "model": model,
            "drawing": drawing,
            "page_no": page_no,
            "pos": str(len(records) + 1),
            "name": name,
            "part_no": anchor["text"],
            "material": material.upper(),
            "work_qty": work_qty,
            "spare_qty": spare_qty,
            "remarks": remarks,
        })
    return records


def to_template_row(record):
    details = []
    if record["work_qty"]:
        details.append(f"Work per pump: {record['work_qty']}")
    if record["spare_qty"]:
        details.append(f"Spare per ship: {record['spare_qty']}")
    return [
        COMPONENT,
        record["sub_component"],
        MANUFACTURER,
        record["model"],
        record["name"],
        record["part_no"],
        record["drawing"],
        record["pos"],
        "",
        record["material"],
        record["remarks"],
        "; ".join(details),
        record["page_no"],
        MANUAL_PDF_NAME,
        "",
        DEFAULT_UOM,
        extracted_pdf_name(record["sub_component"]),
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
    candidates = [
        OUTPUT_PATH,
        OUTPUT_PATH.with_name(f"{OUTPUT_PATH.stem}_fallback{OUTPUT_PATH.suffix}"),
    ]
    last_error = None
    for candidate in candidates:
        try:
            wb.save(candidate)
            wb.close()
            return candidate
        except PermissionError as exc:
            last_error = exc
            print(f"Could not write {candidate}: {exc}")
    wb.close()
    raise last_error


def main():
    doc = fitz.open(PDF_PATH)
    end_page = END_PAGE or len(doc)
    extractor = OCRExtractor()
    records = []
    for page_no in range(START_PAGE, min(end_page, len(doc)) + 1):
        items = ocr_items(PDF_PATH, page_no - 1, extractor)
        page_records = spare_list_rows(items, page_no)
        records.extend(page_records)
        print(f"Page {page_no}: {len(page_records)} rows")
    doc.close()

    rows = [to_template_row(record) for record in records]
    saved_path = write_workbook(rows)
    print(f"Rows written: {len(rows)}")
    print(f"Output: {saved_path}")


if __name__ == "__main__":
    main()
