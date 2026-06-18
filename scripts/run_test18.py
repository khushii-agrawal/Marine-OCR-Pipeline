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


PDF_PATH = PROJECT_ROOT / "Test" / "Test 18" / "Extracted pages from Main engine assessories (1).pdf"
TEMPLATE_PATH = PROJECT_ROOT / "template" / "Spares_Capture_Template_Ver12 2.xlsm"
OUTPUT_PATH = Path(os.environ.get(
    "TEST18_OUTPUT_PATH",
    PROJECT_ROOT / "output" / "Test18_Main_engine_accessories_extracted.xlsm",
))

COMPONENT = "Main Engine Accessories"
MANUFACTURER = ""
MODEL = ""
MANUAL_PDF_NAME = "Extracted pages from Main engine assessories (1).pdf"
DEFAULT_UOM = "Pcs"

START_PAGE = int(os.environ.get("TEST18_START_PAGE", "1"))
END_PAGE = int(os.environ.get("TEST18_END_PAGE", "90"))

PLEIGER_ORDER_RE = re.compile(
    r"\b(?:\d[.\s]\d{2}[.\s]\d{2}[.\s]\d{2}[.\s]\d|"
    r"\d[.\s]\d{2}[.\s]\d{1,5}[.\s]\d{1,5}[.\s]\d|"
    r"\d{1,2}[,.]\d\s*kN[-\s]*\d{3}[.\s]\d{3}(?:-\d)?|"
    r"\d{2}H?[-\s]?\d{3}[.\s]\d{3}(?:-\d)?|"
    r"SA\s*\d+[-\s]\d{3}[.\s]\d{3}|"
    r"\d{2}[-\s]\d{3}[.\s]\d{3}|"
    r"\d{2}[-\s]\d{3}[.\s]\d{3}(?:-\d)?|"
    r"\d\.\d{2}\.\d{4}\.\d|"
    r"\d[.]\d{5}[.]\d{3}[.]\d)\b",
    re.IGNORECASE,
)
BOLL_IDENT_RE = re.compile(r"\b(?:\d{6,8}|[A-Z]\d{4,}|E[S5]\d{4,})\b", re.IGNORECASE)
POS_RE = re.compile(r"^\d{1,4}(?:[.,]\d+)?$")
NW_CODE_RE = re.compile(r"\b(?:\d[- ]\d{3,5}|[AMR]?\s*\d{1,2}\s*x\s*\d{1,3}(?:x\d(?:,\d)?)?|A\s*\d{1,3}\s*x\s*\d{1,3}\s*x\s*\d(?:,\d)?|R\s*1|SXN\s*\d+)\b", re.IGNORECASE)
SKIP_NAME_RE = re.compile(r"\b(?:DATE|PAGE|LIST|IDENTITY|DESIGNATION|BENENNUNG|MATERIAL|QUANTITY|CERTIFICATE|ORDER|ITEM|QTY|POS)\b", re.IGNORECASE)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).strip(" -|")


def titleish(value):
    text = clean_text(value)
    if not text:
        return ""
    fixes = {
        "dn": "DN",
        "pn": "PN",
        "nw": "NW",
        "m": "M",
        "m5": "M5",
        "m6": "M6",
        "m8": "M8",
        "m10": "M10",
        "m12": "M12",
        "m16": "M16",
        "m20": "M20",
        "r1": "R1",
        "cpl.": "cpl.",
        "compl.": "compl.",
        "hex.": "Hex.",
        "lub.": "Lub.",
    }
    out = []
    for token in text.split():
        key = token.lower().strip(",.;:")
        if key in fixes:
            suffix = token[len(token.rstrip(",.;:")):]
            out.append(fixes[key] + suffix)
        elif token.isupper() and len(token) <= 4:
            out.append(token)
        else:
            out.append(token[:1].upper() + token[1:].lower())
    return clean_text(" ".join(out))


def normalize_part(value):
    text = clean_text(value).upper()
    text = text.replace(" ", "")
    text = text.replace(",", ".")
    text = text.replace("O", "0")
    text = text.replace("..", ".")
    return text.strip(".")


def ocr_items(pdf_path, page_no, extractor, dpi=180):
    image = pdf_page_to_image(str(pdf_path), page_no - 1, dpi=dpi)
    return ocr_image_items(image, extractor)


def ocr_image_items(image, extractor):
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


def text_in(row, left, right):
    return clean_text(" ".join(item["text"] for item in sorted(row, key=lambda item: item["rel_x"]) if left <= item["rel_x"] <= right))


def row_text(row):
    return clean_text(" ".join(item["text"] for item in sorted(row, key=lambda item: item["rel_x"])))


def page_text(items):
    return "\n".join(row_text(row) for row in group_rows(items))


def data_name(value):
    text = clean_text(value)
    if not text or len(re.sub(r"[^A-Za-z]", "", text)) < 2:
        return False
    if SKIP_NAME_RE.search(text):
        return False
    if re.fullmatch(r"[\d\s.,:/()-]+", text):
        return False
    return True


def detected_subcomponent(items, fallback="Main Engine Accessories"):
    text = page_text(items)
    if "BOLL" in text.upper() and "KIRCH" in text.upper():
        list_match = re.search(r"\b(?:LIST NO\.?|LISTNO)\s*(\d{4,5})", text, re.IGNORECASE)
        return f"Boll & Kirch List {list_match.group(1)}" if list_match else "Boll & Kirch Spare Parts"
    if "EDUR" in text.upper():
        return "EDUR Medium Pressure Pump"
    patterns = [
        r"Spare Parts List:?\s*([A-Za-z0-9 ,./-]+)",
        r"Spareparts List for\s+([A-Za-z0-9 ,./-]+)",
        r"Spareparts List\s+for\s+([A-Za-z0-9 ,./-]+)",
        r"Typ[: ]+\s*([A-Za-z0-9 .,/:-]+)",
        r"Typ\s+([0-9.]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = re.split(r"\b(?:Pos|Item|Page|Blatt|Date|for)\b", match.group(1), flags=re.IGNORECASE)[0]
            if data_name(value):
                return titleish(value)
    return fallback


def nearest_english_line(rows, idx, name_range):
    for offset in [1, 0, 2]:
        if idx + offset >= len(rows):
            continue
        candidate = text_in(rows[idx + offset], *name_range)
        if data_name(candidate):
            if candidate.isupper() and offset == 0:
                continue
            return candidate
    candidate = text_in(rows[idx], *name_range)
    return candidate if data_name(candidate) else ""


def pleiger_rows(items, page_no):
    text = page_text(items)
    if not re.search(r"\b(?:Ersatzteilliste|Spare\s*Parts?\s*List|Spareparts\s*List|Bestell[-\s]*Nr|Order[-\s]*No|item\s+quantiti)\b", text, re.IGNORECASE):
        return []
    if "BOLL" in text.upper() and "KIRCH" in text.upper():
        return []

    rows = group_rows(items)
    subcomponent = detected_subcomponent(items, "Pleiger Spare Parts")
    records = []
    current = None
    for idx, row in enumerate(rows):
        line = row_text(row)
        if SKIP_NAME_RE.search(line) and "Spare" not in line:
            continue
        pos = text_in(row, 0.08, 0.18)
        pos_match = re.search(r"\b\d{1,3}\b", pos)
        qty = text_in(row, 0.18, 0.32)
        qty_match = re.search(r"\b\d{1,3}\b", qty)
        order_text = text_in(row, 0.66, 0.92)
        order_match = PLEIGER_ORDER_RE.search(order_text)
        if not order_match:
            order_match = PLEIGER_ORDER_RE.search(line)
        if not order_match and idx + 1 < len(rows):
            order_match = PLEIGER_ORDER_RE.search(text_in(rows[idx + 1], 0.66, 0.92))
        has_anchor = bool(pos_match and (order_match or data_name(text_in(row, 0.30, 0.66))))
        if has_anchor:
            if current and data_name(current["name"]):
                records.append(current)
            name = nearest_english_line(rows, idx, (0.30, 0.66))
            current = {
                "sub_component": subcomponent,
                "model": MODEL,
                "drawing": "",
                "pos": pos_match.group(0) if pos_match else "",
                "part": normalize_part(order_match.group(0)) if order_match else "",
                "name": titleish(name or text_in(row, 0.30, 0.66)),
                "material": text_in(row, 0.86, 0.96),
                "qty": qty_match.group(0) if qty_match else "",
                "remarks": "",
                "page": page_no,
            }
            continue
    if current and data_name(current["name"]):
        records.append(current)
    return records


def pleiger_annex_rows(items, page_no):
    text = page_text(items)
    if "PAUL PLEIGER" not in text.upper() and "for 3-way valve" not in text.lower():
        return []
    if "Designation" not in text and "designation" not in text:
        return []
    rows = group_rows(items)
    records = []
    subcomponent = detected_subcomponent(items, "Pleiger Annex Flange")
    last_pos = 0
    for idx, row in enumerate(rows):
        pos = text_in(row, 0.09, 0.15)
        qty = text_in(row, 0.16, 0.23)
        name = text_in(row, 0.25, 0.55)
        part = text_in(row, 0.58, 0.78)
        material = text_in(row, 0.84, 0.96)
        if not re.fullmatch(r"\d{1,3}", pos):
            sorted_items = sorted(row, key=lambda item: item["rel_x"])
            if len(sorted_items) >= 3 and re.fullmatch(r"\d{1,3}", sorted_items[0]["text"]):
                if len(sorted_items) == 3 and last_pos:
                    pos = str(last_pos + 1)
                    qty = sorted_items[0]["text"]
                    name = sorted_items[1]["text"]
                    part = sorted_items[2]["text"]
                else:
                    pos = sorted_items[0]["text"]
                    qty = sorted_items[1]["text"] if len(sorted_items) > 1 else ""
                    name = " ".join(item["text"] for item in sorted_items[2:-1])
                    part = sorted_items[-1]["text"]
            elif last_pos and re.search(r"\b\d{1,3}\b", qty) and data_name(name) and part:
                pos = str(last_pos + 1)
        if not re.fullmatch(r"\d{1,3}", pos) or not data_name(name):
            continue
        next_name = text_in(rows[idx + 1], 0.45, 0.66) if idx + 1 < len(rows) else ""
        if data_name(next_name):
            name = next_name
        last_pos = int(pos)
        records.append({
            "sub_component": subcomponent,
            "model": MODEL,
            "drawing": "",
            "pos": pos,
            "part": normalize_part(part),
            "name": titleish(name),
            "material": material,
            "qty": re.search(r"\d+", qty).group(0) if re.search(r"\d+", qty) else "",
            "remarks": "",
            "page": page_no,
        })
    return records


def heat_exchanger_rows(items, page_no):
    text = page_text(items)
    upper = text.upper()
    if "HEAT EXCHANGER" not in upper or "LIST OF PARTS" not in upper:
        return []
    parts = [
        ("1", "Tube Nest"),
        ("2", "Cover D"),
        ("3", "Cover K"),
        ("4", "Gaskets"),
        ("5", "Case"),
        ("6", "Screws"),
    ]
    return [{
        "sub_component": "Prang Heat Exchanger",
        "model": "",
        "drawing": "",
        "pos": pos,
        "part": "",
        "name": name,
        "material": "",
        "qty": "",
        "remarks": "Embedded list of parts on operating-instructions page",
        "page": page_no,
    } for pos, name in parts]


def differential_pressure_drawing_rows(items, page_no):
    text = page_text(items)
    upper = text.upper()
    if "TYP 4362" not in upper and "TYP4.362" not in upper:
        return []
    if "SPARE PARTS DRAW" not in upper and "ERSATZTEILZEICHNUNG" not in upper:
        return []
    parts = [
        ("10", "Roll Diaphragm"),
        ("11", "Spring"),
        ("", "Piston"),
        ("", "Gasket"),
    ]
    return [{
        "sub_component": "Boll & Kirch Differential Pressure Indicator",
        "model": "Type 4362 / 4462",
        "drawing": "",
        "pos": pos,
        "part": "",
        "name": name,
        "material": "",
        "qty": "",
        "remarks": "Spare part drawing; order number to be stated with order",
        "page": page_no,
    } for pos, name in parts]


def edur_rows(items, page_no):
    text = page_text(items)
    if "EDUR" not in text.upper() and "Pt. -No" not in text:
        return []
    records = []
    for row in group_rows(items):
        pos = text_in(row, 0.09, 0.16)
        if not re.fullmatch(r"\d{3}(?:\.\d)?", pos):
            continue
        name = text_in(row, 0.38, 0.58)
        if not data_name(name):
            continue
        ident = text_in(row, 0.64, 0.78)
        din = text_in(row, 0.78, 0.88)
        records.append({
            "sub_component": "EDUR Medium Pressure Pump",
            "model": "NuV 25",
            "drawing": "",
            "pos": pos,
            "part": clean_text(ident),
            "name": titleish(name),
            "material": "",
            "qty": "",
            "remarks": clean_text(f"DIN/standard: {din}" if din else ""),
            "page": page_no,
        })
    return records


def boll_standard_rows(items, page_no):
    text = page_text(items).upper()
    if "IDENT" not in text and "IDEN" not in text:
        return []
    if "NW32" in text or "NW 32" in text:
        return []

    rows = group_rows(items)
    subcomponent = detected_subcomponent(items, "Boll & Kirch Spare Parts")
    records = []
    current = None
    for idx, row in enumerate(rows):
        pos_text = text_in(row, 0.035, 0.085)
        pos_match = re.search(r"\b\d{3,4}\b", pos_text.replace(" ", ""))
        ident_text = text_in(row, 0.105, 0.17)
        ident_match = BOLL_IDENT_RE.search(normalize_part(ident_text))
        if pos_match and (ident_match or data_name(text_in(row, 0.18, 0.45))):
            if current and data_name(current["name"]):
                records.append(current)
            name = ""
            for j in [idx + 1, idx, idx + 2]:
                if j >= len(rows):
                    continue
                candidate = text_in(rows[j], 0.18, 0.45)
                if data_name(candidate):
                    name = candidate
                    break
            material = text_in(row, 0.50, 0.60)
            qty = text_in(row, 0.74, 0.82)
            remarks = text_in(row, 0.60, 0.72)
            current = {
                "sub_component": subcomponent,
                "model": MODEL,
                "drawing": "",
                "pos": pos_match.group(0) if pos_match else "",
                "part": ident_match.group(0) if ident_match else normalize_part(ident_text),
                "name": titleish(name or text_in(row, 0.18, 0.45)),
                "material": material,
                "qty": qty,
                "remarks": remarks,
                "page": page_no,
            }
            continue
        continuation = text_in(row, 0.18, 0.45)
        if current and data_name(continuation) and len(continuation.split()) <= 5:
            if continuation.lower() not in current["name"].lower():
                current["name"] = clean_text(f"{current['name']} {titleish(continuation)}")
    if current and data_name(current["name"]):
        records.append(current)
    return records


def nw_matrix_rows(items, page_no):
    text = page_text(items).upper()
    if not ("NW32" in text or "NW 32" in text):
        return []
    records = []
    for row in group_rows(items):
        pos_name = text_in(row, 0.075, 0.20)
        match = re.match(r"^\s*(\d{1,3})\s*([A-Za-z].*)?$", pos_name)
        if not match:
            # OCR often glues the position to English name, e.g. 114gasket.
            match = re.match(r"^\s*(\d{1,3})([A-Za-z].*)$", pos_name)
        if not match:
            continue
        pos = match.group(1)
        name = match.group(2) or text_in(row, 0.115, 0.20)
        if not data_name(name):
            continue
        variants = [
            ("NW32", text_in(row, 0.29, 0.43)),
            ("NW40", text_in(row, 0.44, 0.58)),
            ("NW50", text_in(row, 0.59, 0.72)),
        ]
        for size, value in variants:
            value = clean_text(value)
            if not value:
                continue
            code_match = NW_CODE_RE.search(value)
            if not code_match:
                continue
            qty = ""
            qty_match = re.match(r"^(\d{1,2})\s+", value)
            if qty_match:
                qty = qty_match.group(1)
            records.append({
                "sub_component": "Boll & Kirch Matrix Spare List",
                "model": size,
                "drawing": "",
                "pos": pos,
                "part": clean_text(code_match.group(0)),
                "name": titleish(name),
                "material": "",
                "qty": qty,
                "remarks": size,
                "page": page_no,
            })
    return records


def first_part_code(row, start_x=0.12):
    tokens = [item["text"] for item in sorted(row, key=lambda item: item["rel_x"]) if item["rel_x"] >= start_x]
    text = clean_text(" ".join(tokens))
    patterns = [
        r"Art[-\s]*Nr\s*\d+\s*[\d., *-]+",
        r"Ar[1l][- ]*Nr\s*\d+\s*[\d., *-]+",
        r"\d[- ]\d{4,5}",
        r"\d{4,6}",
        r"(?:M\s*)?\d{1,2}\s*x?\s*\d{1,3}\s*(?:Mu)?\s*DIN\s*\d{3,4}",
        r"WN\s*\d+\s*Nr\.?\s*\d+",
        r"R\s*'?I?L?\"?\s*DIN\s*910",
        r"A\s*16\s*x?\s*10\s*x?\s*50",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return clean_text(match.group(0))
    return clean_text(" ".join(tokens[:4]))


def rotated_boll_matrix_rows(items, page_no):
    if page_no not in {61, 62}:
        return []
    image = pdf_page_to_image(str(PDF_PATH), page_no - 1, dpi=180)
    rotated = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    rotated_items = ocr_image_items(rotated, OCRExtractor())
    rows = group_rows(rotated_items, y_tolerance=14)
    text = page_text(rotated_items)
    if "SPARE PART LIST" not in text.upper() and "ERSATZTEILLISTE" not in text.upper():
        return []

    name_map = {
        "28": "O-Ring",
        "29": "Stud Bolt",
        "30": "Nut",
        "31": "Reversing Handle",
        "32": "Differential Pressure Indicator",
        "33": "Screw Plug",
        "34": "Gasket",
        "35": "Steam Heating Pen",
        "36": "Electric Heating Pen",
        "37": "Bottom Fixture",
        "38": "Screw",
        "39": "Plain Washer",
        "41": "Gasket",
        "42": "Stud Bolt",
        "43": "Nut",
        "67": "Screw",
        "87": "Gear Cover",
        "47": "Pinion Shaft",
        "97": "Spur Wheel",
        "107": "Blank Flange",
    }
    records = []
    for row in rows:
        line = row_text(row)
        match = re.match(r"^\s*(107|\d{2})\b", line)
        if not match:
            continue
        pos = match.group(1)
        if pos not in name_map:
            continue
        part = first_part_code(row)
        details = clean_text(" ".join(item["text"] for item in sorted(row, key=lambda item: item["rel_x"]) if item["rel_x"] >= 0.12))
        if name_map[pos] == "Differential Pressure Indicator" and re.search(r"\b(?:to order|nach Auftrag|commande)\b", details, re.IGNORECASE):
            part = "TO ORDER"
        elif len(part) > 36 or re.search(r"\b(?:designation|indi|diff de pression)\b", part, re.IGNORECASE):
            part = ""
        part_value = part if part.upper() == "TO ORDER" else normalize_part(part)
        records.append({
            "sub_component": "Boll & Kirch Rotated Matrix Spare List",
            "model": "Type 2.05.5 / 2.05.5.4",
            "drawing": "",
            "pos": pos,
            "part": part_value,
            "name": name_map[pos],
            "material": "",
            "qty": "",
            "remarks": f"Variant codes: {details}",
            "page": page_no,
        })

    if page_no == 62:
        lower_text = text.lower()
        if "schnellverschl" in lower_text or "quick open" in lower_text:
            records.append({
                "sub_component": "Boll & Kirch Rotated Matrix Spare List",
                "model": "Type 2.05.5 / 2.05.5.4",
                "drawing": "",
                "pos": "15",
                "part": "4-20590",
                "name": "Cover With Quick Open Device",
                "material": "",
                "qty": "",
                "remarks": "Variant codes visible on page: 7706 / 9044 / 9015 with 4-20590",
                "page": page_no,
            })
        if "parallel keys" in lower_text:
            records.append({
                "sub_component": "Boll & Kirch Rotated Matrix Spare List",
                "model": "Type 2.05.5 / 2.05.5.4",
                "drawing": "",
                "pos": "57",
                "part": "A16X10X50 DIN6885",
                "name": "Parallel Keys",
                "material": "",
                "qty": "",
                "remarks": "Supplementary item for mantle element / filter-candle execution",
                "page": page_no,
            })
    return records


def extract_page(items, page_no):
    for extractor in (
        rotated_boll_matrix_rows,
        nw_matrix_rows,
        edur_rows,
        boll_standard_rows,
        heat_exchanger_rows,
        differential_pressure_drawing_rows,
        pleiger_rows,
        pleiger_annex_rows,
    ):
        records = extractor(items, page_no)
        if records:
            return records
    return []


def to_template_row(record):
    details = clean_text(f"Qty: {record['qty']}" if record.get("qty") else "")
    return [
        COMPONENT,
        record["sub_component"],
        MANUFACTURER,
        record.get("model", ""),
        record["name"],
        record.get("part", ""),
        record.get("drawing", ""),
        record.get("pos", ""),
        "",
        record.get("material", ""),
        record.get("remarks", ""),
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
    pages = list(range(START_PAGE, min(END_PAGE, len(doc)) + 1))
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
