from pathlib import Path
import re

import fitz

import run_test12 as base


PROJECT_ROOT = Path(__file__).resolve().parent.parent

base.PDF_PATH = PROJECT_ROOT / "test" / "Test 13" / "M-212-M0000011-Centrifugal Pump REV1.1 (3).pdf"
base.OUTPUT_PATH = PROJECT_ROOT / "output" / "Test13_Centrifugal_Pump_extracted.xlsm"
base.COMPONENT = "Centrifugal Pump"
base.MANUAL_PDF_NAME = "M-212-M0000011-Centrifugal Pump REV1.1 (3).pdf"

PAGE_NUMBERS = [
    4, 5, 6,
    11, 12, 13, 14, 15,
    16, 17, 18, 19,
    20, 21, 22, 23, 24,
    25, 26, 27,
    28, 29, 30, 31,
    32, 33, 34, 35,
    36, 37,
    38, 39, 40, 41,
    42, 44, 45,
    46, 47, 48,
    49, 50, 51,
    52, 53, 54,
    55, 56, 57,
    58, 59, 60,
    61, 62, 63,
    68, 69, 70,
    71, 72,
    73, 74, 75,
    76, 79, 80, 82, 130,
]


def clean_join(parts):
    return base.clean_text(" ".join(part for part in parts if part))


def text_in(items, x0, x1, y0, y1):
    return clean_join(
        item["text"]
        for item in sorted(items, key=lambda item: (item["rel_y0"], item["rel_x0"]))
        if x0 <= item["rel_x0"] <= x1 and y0 <= item["rel_y0"] <= y1
    )


def normalize_quantity(text):
    text = base.clean_text(text).upper()
    text = text.replace("ISET", "1SET").replace("LSET", "1SET")
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_material(text):
    text = base.clean_text(text).upper()
    fixes = {
        "STEEI": "STEEL",
        "DUCT ILE": "DUCTILE",
        "CAST IRQN": "CAST IRON",
        "COCCOPPER": "COPPER",
        "PPER ALLOY": "COPPER ALLOY",
        "OPPER ALLOY": "COPPER ALLOY",
        "SYNTHETIC RESIN": "SYNTHETIC RESIN",
    }
    for src, dst in fixes.items():
        text = text.replace(src, dst)
    text = (
        text.replace("COCCOPPER", "COPPER")
        .replace("COCOPPER", "COPPER")
        .replace("CCOPPER", "COPPER")
        .replace("OCCOPPER", "COPPER")
        .replace("OCOPPER", "COPPER")
    )
    return text


def detect_common_metadata(items, fallback_subcomponent=""):
    text = base.page_text(items)
    drawing = ""
    for item in items:
        value = base.clean_text(item["text"]).upper()
        if base.DRAWING_RE.fullmatch(value):
            drawing = value
            break
    if not drawing:
        match = base.DRAWING_RE.search(text.replace(" ", ""))
        drawing = match.group(0).upper() if match else ""

    model = base.detect_model(items, text)

    known_title = re.search(
        r"\b(HORIZONTAL VACUUM PUMP|AIR EJECTOR UNIT|CHECK VALVE|SOLENOID VALVE|GAUGE BOARD|MECHANICAL SEAL)\b",
        text,
        re.IGNORECASE,
    )
    if known_title:
        return base.titleish(known_title.group(1)), model, drawing

    title_lines = []
    for line in base.group_lines([
        item for item in items
        if 0.20 <= item["rel_x0"] <= 0.78 and 0.04 <= item["rel_y0"] <= 0.14
    ], y_tolerance=0.012):
        line_value = base.line_text(line)
        line_value = re.sub(r"\bMODEL\s*:.*$", "", line_value, flags=re.IGNORECASE)
        line_value = re.sub(r"\bDATE\b.*$", "", line_value, flags=re.IGNORECASE)
        line_value = re.sub(r"\bDRAWING\s*NO\.?\b.*$", "", line_value, flags=re.IGNORECASE)
        line_value = base.clean_text(line_value)
        if not line_value:
            continue
        if re.search(r"\b(?:OUTLINE|SECTIONAL|DRAWING|MATERIAL|PARTS|LIST|NANIWA|DATE)\b", line_value, re.IGNORECASE):
            continue
        title_lines.append(line_value)

    subcomponent = base.titleish(" ".join(title_lines))
    if not subcomponent or re.fullmatch(r"(?:VERTICAL|HORIZONTAL)\s+CENTRIFUGAL\s+PUMP", subcomponent, re.IGNORECASE):
        subcomponent = fallback_subcomponent
    return subcomponent or base.COMPONENT, model, drawing


def accessory_rows(items, page_no):
    text = base.page_text(items).upper()
    if "ACCESSORIES FOR EACH 1 PUMP" not in text:
        return [], None

    subcomponent = text_in(items, 0.68, 0.96, 0.705, 0.775)
    subcomponent = re.sub(r"^PUMP NAME\s*", "", subcomponent, flags=re.IGNORECASE)
    subcomponent = base.titleish(subcomponent)
    model = text_in(items, 0.80, 0.96, 0.765, 0.795)
    model = base.normalize_model(model)
    drawing = text_in(items, 0.80, 0.96, 0.865, 0.895).upper()
    drawing_match = base.DRAWING_RE.search(drawing)
    drawing = drawing_match.group(0) if drawing_match else drawing

    candidates = [
        item for item in items
        if 0.09 <= item["rel_x0"] <= 0.335
        and 0.745 <= item["rel_y0"] <= 0.955
        and not re.search(r"^(?:DESCRIPTION|ACCESSORIES|Q'? ?TY)$", item["text"], re.IGNORECASE)
    ]
    rows = []
    for line in base.group_lines(candidates, y_tolerance=0.012):
        name = base.clean_text(" ".join(
            item["text"] for item in sorted(line, key=lambda item: item["rel_x0"])
            if item["rel_x0"] < 0.335
        ))
        if not name or len(name) < 2:
            continue
        if re.search(r"\b(?:SPECIFICATION|SUCTION|DELIVERY|CAPACITY|TOTAL|RULE|SCALE|DRAWING|REMARKS)\b", name, re.IGNORECASE):
            continue
        y_mid = sum(item["rel_cy"] for item in line) / len(line)
        qty = text_in(items, 0.335, 0.39, y_mid - 0.012, y_mid + 0.014)
        rows.append({
            "sub_component": subcomponent or base.COMPONENT,
            "model": model,
            "drawing": drawing,
            "page_no": page_no,
            "pos": str(len(rows) + 1),
            "name": base.normalize_spare_name(name),
            "part_no": "",
            "material": "",
            "work_qty": normalize_quantity(qty),
            "spare_qty": "",
            "remarks": "Accessories for each 1 pump",
        })
    return rows, {"sub_component": subcomponent, "model": model, "drawing": drawing}


def material_list_side_rows(items, page_no, side, context):
    if side == "left":
        part_range = (0.095, 0.14)
        name_range = (0.145, 0.285)
        material_range = (0.285, 0.485)
        qty_range = (0.485, 0.525)
    else:
        part_range = (0.525, 0.57)
        name_range = (0.57, 0.715)
        material_range = (0.715, 0.91)
        qty_range = (0.91, 0.965)

    footer_headers = [
        item["rel_y0"] for item in items
        if item["rel_y0"] > 0.50
        and re.search(r"\b(?:NAME OF PART|MATERIAL)\b", item["text"], re.IGNORECASE)
    ]
    header_y = max(footer_headers) if footer_headers else 0.94
    small_part_top = max(header_y - 0.19, 0.18)

    anchors = []
    for item in items:
        part_no = material_part_no_text(item["text"])
        if not part_no:
            continue
        if not (part_range[0] <= item["rel_x0"] <= part_range[1]):
            continue
        if not (0.18 <= item["rel_y0"] <= 0.94):
            continue
        if len(part_no) < 3 and item["rel_y0"] < small_part_top:
            continue
        anchors.append(item)
    anchors.sort(key=lambda item: item["rel_cy"])
    rows = []
    for idx, anchor in enumerate(anchors):
        prev_y = anchors[idx - 1]["rel_cy"] if idx else max(anchor["rel_cy"] - 0.04, 0.16)
        next_y = anchors[idx + 1]["rel_cy"] if idx + 1 < len(anchors) else min(anchor["rel_cy"] + 0.04, 0.96)
        top = max((prev_y + anchor["rel_cy"]) / 2, 0.16)
        bottom = min((anchor["rel_cy"] + next_y) / 2, 0.965)
        if idx == 0:
            top = max(anchor["rel_cy"] - 0.035, 0.16)
        band = [item for item in items if top <= item["rel_cy"] < bottom]

        anchor_text = base.clean_text(anchor["text"])
        part_no = material_part_no_text(anchor_text)
        suffix = re.sub(r"^\D*\d{3,4}", "", anchor_text).strip()
        name = clean_join([
            suffix,
            text_in(band, name_range[0], name_range[1], 0.0, 1.0),
        ])
        name = re.sub(rf"^{re.escape(part_no)}\s+", "", name)
        material = clean_join([
            text_in(band, material_range[0], material_range[1], 0.0, 1.0),
        ])
        qty = text_in(band, qty_range[0], qty_range[1], 0.0, 1.0)

        if not part_no or not name or re.search(r"\bNAME OF PART\b", name, re.IGNORECASE):
            continue
        rows.append({
            "sub_component": context["sub_component"],
            "model": context["model"],
            "drawing": context["drawing"],
            "page_no": page_no,
            "pos": part_no,
            "name": base.normalize_spare_name(name),
            "part_no": part_no,
            "material": normalize_material(material),
            "work_qty": normalize_quantity(qty),
            "spare_qty": "",
            "remarks": "Material list",
        })
    return rows


def material_list_rows(items, page_no, fallback_context):
    text = base.page_text(items).upper()
    has_material_table = "NAME OF PART" in text and "MATERIAL" in text
    if not has_material_table:
        return [], None

    detected_sub, detected_model, detected_drawing = detect_common_metadata(
        items,
        fallback_context.get("sub_component", ""),
    )
    detected_model = detected_model if detected_model and not fallback_context.get("model", "").startswith(detected_model) else fallback_context.get("model", detected_model)
    context = {
        "sub_component": detected_sub or fallback_context.get("sub_component", base.COMPONENT),
        "model": detected_model or fallback_context.get("model", ""),
        "drawing": detected_drawing or fallback_context.get("drawing", ""),
    }
    rows = material_list_side_rows(items, page_no, "left", context)
    rows.extend(material_list_side_rows(items, page_no, "right", context))
    rows.sort(key=lambda record: (int(record["part_no"]) if record["part_no"].isdigit() else 9999, record["name"]))
    return rows, context


def split_part_name(text):
    value = base.clean_text(text)
    o_ring_match = re.match(r"^\D*(\d{3})[0O][- ]?RING(.*)$", value, re.IGNORECASE)
    if o_ring_match:
        return o_ring_match.group(1), base.clean_text(f"O-RING {o_ring_match.group(2)}")
    match = re.match(r"^\D*(\d{1,4})(.*)$", value)
    if not match:
        return "", value
    return match.group(1), base.clean_text(match.group(2))


def material_part_no_text(text):
    match = re.search(r"\d{1,4}", base.clean_text(text))
    return match.group(0) if match else ""


def side_material_list_rows(items, page_no, fallback_context):
    text = base.page_text(items).upper()
    if "NAME OF PART" not in text or "MATERIAL" not in text:
        return [], None

    detected_sub, detected_model, detected_drawing = detect_common_metadata(
        items,
        fallback_context.get("sub_component", ""),
    )
    context = {
        "sub_component": detected_sub or fallback_context.get("sub_component", base.COMPONENT),
        "model": detected_model or fallback_context.get("model", ""),
        "drawing": detected_drawing or fallback_context.get("drawing", ""),
    }

    anchors = [
        item for item in items
        if 0.095 <= item["rel_x0"] <= 0.155
        and 0.09 <= item["rel_y0"] <= 0.56
        and len(material_part_no_text(item["text"])) >= 3
    ]
    anchors.sort(key=lambda item: item["rel_cy"])
    rows = []
    for idx, anchor in enumerate(anchors):
        prev_y = anchors[idx - 1]["rel_cy"] if idx else max(anchor["rel_cy"] - 0.025, 0.08)
        next_y = anchors[idx + 1]["rel_cy"] if idx + 1 < len(anchors) else min(anchor["rel_cy"] + 0.025, 0.58)
        top = max((prev_y + anchor["rel_cy"]) / 2, 0.08)
        bottom = min((anchor["rel_cy"] + next_y) / 2, 0.58)
        if idx == 0:
            top = max(anchor["rel_cy"] - 0.012, 0.08)
        band = [item for item in items if top <= item["rel_cy"] < bottom and item["rel_x0"] <= 0.40]

        part_no, suffix = split_part_name(anchor["text"])
        name = clean_join([suffix, text_in(band, 0.135, 0.235, 0.0, 1.0)])
        material = clean_join([
            text_in(band, 0.235, 0.31, 0.0, 1.0),
            text_in(band, 0.31, 0.37, 0.0, 1.0),
        ])
        qty = text_in(band, 0.365, 0.405, 0.0, 1.0)

        if not part_no or not name or re.search(r"\bNAME OF PART\b", name, re.IGNORECASE):
            continue
        rows.append({
            "sub_component": context["sub_component"],
            "model": context["model"],
            "drawing": context["drawing"],
            "page_no": page_no,
            "pos": part_no,
            "name": base.normalize_spare_name(name),
            "part_no": part_no,
            "material": normalize_material(material),
            "work_qty": normalize_quantity(qty),
            "spare_qty": "",
            "remarks": "Material list",
        })
    return rows, context


def robust_spare_list_rows(items, page_no):
    text = base.page_text(items)
    if "SPARE PARTS LIST" not in text.upper():
        return []
    if "ADDITIONAL" in text.upper():
        return base.spare_list_rows(items, page_no)

    subcomponent, model, drawing = base.detect_metadata(items)
    anchors = [
        item for item in items
        if 0.775 <= item["rel_x0"] <= 0.85
        and 0.20 <= item["rel_y0"] <= 0.82
        and base.part_no_text(item["text"])
    ]
    anchors.sort(key=lambda item: item["rel_cy"])

    rows = []
    for idx, anchor in enumerate(anchors):
        prev_y = anchors[idx - 1]["rel_cy"] if idx else max(anchor["rel_cy"] - 0.07, 0.18)
        next_y = anchors[idx + 1]["rel_cy"] if idx + 1 < len(anchors) else min(anchor["rel_cy"] + 0.08, 0.84)
        top = max((prev_y + anchor["rel_cy"]) / 2, 0.18)
        bottom = min((anchor["rel_cy"] + next_y) / 2, 0.84)
        if idx == 0:
            top = max(anchor["rel_cy"] - 0.055, 0.18)
        band = [item for item in items if top <= item["rel_cy"] < bottom]

        part_no = base.part_no_text(anchor["text"])
        name = base.normalize_spare_name(text_in(band, 0.10, 0.46, 0.0, 1.0))
        material = normalize_material(text_in(band, 0.535, 0.635, 0.0, 1.0))
        work_qty = normalize_quantity(text_in(band, 0.635, 0.675, 0.0, 1.0))
        spare_qty = normalize_quantity(text_in(band, 0.675, 0.725, 0.0, 1.0))
        remarks = base.titleish(text_in(band, 0.83, 0.98, 0.0, 1.0))
        if not part_no or not name:
            continue
        rows.append({
            "sub_component": subcomponent,
            "model": model,
            "drawing": drawing,
            "page_no": page_no,
            "pos": str(len(rows) + 1),
            "name": name,
            "part_no": part_no,
            "material": material,
            "work_qty": work_qty,
            "spare_qty": spare_qty,
            "remarks": remarks,
        })
    return rows


def extract_page(items, page_no, context):
    rows = robust_spare_list_rows(items, page_no)
    if rows:
        return rows, context

    rows, new_context = side_material_list_rows(items, page_no, context)
    if len(rows) >= 5:
        return rows, new_context or context

    rows, new_context = material_list_rows(items, page_no, context)
    if rows:
        return rows, new_context or context

    rows, new_context = accessory_rows(items, page_no)
    if rows:
        return rows, new_context or context

    return [], context


def main():
    doc = fitz.open(base.PDF_PATH)
    extractor = base.OCRExtractor()
    records = []
    context = {"sub_component": "", "model": "", "drawing": ""}
    pages = [page_no for page_no in sorted(set(PAGE_NUMBERS)) if 1 <= page_no <= len(doc)]
    for page_no in pages:
        items = base.ocr_items(base.PDF_PATH, page_no - 1, extractor)
        page_records, context = extract_page(items, page_no, context)
        records.extend(page_records)
        print(f"Page {page_no}: {len(page_records)} rows")
    doc.close()

    rows = [base.to_template_row(record) for record in records]
    saved_path = base.write_workbook(rows)
    print(f"Rows written: {len(rows)}")
    print(f"Output: {saved_path}")


if __name__ == "__main__":
    main()
