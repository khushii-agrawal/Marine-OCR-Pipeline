import os
import re
from collections import defaultdict

import openpyxl


REF_PATH = os.path.join("test", "Test 1", "23000143 11L REV 1 AS BUILT 5-12-2013.xlsm")
OUT_PATH = os.path.join("output", "Test1_extracted.xlsm")


def normalize(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def normalize_part(value):
    text = str(value or "").replace("//", "/").replace("\\", "/")
    return normalize(text)


def key_for(row, counts):
    page = str(row[12] or "").strip()
    part = normalize_part(row[5])
    model = normalize(row[14])
    nav = normalize(row[11])
    base = (page, part, model) if part or model else (page, nav)
    counts[base] += 1
    return base + (counts[base],)


def load_rows(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_vba=True)
    ws = wb.active
    rows = {}
    counts = defaultdict(int)
    for row_idx, row in enumerate(ws.iter_rows(min_row=3, max_col=21, values_only=True), start=3):
        if not any(row):
            continue
        key = key_for(row, counts)
        rows[key] = {
            "row": row_idx,
            "component": row[0],
            "sub_component": row[1],
            "manufacturer": row[2],
            "name": row[4],
            "part": row[5],
            "details": row[11],
            "page": row[12],
            "manual": row[13],
            "model": row[14],
            "pdf": row[16],
        }
    wb.close()
    return rows


def evaluate():
    if not os.path.exists(OUT_PATH):
        print(f"Missing output: {OUT_PATH}")
        return

    ref = load_rows(REF_PATH)
    out = load_rows(OUT_PATH)
    matched = set(ref) & set(out)
    missing = set(ref) - matched
    extra = set(out) - matched

    precision = len(matched) / len(out) if out else 0
    recall = len(matched) / len(ref) if ref else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0

    fields = ["manufacturer", "name", "part", "details", "model", "pdf"]
    field_hits = {field: 0 for field in fields}
    discrepancies = []
    for key in matched:
        row_bad = {}
        for field in fields:
            ok = normalize_text(ref[key][field]) == normalize_text(out[key][field])
            if ok:
                field_hits[field] += 1
            else:
                row_bad[field] = (ref[key][field], out[key][field])
        if row_bad:
            discrepancies.append((ref[key], out[key], row_bad))

    print("=" * 60)
    print("TEST1 ACCURACY REPORT")
    print("=" * 60)
    print(f"Reference rows : {len(ref)}")
    print(f"Extracted rows : {len(out)}")
    print(f"Matched rows   : {len(matched)}")
    print(f"Missing rows   : {len(missing)}")
    print(f"Extra rows     : {len(extra)}")
    print(f"Precision      : {precision:.2%}")
    print(f"Recall         : {recall:.2%}")
    print(f"F1             : {f1:.2%}")
    print("-" * 60)
    for field in fields:
        score = field_hits[field] / len(matched) if matched else 0
        print(f"{field:13}: {score:.2%} ({field_hits[field]}/{len(matched)})")

    if missing:
        print("\nMissing sample:")
        for key in sorted(missing, key=str)[:10]:
            item = ref[key]
            print(f"Page {item['page']} | Part {item['part']} | Model {item['model']} | Name {item['name']}")

    if extra:
        print("\nExtra sample:")
        for key in sorted(extra, key=str)[:10]:
            item = out[key]
            print(f"Page {item['page']} | Part {item['part']} | Model {item['model']} | Name {item['name']}")

    if discrepancies:
        print("\nField discrepancy sample:")
        for ref_item, out_item, bad in discrepancies[:10]:
            print(f"Ref row {ref_item['row']} vs Out row {out_item['row']} | Part {ref_item['part']}")
            for field, (ref_val, out_val) in bad.items():
                print(f"  {field}: ref={ref_val!r} out={out_val!r}")


if __name__ == "__main__":
    evaluate()
