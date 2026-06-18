import re
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REF_PATH = PROJECT_ROOT / "Test" / "Test 4" / "IME36520W_FAR2xx8.pdf.xlsx"
OUT_PATH = PROJECT_ROOT / "output" / "Test4_extracted.xlsm"

MAX_COLS = 21


def parse_page(value):
    try:
        return int(str(value or "").strip())
    except ValueError:
        return None


def normalize(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def normalized_row(row):
    return tuple(normalize_text(value) for value in row[:MAX_COLS])


def load_rows(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = []
    for idx, row in enumerate(ws.iter_rows(min_row=3, max_col=MAX_COLS, values_only=True), start=3):
        if not any(row):
            continue
        rows.append({"excel_row": idx, "values": tuple(row)})
    wb.close()
    return rows


def multiset_score(ref_rows, out_rows):
    ref_counts = Counter(normalized_row(item["values"]) for item in ref_rows)
    out_counts = Counter(normalized_row(item["values"]) for item in out_rows)
    matched = sum((ref_counts & out_counts).values())
    missing = sum((ref_counts - out_counts).values())
    extra = sum((out_counts - ref_counts).values())
    precision = matched / len(out_rows) if out_rows else 0
    recall = matched / len(ref_rows) if ref_rows else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    return matched, missing, extra, precision, recall, f1


def key_for(row, counts):
    page = str(row[12] or "").strip()
    sub = normalize(row[1])
    name = normalize(row[4])
    part = normalize(row[5])
    drwg = normalize(row[6])
    pos = normalize(row[7])
    base = (page, sub, name, part, drwg, pos)
    counts[base] += 1
    return base + (counts[base],)


def field_scores(ref_rows, out_rows):
    ref_counts = defaultdict(int)
    out_counts = defaultdict(int)
    ref_by_key = {}
    out_by_key = {}
    for item in ref_rows:
        key = key_for(item["values"], ref_counts)
        ref_by_key[key] = item
    for item in out_rows:
        key = key_for(item["values"], out_counts)
        out_by_key[key] = item

    matched_keys = set(ref_by_key) & set(out_by_key)
    fields = {
        "component": 0,
        "sub_component": 1,
        "manufacturer": 2,
        "model": 3,
        "name": 4,
        "mfg_part": 5,
        "drawing": 6,
        "pos": 7,
        "size": 8,
        "material": 9,
        "details": 11,
        "page": 12,
        "manual": 13,
        "uom": 15,
        "pdf": 16,
        "drawing_without_pos": 17,
        "drawing_with_pos": 18,
    }
    hits = {field: 0 for field in fields}
    for key in matched_keys:
        ref = ref_by_key[key]["values"]
        out = out_by_key[key]["values"]
        for field, col in fields.items():
            if normalize_text(ref[col]) == normalize_text(out[col]):
                hits[field] += 1
    return len(matched_keys), fields, hits


def main():
    if not OUT_PATH.exists():
        print(f"Missing output: {OUT_PATH}")
        return

    ref_rows = load_rows(REF_PATH)
    out_rows = load_rows(OUT_PATH)
    matched, missing, extra, precision, recall, f1 = multiset_score(ref_rows, out_rows)
    key_matches, fields, hits = field_scores(ref_rows, out_rows)

    print("=" * 64)
    print("TEST4 ACCURACY REPORT")
    print("=" * 64)
    print(f"Reference rows : {len(ref_rows)}")
    print(f"Extracted rows : {len(out_rows)}")
    print(f"Matched rows   : {matched}")
    print(f"Missing rows   : {missing}")
    print(f"Extra rows     : {extra}")
    print(f"Precision      : {precision:.2%}")
    print(f"Recall         : {recall:.2%}")
    print(f"F1             : {f1:.2%}")
    print("-" * 64)
    print(f"Key matches    : {key_matches}")
    for field in fields:
        score = hits[field] / key_matches if key_matches else 0
        print(f"{field:20}: {score:.2%} ({hits[field]}/{key_matches})")


if __name__ == "__main__":
    main()
