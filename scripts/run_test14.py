from pathlib import Path
import re

import fitz
import openpyxl

import run_test9 as base


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PDF_PATH = PROJECT_ROOT / "test" / "Test 14" / "13k obp spare - full list rev 4 (2) (1) (1).pdf"
OUTPUT_PATH = PROJECT_ROOT / "output" / "Test14_OBP_spares_extracted.xlsm"
TEMPLATE_PATH = base.TEMPLATE_PATH

COMPONENT = "OBP Spare List"
MODEL = base.MODEL
MANUAL_PDF_NAME = "13k obp spare - full list rev 4 (2) (1) (1).pdf"
DEFAULT_UOM = base.DEFAULT_UOM

PAGE_NUMBERS = [
    84, 86, 88, 90, 92, 94, 96, 98, 100, 102, 104, 106,
    108, 109, 110, 111, 112, 113,
    115, 116, 117, 118, 119, 120, 121,
    123, 125, 127, 129, 131, 134, 136, 138, 140, 142, 144, 146, 148, 150,
]


def clean_text(text):
    return base.clean_text(text)


def titleish(text):
    return base.titleish(text)


def extracted_pdf_name(subcomponent):
    token = re.sub(r"[^A-Za-z0-9]+", "", subcomponent)
    return f"OBP_{token or 'Spares'}.pdf"


def record(page_no, subcomponent, name, part="", qty="", material="", remarks="", drawing="", pos=""):
    name = titleish(name)
    if not name:
        return None
    return {
        "component": COMPONENT,
        "sub_component": titleish(subcomponent) or "OBP Spare List",
        "manufacturer": "",
        "model": MODEL,
        "name": name,
        "part": clean_text(part),
        "drawing": clean_text(drawing),
        "pos": clean_text(pos),
        "size": "",
        "material": clean_text(material).upper(),
        "remarks": clean_text(remarks),
        "details": f"Qty: {clean_text(qty)}" if clean_text(qty) else "",
        "page": page_no,
        "manual": MANUAL_PDF_NAME,
        "uom": DEFAULT_UOM,
        "pdf": extracted_pdf_name(subcomponent),
    }


def page_lines(items):
    return [base.line_text(line) for line in base.group_lines(items, y_tolerance=0.008)]


def title_from_lines(lines, fallback="OBP Spare List"):
    for line in lines[:8]:
        if line.startswith("PIL 13K TEU Hudong Shipyard New Builds "):
            tail = line.replace("PIL 13K TEU Hudong Shipyard New Builds", "", 1)
            tail = re.sub(r"\b(?:OBP|Standard)\s+Spares?\b.*$", "", tail, flags=re.IGNORECASE)
            tail = re.sub(r"\b(?:OBP|Standard)\s+Spare\s+List\b.*$", "", tail, flags=re.IGNORECASE)
            return clean_text(tail) or fallback
        if "PIL 13K TEU" in line and " - " in line:
            tail = line.split(" - ", 1)[1]
            tail = re.sub(r"\b(?:OBP|Standard)\s+Spares?\b.*$", "", tail, flags=re.IGNORECASE)
            tail = re.sub(r"\b(?:OBP|Standard)\s+Spare\s+List\b.*$", "", tail, flags=re.IGNORECASE)
            return clean_text(tail) or fallback
        match = re.search(r"\)\s+(.+)$", line)
        if "PIL 13K TEU" in line and match:
            tail = match.group(1)
            tail = re.sub(r"\b(?:OBP|Standard)\s+Spares?\b.*$", "", tail, flags=re.IGNORECASE)
            tail = re.sub(r"\b(?:OBP|Standard)\s+Spare\s+List\b.*$", "", tail, flags=re.IGNORECASE)
            return clean_text(tail) or fallback
    for line in lines[:8]:
        if re.search(r"\b(?:SPARE|PARTS|LIST|PAGE|PROJECT|TITLE)\b", line, re.IGNORECASE):
            continue
        if len(line) > 4:
            return clean_text(line)
    return fallback


def parse_part_qty_description(items, page_no):
    lines = page_lines(items)
    text = "\n".join(lines).upper()
    if "PART NUMBER" not in text or "QUANTITY PER SHIPSET" not in text or "DESCRIPTION" not in text:
        return []
    subcomponent = title_from_lines(lines, "Fresh Water Generator")
    rows = []
    in_table = False
    for line in lines:
        if "PART NUMBER" in line.upper() and "DESCRIPTION" in line.upper():
            in_table = True
            continue
        if not in_table:
            continue
        match = re.match(r"^(.+?)\s+(\d+(?:\.\d+)?)\s+(.+)$", line)
        if not match:
            continue
        part, qty, name = match.groups()
        rows.append(record(page_no, subcomponent, name, part=part, qty=qty, pos=str(len(rows) + 1)))
    return [row for row in rows if row]


def parse_category_description(items, page_no):
    lines = page_lines(items)
    text = "\n".join(lines).upper()
    if "CATEGORY" not in text or "QTY/SHIP" not in text or "DESCRIPTION" not in text:
        return []
    subcomponent = title_from_lines(lines, "CCTV")
    rows = []
    in_table = False
    for line in lines:
        if "CATEGORY" in line.upper() and "DESCRIPTION" in line.upper():
            in_table = True
            continue
        if not in_table:
            continue
        match = re.match(r"^(.+?)\s+(\d+(?:\.\d+)?)\s+([A-Za-z.]+)$", line)
        if not match:
            continue
        prefix, qty, unit = match.groups()
        category = ""
        for candidate in ["Electrical Machinery", "LAN cable", "Wiper", "Jumper", "Camera"]:
            if prefix.lower().startswith(candidate.lower() + " "):
                category = candidate
                name = prefix[len(candidate):].strip()
                break
        if not category:
            parts = prefix.split(" ", 1)
            category = parts[0]
            name = parts[1] if len(parts) > 1 else prefix
        row = record(
            page_no,
            subcomponent,
            name,
            qty=qty,
            remarks=f"Category: {category}; Unit: {unit}",
            pos=str(len(rows) + 1),
        )
        if row:
            rows.append(row)
    return rows


def parse_description_qty(items, page_no):
    lines = page_lines(items)
    text = "\n".join(lines).upper()
    if "DESCRIPTION" not in text or "QTY / SHIP" not in text:
        return []
    subcomponent = title_from_lines(lines, "OBP Spares")
    rows = []
    in_table = False
    for line in lines:
        upper = line.upper()
        if "DESCRIPTION" in upper and "QTY" in upper:
            in_table = True
            continue
        if not in_table:
            continue
        if re.search(r"\bSTANDARD SPARES\b|\bSPARE PARTS\b", upper):
            break
        match = re.match(r"^(.+?)\s+(\d+(?:\.\d+)?)$", line)
        if not match:
            continue
        name, qty = match.groups()
        row = record(page_no, subcomponent, name, qty=qty, pos=str(len(rows) + 1))
        if row:
            rows.append(row)
    return rows


def parse_sludge_checker(items, page_no):
    text = base.page_text(items).upper()
    if "SPARE PARTS L" not in text or not re.search(r"\b(?:AUTO\. FILTER|BY-PASS FILTER)\b", text):
        return []
    subcomponent = "Sludge Checker"
    for line in page_lines(items):
        if "AUTO. FILTER" in line.upper() or "BY-PASS FILTER" in line.upper():
            subcomponent = clean_text(line.replace("BOX NO.", ""))
            break

    rows = []
    for item in items:
        if not (0.16 <= item["rel_x"] <= 0.34 and 0.20 <= item["rel_y"] <= 0.50):
            continue
        name = item["text"]
        if not re.search(r"[O0][- ]?RING|GASKET|SEAL|PACKING", name, re.IGNORECASE):
            continue
        y = item["rel_y"]
        band = [candidate for candidate in items if abs(candidate["rel_y"] - y) <= 0.025]
        material = base.join_col(band, 0.50, 0.60)
        qty = base.first_num_col(band, 0.60, 0.72)
        part = base.join_col(band, 0.72, 0.83)
        remarks = base.join_col(band, 0.83, 0.98)
        row = record(
            page_no,
            subcomponent,
            name.replace("0-", "O-"),
            part=part,
            qty=qty,
            material=material,
            remarks=remarks,
            pos=str(len(rows) + 1),
        )
        if row:
            rows.append(row)
    return rows[:1]


def parse_kangrim_table(items, page_no):
    text = base.page_text(items).upper()
    if "KANGRIM" not in text or "SPEC./MATERIAL" not in text:
        return []
    subcomponent = "Kangrim Boiler Spares"
    footer = base.join_col(items, 0.25, 0.75)
    if "ECONOMIZER" in footer.upper():
        subcomponent = "Kangrim Economizer Spares"
    elif "BOILER" in footer.upper():
        subcomponent = "Kangrim Exhaust Gas Boiler Spares"

    starts = [
        item for item in items
        if 0.06 <= item["rel_x"] <= 0.13
        and 0.20 <= item["rel_y"] <= 0.90
        and re.fullmatch(r"\d{1,3}", clean_text(item["text"]))
    ]
    starts.sort(key=lambda item: item["rel_y"])
    rows = []
    for idx, start in enumerate(starts):
        next_y = starts[idx + 1]["rel_y"] if idx + 1 < len(starts) else 0.88
        band = [item for item in items if start["rel_y"] - 0.03 <= item["rel_y"] < next_y - 0.004]
        name = base.join_col(band, 0.10, 0.28)
        material = base.join_col(band, 0.50, 0.66)
        qty = base.first_num_col(band, 0.76, 0.84)
        remarks = base.join_col(band, 0.86, 0.98)
        part = ""
        code_match = re.search(r"\b\d{5,}\b|[A-Z]{1,4}-[A-Z0-9-]+", material)
        if code_match:
            part = code_match.group(0)
        row = record(
            page_no,
            subcomponent,
            name,
            part=part,
            qty=qty,
            material=material,
            remarks=remarks,
            pos=start["text"],
        )
        if row:
            rows.append(row)
    return rows


def parse_kangrim_additional(items, page_no):
    lines = page_lines(items)
    text = "\n".join(lines).upper()
    if "ADDITIONAL SPARE PARTS LIST" not in text or "KANGRIM" not in text:
        return []
    subcomponent = "Kangrim Spares"
    for line in lines:
        if "Exhaust Gas Boiler" in line:
            subcomponent = "Kangrim Exhaust Gas Boiler Spares"
            break
        if "Exhaust Gas Economizer" in line:
            subcomponent = "Kangrim Exhaust Gas Economizer Spares"
            break
    rows = []
    in_table = False
    for line in lines:
        if re.search(r"\bNo\s+Description\b", line, re.IGNORECASE):
            in_table = True
            continue
        if not in_table:
            continue
        if line.startswith("Form_") or "Copyright" in line:
            break
        match = re.match(r"^(\d+)\s+(.+?)\s+(\d+)(?:\s+(.+))?$", line)
        if not match:
            continue
        pos, name, qty, remarks = match.groups()
        row = record(
            page_no,
            subcomponent,
            name,
            qty=qty,
            remarks=remarks or "",
            pos=pos,
        )
        if row:
            rows.append(row)
    return rows


def custom_extract(items, page_no):
    for parser in [
        parse_kangrim_additional,
        parse_kangrim_table,
        parse_part_qty_description,
        parse_category_description,
        parse_description_qty,
        parse_sludge_checker,
    ]:
        rows = parser(items, page_no)
        if rows:
            return rows
    return []


def base_extract(items, page_no):
    rows = base.extract_page(items, page_no)
    for row in rows:
        row["component"] = COMPONENT
        row["manual"] = MANUAL_PDF_NAME
        row["pdf"] = extracted_pdf_name(row["sub_component"])
    return rows


def to_template_row(item):
    return [
        item["component"],
        item["sub_component"],
        item["manufacturer"],
        item["model"],
        item["name"],
        item["part"],
        item["drawing"],
        item["pos"],
        item["size"],
        item["material"],
        item["remarks"],
        item["details"],
        item["page"],
        item["manual"],
        "",
        item["uom"],
        item["pdf"],
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
    extractor = base.OCRExtractor()
    records = []
    for page_no in sorted(set(PAGE_NUMBERS)):
        page = doc[page_no - 1]
        items = base.embedded_items(page)
        mode = "embedded"
        if len(items) < 8:
            items = base.ocr_items(PDF_PATH, page_no - 1, extractor)
            mode = "ocr"
        page_records = custom_extract(items, page_no)
        if not page_records:
            page_records = base_extract(items, page_no)
        records.extend(page_records)
        print(f"Page {page_no}: {len(page_records)} rows ({mode})")
    doc.close()

    rows = [to_template_row(item) for item in records]
    saved_path = write_workbook(rows)
    print(f"Rows written: {len(rows)}")
    print(f"Output: {saved_path}")


if __name__ == "__main__":
    main()
