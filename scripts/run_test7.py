import os
import re
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.append(str(SCRIPT_DIR))
sys.path.append(str((SCRIPT_DIR / "local_engine").resolve()))

from pdf_converter import pdf_page_to_image
from ocr_extractor import OCRExtractor
import run_ae as ae


PDF_PATH = PROJECT_ROOT / "test" / "Test 7" / "Auxiliary engine spare parts 1.pdf"
TEMPLATE_PATH = PROJECT_ROOT / "template" / "Spares_Capture_Template_Ver12 2.xlsm"
OUTPUT_PATH = PROJECT_ROOT / "output" / "Test7_AE_spares_extracted.xlsm"

START_PAGE = 6
END_PAGE = 62

TABLE_NO_RE = re.compile(r"^\d{2}-\d{2}$")
OLD_PART_RE = re.compile(r"(?:\d{0,2})?((?:0|5|6|8)\d\.\d{5}-\d{4})")
GENERIC_HEADER_WORDS = {
    "CRANKCASE", "MAIN BEARING COVER", "GROUP - PLATE", "GROUPE - PLANCHE",
    "GRUPPO - TAVOLA", "GRUPO - LAMINA", "D 2842", "D2842", "LE",
}


def ocr_items(page_image, extractor):
    h, w = page_image.shape[:2]
    items = []
    for box, (text, conf) in extractor.extract_text(page_image):
        text = ae.clean_text(text)
        if not text:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        items.append({
            "text": text,
            "x0": min(xs),
            "x1": max(xs),
            "y0": min(ys),
            "y1": max(ys),
            "cx": (min(xs) + max(xs)) / 2,
            "cy": (min(ys) + max(ys)) / 2,
            "rel_x": ((min(xs) + max(xs)) / 2) / w,
            "rel_y": ((min(ys) + max(ys)) / 2) / h,
            "conf": conf,
        })
    return items


def group_rows(items, y_tolerance=18):
    filtered = [item for item in items if 0.08 <= item["rel_y"] <= 0.93]
    filtered.sort(key=lambda item: (item["cy"], item["cx"]))
    rows = []
    current = []
    current_y = None

    for item in filtered:
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


def row_text(row):
    return ae.clean_text(" ".join(item["text"] for item in row))


def english_tokens(row):
    return [item["text"] for item in row if 0.43 <= item["rel_x"] <= 0.57]


def german_tokens(row):
    return [item["text"] for item in row if 0.18 <= item["rel_x"] <= 0.29]


def size_tokens(row):
    return [item["text"] for item in row if 0.29 <= item["rel_x"] <= 0.39]


def qty_tokens(row):
    return [item["text"] for item in row if 0.39 <= item["rel_x"] <= 0.45]


def part_tokens(row):
    return [item["text"] for item in row if 0.05 <= item["rel_x"] <= 0.18]


def pos_tokens(row):
    return [item["text"] for item in row if 0.01 <= item["rel_x"] <= 0.05]


def left_tokens(row):
    return [item["text"] for item in row if item["rel_x"] <= 0.42]


def normalize_old_part_no(text):
    text = ae.clean_text(text).replace(" ", "")
    text = text.replace("O6.", "06.").replace("o6.", "06.")
    if text.startswith("306."):
        text = "06." + text[4:]
    if text.startswith("51.0410-"):
        text = text.replace("51.0410-", "51.04410-", 1)
    return text


def find_old_part_no(text):
    compact = normalize_old_part_no(text)
    match = OLD_PART_RE.search(compact)
    if not match:
        return ""
    return normalize_old_part_no(match.group(1))


def find_pos_no(text, part_no):
    compact = ae.clean_text(text).replace(" ", "")
    if not part_no:
        return ""
    idx = compact.find(part_no)
    prefix = compact[:idx]
    match = re.search(r"(\d{1,3})$", prefix)
    if not match:
        return ""
    value = match.group(1)
    return value if value.isdigit() and int(value) < 200 else ""


def page_context(rows, last_subcomponent, last_table_no):
    subcomponent = last_subcomponent
    table_no = last_table_no

    first_data_idx = len(rows)
    for idx, row in enumerate(rows[:24]):
        if find_old_part_no(" ".join(left_tokens(row))):
            first_data_idx = idx
            break

    header_rows = rows[:max(first_data_idx, 1)]

    for row in header_rows:
        for item in row:
            if TABLE_NO_RE.match(item["text"]):
                table_no = item["text"]

    engine_idx = None
    for idx, row in enumerate(header_rows):
        text = row_text(row).upper()
        if "D2842" in text or "D 2842" in text:
            engine_idx = idx
            break

    candidate_rows = header_rows[engine_idx + 1: engine_idx + 5] if engine_idx is not None else header_rows
    for row in candidate_rows:
        english = ae.clean_text(" ".join(english_tokens(row))).upper()
        if not english:
            continue
        if any(ch.isdigit() for ch in english):
            continue
        if english in GENERIC_HEADER_WORDS:
            continue
        if "GROUP" in english or "GRUPPO" in english or "GROUPE" in english or "GRUPO" in english:
            continue
        if len(english) < 3:
            continue
        subcomponent = english
        break

    if not subcomponent:
        for row in header_rows:
            text = row_text(row).upper()
            if "CRANKCASE" in text:
                subcomponent = "CRANKCASE"
                break
    return subcomponent, table_no


def extract_page_rows(rows, page_no, subcomponent, table_no):
    extracted = []
    current = None

    for row in rows:
        left_text = " ".join(left_tokens(row))
        part_no = find_old_part_no(left_text)
        if part_no:
            if current:
                extracted.append(current)

            pos = find_pos_no(left_text, part_no) or ae.clean_text(" ".join(pos_tokens(row)))
            qty = ae.clean_text(" ".join(qty_tokens(row)))
            name_parts = []
            english = ae.clean_text(" ".join(english_tokens(row)))
            german = ae.clean_text(" ".join(german_tokens(row)))
            size = ae.clean_text(" ".join(size_tokens(row)))

            if english:
                name_parts.append(english)
            elif german:
                name_parts.append(german)
            if size and english:
                name_parts.append(size)

            current = {
                "page_no": page_no,
                "sub_component": subcomponent,
                "table_no": table_no,
                "pos_no": pos if pos.isdigit() else "",
                "mfg_part_no": ae.correct_part_no(part_no),
                "qty": qty,
                "name_text": ae.clean_text(" ".join(name_parts)),
            }
            continue

        if not current:
            continue

        continuation = ae.clean_text(" ".join(english_tokens(row)))
        german_more = ae.clean_text(" ".join(german_tokens(row)))
        size_more = ae.clean_text(" ".join(size_tokens(row)))
        pos = ae.clean_text(" ".join(pos_tokens(row)))

        if pos == "-" and "REPLACED" in row_text(row).upper():
            continue

        if continuation:
            current["name_text"] = ae.clean_text(f"{current['name_text']} {continuation}")
        elif german_more and not current["name_text"]:
            current["name_text"] = german_more
        elif size_more and any(ch.isdigit() for ch in size_more):
            current["name_text"] = ae.clean_text(f"{current['name_text']} {size_more}")

    if current:
        extracted.append(current)
    return extracted


def to_template_row(record):
    name, size, material = ae.PART_NAME_OVERRIDES.get(
        record["mfg_part_no"],
        ae.split_spare_name_details(record["name_text"]),
    )
    subcomponent = record["sub_component"] or "SUBCOMPONENT"
    return [
        ae.COMPONENT_NAME,
        subcomponent,
        ae.MANUFACTURER,
        ae.MODEL,
        name,
        record["mfg_part_no"],
        "",
        record["pos_no"],
        size,
        material,
        "",
        record["table_no"],
        record["page_no"],
        ae.extracted_pdf_name(subcomponent),
        "",
        ae.DEFAULT_UOM,
        "",
        "",
        ae.DRAWING_PAGE_WITH_POS,
        "",
        "",
    ]


def main():
    extractor = OCRExtractor()
    context_subcomponent = ""
    context_table_no = ""
    rows = []

    for page_no in range(START_PAGE, END_PAGE + 1):
        print(f"Processing page {page_no}/{END_PAGE}...")
        image = pdf_page_to_image(str(PDF_PATH), page_no - 1, dpi=200)
        items = ocr_items(image, extractor)
        page_rows = group_rows(items)
        subcomponent, table_no = page_context(page_rows, context_subcomponent, context_table_no)
        context_subcomponent = subcomponent
        context_table_no = table_no
        rows.extend(extract_page_rows(page_rows, page_no, subcomponent, table_no))

    output_rows = [to_template_row(record) for record in rows]
    os.makedirs(OUTPUT_PATH.parent, exist_ok=True)
    saved_path = ae.write_to_excel(output_rows, str(TEMPLATE_PATH), str(OUTPUT_PATH))
    print(f"Extracted rows: {len(output_rows)}")
    print(f"Output: {saved_path}")


if __name__ == "__main__":
    main()
