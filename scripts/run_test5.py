import os
import re
import sys
from collections import OrderedDict
from pathlib import Path

import fitz


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.append(str(SCRIPT_DIR))

import run_ae as ae


TEST_DIR = PROJECT_ROOT / "test" / "Test 5"
PDF_PATH = TEST_DIR / "Extracted pages from DCI DR 12 & 14 AE PARTS CAT D2842LE ESN 49361860994101.pdf"
TEMPLATE_PATH = PROJECT_ROOT / "template" / "Spares_Capture_Template_Ver12 2.xlsm"
OUTPUT_PATH = PROJECT_ROOT / "output" / "Test5_AE_spares_extracted.xlsm"


def group_words_into_rows(words, y_tolerance=3):
    body = [w for w in words if 40 <= w[1] <= 805]
    body.sort(key=lambda w: (w[1], w[0]))
    rows = []
    current = []
    current_y = None

    for word in body:
        y = word[1]
        if current_y is None or abs(y - current_y) <= y_tolerance:
            current.append(word)
            current_y = y if current_y is None else (current_y + y) / 2
        else:
            current.sort(key=lambda w: w[0])
            rows.append(current)
            current = [word]
            current_y = y

    if current:
        current.sort(key=lambda w: w[0])
        rows.append(current)
    return rows


def row_text(row):
    return ae.clean_text(" ".join(w[4] for w in row))


def looks_like_pos(text):
    text = ae.clean_text(text)
    return bool(re.match(r"^\d{1,3}$", text))


def page_context(rows, last_table_no, last_sub_component):
    table_no = last_table_no
    sub_component = last_sub_component

    for idx, row in enumerate(rows[:12]):
        line = row_text(row)
        if "#" in line or "ELTIS" in line.upper():
            table_no = ae.normalize_table_no(line)
            for candidate_row in rows[idx + 1: idx + 8]:
                has_part = any(ae.split_part_no_prefix(word[4])[0] for word in candidate_row if word[0] <= 165)
                if has_part:
                    break
                candidate = row_text(candidate_row)
                if not candidate:
                    continue
                first = candidate.split()[0]
                if looks_like_pos(first) or ae.is_part_no(first) or candidate.startswith("V-GR"):
                    continue
                if len(candidate) > 2:
                    sub_component = candidate.upper()
                    break
            break

    return table_no, sub_component


def extract_rows_from_page(page, page_no, context):
    rows = group_words_into_rows(page.get_text("words"))
    table_no, sub_component = page_context(rows, context["table_no"], context["sub_component"])
    context["table_no"] = table_no
    context["sub_component"] = sub_component

    extracted = []
    current = None

    for row in rows:
        tokens = [(w[0], w[4]) for w in row]
        part_idx = None
        part_no = ""
        tail = ""
        for idx, (x, text) in enumerate(tokens):
            if x > 165:
                continue
            candidate_part, candidate_tail = ae.split_part_no_prefix(text)
            if candidate_part:
                part_idx = idx
                part_no = candidate_part
                tail = candidate_tail
                break

        if part_no:
            if current:
                extracted.append(current)

            pos = ""
            if part_idx and looks_like_pos(tokens[part_idx - 1][1]):
                pos = tokens[part_idx - 1][1]

            name = ae.clean_text(" ".join(text for x, text in tokens[part_idx + 1:] if x < 525))
            name_parts = []
            if tail:
                name_parts.append(tail)
            if name:
                name_parts.append(name)
            current = {
                "pos_no": pos if looks_like_pos(pos) else "",
                "mfg_part_no": ae.correct_part_no(part_no),
                "name": ae.clean_text(" ".join(name_parts)),
                "page_no": page_no,
                "table_no": table_no,
                "sub_component": sub_component,
            }
            continue

        name = ae.clean_text(" ".join(text for x, text in tokens if 110 <= x < 525))
        if current and name:
            current["name"] = ae.clean_text(f"{current['name']} {name}")

    if current:
        extracted.append(current)
    return extracted


def extract_test5():
    doc = fitz.open(PDF_PATH)
    context = {"table_no": "", "sub_component": ""}
    records = []
    for page_idx in range(len(doc)):
        records.extend(extract_rows_from_page(doc[page_idx], page_idx + 1, context))
    doc.close()
    return records


def to_template_row(record):
    name, size, material = ae.PART_NAME_OVERRIDES.get(
        record["mfg_part_no"],
        ae.split_spare_name_details(record["name"]),
    )
    sub_component = record["sub_component"]
    return [
        ae.COMPONENT_NAME,
        sub_component,
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
        ae.extracted_pdf_name(sub_component),
        "",
        ae.DEFAULT_UOM,
        "",
        "",
        ae.DRAWING_PAGE_WITH_POS,
        "",
        "",
    ]


def main():
    rows = [to_template_row(record) for record in extract_test5()]
    os.makedirs(OUTPUT_PATH.parent, exist_ok=True)
    saved_path = ae.write_to_excel(rows, str(TEMPLATE_PATH), str(OUTPUT_PATH))
    print(f"Extracted rows: {len(rows)}")
    print(f"Output: {saved_path}")


if __name__ == "__main__":
    main()
