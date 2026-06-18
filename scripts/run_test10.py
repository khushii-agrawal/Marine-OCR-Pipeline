import os
import re
import sys
from pathlib import Path

import cv2
import fitz
import openpyxl


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.append(str(SCRIPT_DIR / "local_engine"))

from ocr_extractor import OCRExtractor
from pdf_converter import pdf_page_to_image


PDF_PATH = PROJECT_ROOT / "test" / "Test 10" / "V-202-V0000004-cargo area fans-Rev.1.1.pdf"
TEMPLATE_PATH = PROJECT_ROOT / "template" / "Spares_Capture_Template_Ver12 2.xlsm"
OUTPUT_PATH = PROJECT_ROOT / "output" / "Test10_Cargo_Area_Fans_extracted.xlsm"

COMPONENT = "Cargo Area Fans"
MANUFACTURER = "SHANGHAI HENGYUAN MARINE EQUIPMENT CO LTD"
MANUAL_PDF_NAME = "V-202-V0000004-cargo area fans-Rev.1.1.pdf"
DEFAULT_UOM = "Pcs"
EXTRACTED_PDF_NAME = "CAF_CargoAreaFans.pdf"

START_PAGE = int(os.environ.get("TEST10_START_PAGE", "1"))
END_PAGE = int(os.environ.get("TEST10_END_PAGE", "0"))

DRAWING_RE = re.compile(r"\bHF[A-Z]{2}\s*[-]?\s*\d{1,3}[A-Z]?(?:-\d)?\s*[-]?\s*(?:1[O0]2|2[O0](?:[24]?W?X|X))\b", re.IGNORECASE)
MODEL_RE = re.compile(r"\b(?:CBZ|JCZ|HFCZ)\s*[-]?\s*\d{1,3}[A-Z]?(?:-\d)?(?:\s*II)?\b", re.IGNORECASE)


def clean_text(text):
    text = re.sub(r"\s+", " ", str(text or "").strip())
    return text.strip(" -|")


def normalize_drawing(text):
    text = clean_text(text).upper().replace(" ", "")
    text = text.replace("HFBZ7OA", "HFBZ70A").replace("HF8Z", "HFBZ")
    text = text.replace("HFCZ9OC", "HFCZ90C").replace("HFCZ8OA", "HFCZ80A")
    text = text.replace("HFCZ6O", "HFCZ60").replace("HFBZ3O", "HFBZ30")
    text = text.replace("2O4WX", "204WX").replace("2O2WX", "202WX")
    text = text.replace("2O4X", "204X").replace("2O2X", "202X")
    text = text.replace("2OWX", "20WX").replace("2OX", "20X")
    text = text.replace("1O2WX", "102WX").replace("1O2", "102")
    return text


def normalize_model(text):
    text = clean_text(text).upper().replace(" ", "")
    text = text.replace("CBZ-7OA", "CBZ-70A").replace("CBZ-8OA", "CBZ-80A")
    text = text.replace("3O6-ZOR", "CBZ-90C")
    text = text.replace("JCZ-8OA", "JCZ-80A").replace("HFCZ9OC", "HFCZ90C")
    text = text.replace("HFCZ6O", "HFCZ60").replace("HFBZ3O", "HFBZ30")
    return text


def titleish(text):
    text = clean_text(text)
    if not text:
        return ""
    fixes = {
        "ASS": "ASS",
        "Q235B": "Q235B",
        "ZL104": "ZL104",
    }
    words = []
    for token in text.split():
        token = token.strip()
        key = token.upper().strip(".")
        if key in fixes:
            words.append(fixes[key])
        else:
            words.append(token[:1].upper() + token[1:].lower())
    return clean_text(" ".join(words))


def ocr_rotated_items(pdf_path, page_idx, extractor):
    image = pdf_page_to_image(str(pdf_path), page_idx, dpi=200)
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
        })
    return items


def group_lines(items, y_tolerance=18):
    rows = []
    current = []
    current_y = None
    for item in sorted(items, key=lambda item: (item["y0"], item["x0"])):
        if current_y is None or abs(item["y0"] - current_y) <= y_tolerance:
            current.append(item)
            current_y = item["y0"] if current_y is None else (current_y + item["y0"]) / 2
        else:
            current.sort(key=lambda item: item["x0"])
            rows.append(current)
            current = [item]
            current_y = item["y0"]
    if current:
        current.sort(key=lambda item: item["x0"])
        rows.append(current)
    return rows


def line_text(line):
    return clean_text(" ".join(item["text"] for item in sorted(line, key=lambda item: item["x0"])))


def page_text(items):
    return "\n".join(line_text(line) for line in group_lines(items, y_tolerance=20))


def detect_metadata(items):
    text = page_text(items)
    drawing = ""
    model = ""

    drawing_match = DRAWING_RE.search(text.replace(" ", ""))
    if drawing_match:
        drawing = normalize_drawing(drawing_match.group(0))
    else:
        for item in items:
            candidate = normalize_drawing(item["text"])
            if candidate.startswith(("HFBZ", "HFCZ", "HFCL")) and re.search(r"(?:102|20(?:[24]W?X|X))", candidate):
                drawing = candidate
                break

    model_match = MODEL_RE.search(text)
    if model_match:
        model = normalize_model(model_match.group(0))
    elif drawing:
        model_match = re.search(r"HF[BC]Z(\d{2,3}[A-Z]?)", drawing)
        if model_match:
            model = "CBZ-" + model_match.group(1)

    subcomponent = "Cargo Area Fan"
    numbered = re.findall(r"\b\d+\.\s*([A-Z0-9][A-Z0-9 /&.-]{1,42}?\s+(?:EXH|SUP)\.?)", text, re.IGNORECASE)
    if numbered:
        subcomponent = titleish(numbered[-1])
    else:
        for pattern in [
            r"\bCargo\s+hold\s+NO\.?\s*\d+\s*(?:EXH|SUP)\.?",
            r"\bPIPE\s+TUNNEL\s+SUP\.?",
            r"\b[A-Z0-9][A-Z0-9 /&.-]{1,32}\s+(?:EXH|SUP)\.?",
        ]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                subcomponent = titleish(match.group(0))
                break

    return subcomponent, model, drawing


def bom_lines(items):
    table_items = [
        item for item in items
        if 1650 <= item["x0"] <= 2255 and 185 <= item["y0"] <= 455
    ]
    lines = []
    for line in group_lines(table_items, y_tolerance=20):
        text = line_text(line)
        if not text or re.search(r"\b(?:No\.?|Code|Material|Qty|Remarks)\b", text, re.IGNORECASE):
            continue
        if known_name(text):
            lines.append(line)
    return lines


def known_name(text):
    normalized = clean_text(text).lower()
    return any(
        key in normalized
        for key in ["casing", "guide", "motor", "impeller", "cover", "cable box", "cable pipe"]
    )


def parse_bom_line(line, inferred_pos):
    text = line_text(line)
    lower = text.lower()
    pos = ""
    for item in line:
        if 1660 <= item["x0"] <= 1715 and re.fullmatch(r"\d+", item["text"]):
            pos = item["text"]
            break
    pos = pos or str(inferred_pos)

    if "cable pipe" in lower:
        name = "Cable Pipe"
        material = "ASS. ASSEMBLY"
    elif "cable box" in lower:
        name = "Cable Box"
        material = "ASS. ASSEMBLY"
    elif "cover" in lower:
        name = "Cover Plate"
        material = "Q235B STEEL PLATE"
    elif "impeller" in lower:
        name = "Impeller"
        material = "ZL104 ALUM ALLOY CAST"
    elif "motor" in lower:
        name = "Motor"
        material = "ASS. ASSEMBLY"
    elif "guide" in lower:
        name = "Guide Plate"
        material = "Q235B STEEL PLATE"
        if "q235-a" in lower or "q235a" in lower:
            material = "Q235-A STEEL PLATE"
    elif "casing" in lower:
        name = "Casing"
        material = "Q235B STEEL PLATE"
        if "q235-a" in lower or "q235a" in lower:
            material = "Q235-A STEEL PLATE"
    else:
        return None

    qty = ""
    remarks = []
    for item in line:
        if 2090 <= item["x0"] <= 2160 and re.fullmatch(r"\d+", item["text"]):
            qty = item["text"]
        elif item["x0"] > 2160:
            remarks.append(item["text"])

    return {
        "pos": pos,
        "name": name,
        "material": material,
        "qty": qty,
        "remarks": clean_text(" ".join(remarks)),
    }


def fan_spare_list_rows(items, page_no):
    text = page_text(items)
    if "FAN SPARE LIST" not in text.upper():
        return []

    starts = [
        item for item in items
        if 70 <= item["x0"] <= 150
        and 300 <= item["y0"] <= 1200
        and re.fullmatch(r"\d{1,2}", item["text"])
    ]
    starts.sort(key=lambda item: item["y0"])
    records = []
    for idx, start in enumerate(starts):
        next_y = starts[idx + 1]["y0"] if idx + 1 < len(starts) else start["y0"] + 170
        band_top = start["y0"] - 75
        band_bottom = next_y - 75
        band = [item for item in items if band_top <= item["y0"] < band_bottom]
        name_parts = [
            item["text"] for item in sorted(band, key=lambda item: (item["y0"], item["x0"]))
            if 180 <= item["x0"] <= 520 and not re.fullmatch(r"\d{4}-2RZ", item["text"], re.IGNORECASE)
        ]
        part = ""
        for item in band:
            if re.fullmatch(r"\d{4}-2RZ", item["text"], re.IGNORECASE):
                part = item["text"].upper()
                break
        qty = ""
        for item in band:
            if 1450 <= item["x0"] <= 1600 and re.fullmatch(r"\d+", item["text"]):
                qty = item["text"]
                break
        remarks = clean_text(" ".join(
            item["text"] for item in sorted(band, key=lambda item: item["x0"])
            if 1680 <= item["x0"] <= 2020
        ))
        name = clean_text(" ".join(name_parts))
        name = re.sub(r"^#+\s*(?:7K|K)?\s*", "", name, flags=re.IGNORECASE)
        name = titleish(name or "Bearing")
        if not part and "bearing" not in name.lower():
            continue
        records.append({
            "sub_component": "Fan Spare List",
            "model": "",
            "drawing": "",
            "page_no": page_no,
            "pos": start["text"],
            "name": name,
            "part_no": part,
            "material": "",
            "qty": qty,
            "remarks": remarks,
        })
    return records


def extract_page(items, page_no):
    spare_rows = fan_spare_list_rows(items, page_no)
    if spare_rows:
        return spare_rows

    subcomponent, model, drawing = detect_metadata(items)
    records = []
    seen = set()
    for idx, line in enumerate(bom_lines(items), start=1):
        parsed = parse_bom_line(line, idx)
        if not parsed:
            continue
        key = (parsed["pos"], parsed["name"])
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "sub_component": subcomponent,
            "model": model,
            "drawing": drawing,
            "page_no": page_no,
            **parsed,
        })
    return records


def to_template_row(record):
    details = f"Qty: {record['qty']}" if record["qty"] else ""
    return [
        COMPONENT,
        record["sub_component"],
        MANUFACTURER,
        record["model"],
        record["name"],
        record.get("part_no", ""),
        record["drawing"],
        record["pos"],
        "",
        record["material"],
        record["remarks"],
        details,
        record["page_no"],
        MANUAL_PDF_NAME,
        "",
        DEFAULT_UOM,
        EXTRACTED_PDF_NAME,
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
        items = ocr_rotated_items(PDF_PATH, page_no - 1, extractor)
        page_records = extract_page(items, page_no)
        records.extend(page_records)
        print(f"Page {page_no}: {len(page_records)} rows")
    doc.close()

    rows = [to_template_row(record) for record in records]
    saved_path = write_workbook(rows)
    print(f"Rows written: {len(rows)}")
    print(f"Output: {saved_path}")


if __name__ == "__main__":
    main()
