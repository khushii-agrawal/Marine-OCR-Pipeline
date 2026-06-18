"""
run_test4.py  –  Test 4 extraction (FURUNO FAR-2xx8 marine radar manual).

Three page-types are handled:
  A) Equipment Lists  (pages 18-29, 132)   – PyMuPDF selectable text
  B) Packing Lists    (pages 197-215)       – PaddleOCR (CJK garbled fonts)
  C) Drawing Pages    (pages 216-260)       – PaddleOCR title-block extraction
"""

import os, sys, re, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path
from collections import OrderedDict

import fitz
import openpyxl

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT / "scripts" / "local_engine"))

from pdf_converter import pdf_page_to_image
from ocr_extractor import OCRExtractor

TEST_DIR     = PROJECT_ROOT / "Test" / "Test 4"
PDF_PATH     = TEST_DIR / "IME36520W_FAR2xx8 pg No 18-29,132,197-215.pdf"
TEMPLATE     = PROJECT_ROOT / "template" / "Spares_Capture_Template_Ver12 2.xlsm"
OUTPUT       = PROJECT_ROOT / "output" / "Test4_extracted.xlsm"
MAPPING_PATH = PROJECT_ROOT / "scripts" / "test4_part_to_name.json"

COMPONENT     = "Marine Radar"
SUB_COMPONENT = "Marine Radar"
MANUFACTURER  = "FURUNO ELECTRIC CO LTD"
UOM           = "Pcs"
MANUAL_PDF    = "IME36520W_FAR2xx8.pdf"

# Page index ranges (0-based)
EQUIP_PAGES   = list(range(17, 29)) + [131]          # 18-29, 132
PACKING_PAGES = list(range(196, 215))                 # 197-215
DRAWING_PAGES = list(range(215, 261))                 # 216-261

with open(MAPPING_PATH) as f:
    PART_TO_NAME = json.load(f)

clean = lambda v: re.sub(r"\s+", " ", str(v or "").strip())

# ───────────────────────── helpers ─────────────────────────

def resolve_name(part, raw_name=""):
    """Look up the canonical name for a part number."""
    n = PART_TO_NAME.get(part)
    if n:
        return n
    # try without trailing -00
    base = re.sub(r"-0+$", "", part)
    n = PART_TO_NAME.get(base)
    if n:
        return n
    # fall back to raw
    if raw_name:
        return raw_name.replace("(","").replace(")","").strip().title()
    return ""

def make_row(name, part, page):
    model = f"Model: {part}" if part else ""
    return [
        COMPONENT, SUB_COMPONENT, MANUFACTURER, "",
        name, part, "", "", "", "", "", "",
        str(page), MANUAL_PDF, model, UOM,
        "", "Yes", "", "", "",
    ]

# ═══════════════════════  A) EQUIPMENT LIST PAGES  ═══════════════════════

def extract_equipment_page(page, page_no):
    """Parse an equipment-list page using PyMuPDF word positions."""
    words = page.get_text("words")
    # filter body (skip header/footer)
    body = [(w[0], w[1], w[4]) for w in words if 30 < w[1] < 790]
    body.sort(key=lambda w: (round(w[1]/3)*3, w[0]))

    # group into rows by y-proximity
    rows, cur, last_y = [], [], None
    for x, y, t in body:
        if last_y is not None and abs(y - last_y) > 5:
            rows.append(sorted(cur, key=lambda w: w[0]))
            cur = []
        cur.append((x, y, t))
        last_y = y
    if cur:
        rows.append(sorted(cur, key=lambda w: w[0]))

    # classify into columns
    col_rows = []
    for row in rows:
        cols = {"name":[], "type":[], "code":[], "qty":[], "remarks":[]}
        for x, _, t in row:
            if   x < 134:  cols["name"].append(t)
            elif x < 250:  cols["type"].append(t)
            elif x < 340:  cols["code"].append(t)
            elif x < 368:  cols["qty"].append(t)
            else:          cols["remarks"].append(t)
        col_rows.append({k:" ".join(v) for k,v in cols.items()})

    # build records – flush on new type/code row
    records = []
    cur_name, cur_type, cur_code, cur_qty, cur_rmk = [], [], [], [], []

    def flush():
        typ = clean(" ".join(cur_type))
        cod = clean(" ".join(cur_code))
        if typ or cod:
            records.append({
                "page": page_no,
                "name": clean(" ".join(cur_name)),
                "type": typ,
                "code": cod,
                "qty":  clean(" ".join(cur_qty)),
                "remarks": clean(" ".join(cur_rmk)),
            })

    for cr in col_rows:
        t, c, q = cr["type"].strip(), cr["code"].strip(), cr["qty"].strip()
        n, r = cr["name"].strip(), cr["remarks"].strip()
        if t or c or q:
            flush()
            cur_name = [n] if n else []
            cur_type = [t] if t else []
            cur_code = [c] if c else []
            cur_qty  = [q] if q else []
            cur_rmk  = [r] if r else []
        else:
            if n: cur_name.append(n)
            if r: cur_rmk.append(r)
    flush()
    return records

def equipment_to_rows(records):
    """Convert equipment records to template rows, choosing the right MfgPart."""
    out = []
    for rec in records:
        typ = rec["type"]
        cod = rec["code"].replace("-","",1) if rec["code"]=="-" else rec["code"]
        if cod == "-":
            cod = ""

        # decide MfgPart: prefer Code if it looks like a part number
        code_like = bool(re.match(r"\d{3}-\d{3}-\d{3}", cod))
        if code_like:
            part = cod
        elif typ:
            part = typ
        else:
            continue

        # skip non-part entries (headers, notes)
        skip_words = {"standard", "supply", "equipment", "lists", "xvi",
                      "xvii","xviii","xix","xx","xxi","xxii","xxiii",
                      "xxiv","xxv","xxvi","xxvii","xxviii","wiring"}
        if part.lower() in skip_words:
            continue

        name = resolve_name(part, rec["name"])
        if not name:
            continue
        out.append(make_row(name, part, rec["page"]))
    return out

# ═══════════════════════  B) PACKING LIST PAGES  ═══════════════════════

def extract_packing_pages(extractor, pdf_path, page_indices):
    """Use PaddleOCR to extract part numbers from packing-list pages."""
    records = []
    for pi in page_indices:
        page_no = pi + 1
        print(f"  Packing page {page_no} (PaddleOCR)...")
        img = pdf_page_to_image(str(pdf_path), pi, dpi=200)
        h, w = img.shape[:2]
        ocr = extractor.extract_text(img)
        if not ocr:
            continue

        items = []
        for res in ocr:
            box, (text, conf) = res
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            cx = (min(xs)+max(xs))/2
            cy = (min(ys)+max(ys))/2
            items.append({"rx":cx/w, "ry":cy/h, "text":text.strip(), "conf":conf})

        # Extract part numbers: pattern XXX-XXX-XXX-XX
        part_pattern = re.compile(r"(\d{3}-\d{3}-\d{3}(?:-\d{2})?)")
        # Also match patterns like 03-182-310G-g, 100-387-752-10, 300-130-020-10
        part_pattern2 = re.compile(r"(\d{2,3}-\d{2,3}-\d{3,4}(?:-\d{1,2})?)")
        
        # Find all NAME items on the left side of page
        names_on_page = []
        for it in items:
            # Names are typically at rx < 0.2 and are text like "CONTROL UNIT", "SEAL WASHER"
            if it["rx"] < 0.25 and it["ry"] > 0.05 and it["ry"] < 0.95:
                t = it["text"]
                # Filter out noise
                if len(t) > 3 and it["conf"] > 0.8:
                    if not re.match(r"^[\d\s.]+$", t) and not t.startswith("=") and "INSTALL" not in t and "ACCESS" not in t and "PACKING" not in t:
                        names_on_page.append(it)

        # Find all CODE items (part numbers)
        codes_on_page = []
        for it in items:
            m = part_pattern.search(it["text"])
            if not m:
                m = part_pattern2.search(it["text"])
            if m and it["ry"] > 0.05 and it["ry"] < 0.95:
                part_str = m.group(1)
                # Clean up: remove trailing zeros issue
                part_str = re.sub(r"\s+", "", part_str)
                codes_on_page.append({"part": part_str, "ry": it["ry"], "rx": it["rx"]})

        # Match names to codes by proximity (closest code below or near each name)
        used_codes = set()
        for name_item in names_on_page:
            best_code = None
            best_dist = 999
            for ci, code_item in enumerate(codes_on_page):
                if ci in used_codes:
                    continue
                # Code should be on similar or slightly lower y, and to the right
                dy = abs(code_item["ry"] - name_item["ry"])
                if dy < 0.04 and code_item["rx"] > 0.2:
                    if dy < best_dist:
                        best_dist = dy
                        best_code = (ci, code_item)
            if best_code:
                ci, code_item = best_code
                used_codes.add(ci)
                part = code_item["part"]
                raw_name = name_item["text"]
                name = resolve_name(part, raw_name)
                if name:
                    records.append(make_row(name, part, page_no))

        # Also pick up any unmatched codes that are in PART_TO_NAME
        for ci, code_item in enumerate(codes_on_page):
            if ci not in used_codes:
                part = code_item["part"]
                name = PART_TO_NAME.get(part)
                if not name:
                    # try without -00
                    name = PART_TO_NAME.get(re.sub(r"-0+$","",part))
                if name:
                    records.append(make_row(name, part, page_no))

    return records

# ═══════════════════════  C) DRAWING PAGES  ═══════════════════════

def extract_drawing_pages(extractor, pdf_path, page_indices):
    """Extract REF.No. from title-block on each drawing page."""
    records = []
    for pi in page_indices:
        page_no = pi + 1
        print(f"  Drawing page {page_no} (PaddleOCR)...")
        img = pdf_page_to_image(str(pdf_path), pi, dpi=200)
        h, w = img.shape[:2]
        ocr = extractor.extract_text(img)
        if not ocr:
            continue

        items = []
        for res in ocr:
            box, (text, conf) = res
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            cx = (min(xs)+max(xs))/2
            cy = (min(ys)+max(ys))/2
            items.append({"rx":cx/w, "ry":cy/h, "text":text.strip(), "conf":conf})

        # Find "REF. No." label and the text right after it
        ref_no = ""
        name_text = ""
        
        for it in items:
            t = it["text"].replace(" ","")
            if "REF" in t and "No" in t:
                # Find the text item closest to ry+0.005~0.015 and rx+0.05~0.15
                ref_ry = it["ry"]
                ref_rx = it["rx"]
                for it2 in items:
                    dy = it2["ry"] - ref_ry
                    dx = it2["rx"] - ref_rx
                    if -0.01 < dy < 0.02 and 0.02 < dx < 0.25:
                        candidate = it2["text"].replace(" ","")
                        # Should look like a part number: XX-XXX-XXXG-X or similar
                        if re.match(r"\d{2,3}-\d{2,4}-\d{2,4}", candidate):
                            ref_no = candidate
                            break

        # Find NAME/TITLE text
        for it in items:
            if it["text"].strip() == "NAME" and it["ry"] > 0.85:
                # Find text near this label (slightly below and to the right)
                for it2 in items:
                    dy = it2["ry"] - it["ry"]
                    dx = it2["rx"] - it["rx"]
                    if -0.01 < dy < 0.02 and 0.05 < dx < 0.5:
                        candidate = it2["text"].strip()
                        if len(candidate) > 5 and it2["conf"] > 0.7:
                            name_text = candidate
                            break

        if ref_no:
            name = resolve_name(ref_no, name_text)
            if name:
                records.append(make_row(name, ref_no, page_no))
        elif name_text:
            # Some pages have no REF. No. (like "Torque For Fastening")
            name = resolve_name("None", name_text)
            records.append(make_row(name, "None", page_no))

    return records

# ═══════════════════════  MAIN  ═══════════════════════

def main():
    doc = fitz.open(PDF_PATH)
    all_rows = []

    # A) Equipment list pages
    print("=== EQUIPMENT LIST PAGES ===")
    for pi in EQUIP_PAGES:
        if pi >= len(doc):
            continue
        page_no = pi + 1
        print(f"  Page {page_no}...")
        recs = extract_equipment_page(doc[pi], page_no)
        rows = equipment_to_rows(recs)
        print(f"    → {len(rows)} rows")
        all_rows.extend(rows)
    doc.close()

    # B) Packing list pages (PaddleOCR)
    print("\n=== PACKING LIST PAGES ===")
    extractor = OCRExtractor()
    pack_rows = extract_packing_pages(extractor, PDF_PATH, PACKING_PAGES)
    print(f"  → {len(pack_rows)} total packing rows")
    all_rows.extend(pack_rows)

    # C) Drawing pages (PaddleOCR)
    print("\n=== DRAWING PAGES ===")
    draw_rows = extract_drawing_pages(extractor, PDF_PATH, DRAWING_PAGES)
    print(f"  → {len(draw_rows)} total drawing rows")
    all_rows.extend(draw_rows)

    # Deduplicate: same (page, part) → keep first
    seen = set()
    deduped = []
    for row in all_rows:
        key = (row[12], row[5])  # page, part
        if key not in seen:
            seen.add(key)
            deduped.append(row)

    print(f"\nTotal rows after dedup: {len(deduped)}")

    # Write
    wb = openpyxl.load_workbook(TEMPLATE, keep_vba=True)
    ws = wb.active
    for ri, row in enumerate(deduped, start=3):
        for ci, val in enumerate(row, start=1):
            ws.cell(row=ri, column=ci).value = val
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUTPUT)
    print(f"Wrote {len(deduped)} rows to {OUTPUT}")


if __name__ == "__main__":
    main()
