import os
import sys
import openpyxl
import fitz
import re
from collections import defaultdict

# Ensure local imports work
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(script_dir, "..")
sys.path.append(os.path.join(script_dir, 'local_engine'))

from pdf_converter import pdf_page_to_image
from table_detector import group_ocr_into_rows
from ocr_extractor import OCRExtractor

# --- Configuration Constants ---
COMPONENT_NAME = "Auxiliary Engine"
MANUFACTURER = "MAN B&W"
MODEL = "D2842LE"
MANUAL_PDF_NAME = "AE D2842LE spare parts manual 1.pdf"
DEFAULT_UOM = "Pcs"
DRAWING_PAGE_WITH_POS = "Yes"
VERBOSE_ROWS = os.getenv("AE_VERBOSE_ROWS") == "1"

PART_NO_RE = re.compile(r"^\d{2}\.\d{5}[-.]\d{3,5}$")
PART_NO_PREFIX_RE = re.compile(r"^(\d{2}\.\d{5}[-.]\d{3,5})(.*)$")
DRAWING_NO_RE = re.compile(r"^\d{2}\s*(?:-|N)\s*[A-Z0-9][A-Z0-9/ -]{2,}$", re.IGNORECASE)
TABLE_NO_RE = re.compile(r"\b(?:ELTIS|#)\s*[A-Z0-9]{6,}\b", re.IGNORECASE)
PART_NO_CORRECTIONS = {
    "51.99131-2001": "51.98131-2001",
    "51.97430-0636": "51.97480-0636",
    "50.98112-2384": "50.96112-2384",
    "05.01283-3125": "06.01283-3125",
}
PART_NAME_OVERRIDES = {
    "51.08308-0029": ("WYE", "", ""),
}
SIZE_TOKEN_RE = re.compile(
    r"^(?:"
    r"M\d"
    r"|AM\d"
    r"|CM\d"
    r"|BLL?\d"
    r"|N\d"
    r"|A-\d"
    r"|\d+(?:[,.]\d+)?[A-Z]*X\d"
    r"|[A-Z]\d+(?:[,.]\d+)?[/X]\d"
    r"|[AB]\d+"
    r"|N\d+-\d+"
    r"|\d+[A-Z]\d"
    r")",
    re.IGNORECASE,
)
NOISE_PHRASE_RE = re.compile(
    r"\b(?:NB|DELETED|REPLACED\s*BY|ITEM\s*NUMBER\s*\S*|ITEMNUMBER\S*|SEE\s*PLATE:?.*)\b",
    re.IGNORECASE,
)


def clean_text(text):
    return re.sub(r"\s+", " ", str(text or "").strip())


def extracted_pdf_name(sub_component):
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", clean_text(sub_component).upper())
    cleaned = cleaned or "SUBCOMPONENT"
    return f"AE{cleaned}.PDF"


def mostly_uppercase(text):
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    upper_count = sum(1 for ch in letters if ch.isupper())
    return upper_count / len(letters) >= 0.75


def normalize_part_no(text):
    return clean_text(text).replace(" ", "")


def correct_part_no(text):
    part_no = normalize_part_no(text)
    return PART_NO_CORRECTIONS.get(part_no, part_no)


def split_part_no_prefix(text):
    compact = normalize_part_no(text)
    match = PART_NO_PREFIX_RE.match(compact)
    if not match:
        return "", clean_text(text)
    part_no, tail = match.groups()
    tail = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", tail)
    return part_no, clean_text(tail)


def normalize_table_no(text):
    return clean_text(text).replace(" ", "").upper()


def split_spare_name_details(raw_name):
    text = clean_text(raw_name).upper()
    if not text:
        return "", "", ""

    replacements = {
        "HEXCOLLAR": "HEX COLLAR",
        "HEXBOLT": "HEX BOLT",
        "HEX BOLTM": "HEX BOLT M",
        "HEXAGONNUT": "HEXAGON NUT",
        "HOLLOWSCREW": "HOLLOW SCREW",
        "SOCKETHEAD": "SOCKET HEAD",
        "DOWELPIN": "DOWEL PIN",
        "TORICSEAL": "TORIC SEAL",
        "FUELLINE": "FUEL LINE",
        "THREADEDUNION": "THREADED UNION",
        "DRAINPLUG": "DRAIN PLUG",
        "HOSEN": "HOSE N",
        "SPRING CLIPA": "SPRING CLIP A",
        "KEYSTONERING": "KEYSTONE RING",
        "FLYWHEELASSEMBLY": "FLYWHEEL ASSEMBLY",
        "ASSEMBLYPASTE": "ASSEMBLY PASTE",
        "WITHNOZZLE": "WITH NOZZLE",
        "ELASTOMERLIP": "ELASTOMER LIP",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)

    # OCR often glues the first size token to the spare name, e.g.
    # COVERA37,3/14-X5CRNI1810N or SEAL14,7X22X1,5.
    text = re.sub(r"\b([A-Z]{3,})([A-Z]?\d+[,.]?\d*(?:[/X]\d))", r"\1 \2", text)
    text = re.sub(r"\bCOVER([A-Z])\s+(\d)", r"COVER \1\2", text)
    text = re.sub(r"\b(BOLT|SCREW|STUD|NUT|WASHER|SEAL|HOSE|CLIP|RING|BUSH|PLUG)([A-Z]?\d)", r"\1 \2", text)
    text = NOISE_PHRASE_RE.sub("", text)
    text = clean_text(text)

    if "WITH ELASTOMER LIP" in text and "SEAL" in text:
        prefix = "SEAL WITH ELASTOMER LIP"
        detail = clean_text(text.replace("WITH ELASTOMER LIP", "").replace("SEAL", "", 1))
        size, material = split_size_material(detail)
        return prefix, size, material

    if text.startswith("GASKET") and "ASBESTOS-FREE" in text:
        detail = clean_text(text.replace("GASKET", "", 1).replace("ASBESTOS-FREE", ""))
        size, material = split_size_material(detail)
        return "GASKET ASBESTOS-FREE", size, material

    tokens = text.split()
    split_idx = None
    for idx, token in enumerate(tokens):
        token_clean = token.strip(",:;()[]")
        if idx == 0:
            continue
        if SIZE_TOKEN_RE.match(token_clean):
            split_idx = idx
            break

    if split_idx is None:
        return canonicalize_spare_name(text), "", ""

    name = canonicalize_spare_name(clean_text(" ".join(tokens[:split_idx])))
    detail = clean_text(" ".join(tokens[split_idx:]))

    size, material = split_size_material(detail)
    return name, size, material


def split_size_material(detail):
    detail = clean_text(detail)
    if not detail:
        return "", ""

    dash_match = re.search(r"[-–—]", detail)
    if dash_match:
        return clean_text(detail[:dash_match.start()]), clean_text(detail[dash_match.end():])

    detail_tokens = detail.split()
    if len(detail_tokens) > 1 and re.search(r"\d", detail_tokens[0]):
        return detail_tokens[0], clean_text(" ".join(detail_tokens[1:]))
    return detail, ""


def clean_material(text):
    text = clean_text(text)
    text = re.sub(r"\bMAN183-B1\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bM3219-G1\b", "", text, flags=re.IGNORECASE)
    text = text.strip(" -")
    return clean_text(text)


def split_size_material(detail):
    detail = clean_text(detail)
    if not detail:
        return "", ""

    detail = re.sub(r"\bMAN183-B1\b", "", detail, flags=re.IGNORECASE)
    detail = re.sub(r"\bM3219-G1\b", "", detail, flags=re.IGNORECASE)
    detail = clean_text(detail)

    token_match = re.match(r"^(A-\d+X\d+|N\d+-\d+)\b(.*)$", detail, re.IGNORECASE)
    if token_match:
        size, rest = token_match.groups()
        return clean_text(size), clean_material(rest)

    token_match = re.match(r"^(B\d+)-(.+)$", detail, re.IGNORECASE)
    if token_match:
        size, rest = token_match.groups()
        return clean_text(size), clean_material(rest)

    dash_match = re.search(r"[-]", detail)
    if dash_match:
        return clean_text(detail[:dash_match.start()]), clean_material(detail[dash_match.end():])

    detail_tokens = detail.split()
    if len(detail_tokens) > 1 and re.search(r"\d", detail_tokens[0]):
        return detail_tokens[0], clean_material(" ".join(detail_tokens[1:]))
    return detail, ""


def canonicalize_spare_name(name):
    name = clean_text(name)
    if not name:
        return ""

    name = re.sub(r"\b(?:NORMAL|OVERSIZE|UNDERSIZE|REPAIR STAGE\s*\d*|DIAMETER|OUTSIDE DIAMETER|COLLAR HEIGHT|HEIGHT:?)\b.*", "", name)
    name = re.sub(r"\b(?:FOR RIGHT|FOR LEFT|RIGHT|LEFT)\b.*", "", name)
    name = re.sub(r"\b(?:WITH BORE|WITH LARGE FLANGE)\b.*", "", name)
    name = re.sub(r"\bOS\b$", "", name)
    name = re.sub(r"\b(?:BOTTLE|TUBE|CARTRIDGE)\b$", "", name)
    name = re.sub(r"\b\d+(?:[,.]\d+)?\s*(?:MM|ML|G)\b.*", "", name)

    simple_prefixes = [
        "SEALANTS OMNIFIT", "SEALANTS", "ADHESIVE", "ASSEMBLY PASTE",
        "DRAIN PLUG", "SPRING WASHER", "HEX SHOULDER STUD", "HEX COLLAR BOLT",
        "HEX BOLT", "HEXAGON NUT", "HOLLOW SCREW", "UNION NUT", "SPRING CLIP",
        "HOSE CLAMP", "MOUNTING CLAMP", "PIPE CLIP", "SUPPORT WASHER",
        "CYLINDER SCREW", "SOCKET HEAD SCREW", "TORIC SEAL", "SEAL",
        "DOWEL PIN", "SPIRAL DOWEL PIN", "BALL", "BUSH", "HOSE", "STUD",
        "WASHER", "GASKET", "COVER", "CLAMP", "BRACKET", "FLANGE",
        "SHIM", "RACE", "PIPE", "RING UNION",
        "SWIVEL UNION", "T-UNION",
    ]
    for prefix in simple_prefixes:
        if name.startswith(prefix):
            return prefix

    return clean_text(name)


def is_part_no(text):
    compact = normalize_part_no(text)
    return bool(PART_NO_RE.match(compact) or PART_NO_PREFIX_RE.match(compact))


def ocr_items(ocr_results, page_width, page_height):
    items = []
    for box, (text, conf) in ocr_results:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x_min = min(xs)
        x_max = max(xs)
        y_min = min(ys)
        y_max = max(ys)
        center_x = (x_min + x_max) / 2
        center_y = (y_min + y_max) / 2
        items.append({
            "x_min": x_min,
            "x_max": x_max,
            "y_min": y_min,
            "y_max": y_max,
            "cx": center_x,
            "cy": center_y,
            "rel_x": center_x / page_width,
            "rel_y": center_y / page_height,
            "text": clean_text(text),
            "conf": conf,
        })
    return items


def find_page_context_ae(ocr_results, page_width, page_height):
    items = ocr_items(ocr_results, page_width, page_height)
    drawing_no = ""
    sub_component = ""
    table_no = ""

    for item in items:
        match = TABLE_NO_RE.search(item["text"])
        if match:
            table_no = normalize_table_no(match.group(0))
            break

    for item in items:
        text = item["text"]
        compact = text.replace(" ", "")
        if "." in compact:
            continue
        if item["rel_x"] < 0.35 and item["rel_y"] < 0.55 and DRAWING_NO_RE.match(text):
            drawing_no = clean_text(text)
            break

    part_rows_y = [item["rel_y"] for item in items if is_part_no(item["text"]) and item["rel_y"] > 0.40]
    first_part_y = min(part_rows_y) if part_rows_y else 0.70

    title_candidates = []
    for item in items:
        text = item["text"]
        upper = text.upper()
        if not (0.45 <= item["rel_y"] <= first_part_y - 0.01):
            continue
        if item["rel_x"] > 0.48:
            continue
        if is_part_no(text) or DRAWING_NO_RE.match(text):
            continue
        if "D2842" in upper or "ELTIS" in upper or "MAN" == upper:
            continue
        if any(ch.isdigit() for ch in text):
            continue
        if len(text) < 4 or not mostly_uppercase(text):
            continue
        title_candidates.append(item)

    if title_candidates:
        title_candidates.sort(key=lambda i: (i["cy"], i["cx"]))
        sub_component = clean_text(" ".join(i["text"] for i in title_candidates))

    return drawing_no, sub_component, table_no


def is_table_page_ae(ocr_results, page_width):
    part_no_count = 0
    for box, (text, conf) in ocr_results:
        ys = [p[1] for p in box]
        center_y = (min(ys) + max(ys)) / 2
        # AE table pages are image-heavy at the top, with dense part numbers in
        # the lower half. Position numbers alone are too small for reliable OCR.
        if center_y > 0 and is_part_no(text):
            part_no_count += 1
    return part_no_count >= 1

def row_pos_no(row):
    pos_tokens = []
    for item in row:
        text = item["text"].replace(" ", "")
        if item["rel_x"] < 0.10 and text.isdigit() and len(text) <= 3:
            pos_tokens.append(text)
    if not pos_tokens:
        return ""
    return pos_tokens[0]


def english_tokens(row):
    tokens = []
    for item in row:
        text = item["text"]
        if item["rel_x"] <= 0.20 or item["rel_x"] >= 0.48:
            continue
        if is_part_no(text):
            continue
        if text.replace(" ", "").isdigit():
            continue
        tokens.append(text)
    return tokens


def row_has_part_no(row):
    return any(is_part_no(item["text"]) and 0.08 <= item["rel_x"] <= 0.24 for item in row)


def row_has_quantity_marker(row):
    for item in row:
        text = item["text"].replace(" ", "")
        if 0.46 <= item["rel_x"] <= 0.54 and text.isdigit() and len(text) <= 3:
            return True
    return False


def is_name_prefix_for_next(row, next_row):
    if not next_row or not row_has_part_no(next_row):
        return False
    tokens = english_tokens(row)
    if not tokens:
        return False

    text = clean_text(" ".join(tokens)).upper()
    name_starters = (
        "GASKET", "HEX", "WASHER", "SPRING WASHER", "DRAIN PLUG", "SEAL",
        "TORIC SEAL", "HOLLOW SCREW", "STUD", "PIPE", "HOSE", "BRACKET",
        "COVER", "FLANGE", "HEATER FLANGE", "OIL SPRAYER NOZZLE",
    )
    return row_has_quantity_marker(row) or text.startswith(name_starters)


def infer_missing_positions(rows):
    last_anchor = None
    for row in rows:
        if not row["pos_no"].isdigit():
            continue
        current = int(row["pos_no"])
        if last_anchor is not None and current < last_anchor:
            row["pos_no"] = ""
            continue
        if last_anchor is not None and current > last_anchor + 3:
            row["pos_no"] = ""
            continue
        last_anchor = current

    explicit = [(idx, int(row["pos_no"])) for idx, row in enumerate(rows) if row["pos_no"].isdigit()]
    if not rows:
        return rows

    explicit_flags = [row["pos_no"].isdigit() for row in rows]
    inferred = [None] * len(rows)
    for idx, pos in explicit:
        inferred[idx] = pos

    if explicit:
        first_idx, first_pos = explicit[0]
        start_pos = max(1, first_pos - first_idx)
        if len(explicit) == 1 and first_idx >= 3 and start_pos == 2:
            start_pos = 1
        for idx in range(first_idx):
            inferred[idx] = start_pos + idx

        for (left_idx, left_pos), (right_idx, right_pos) in zip(explicit, explicit[1:]):
            gap = right_idx - left_idx
            pos_gap = right_pos - left_pos
            if gap > 0 and pos_gap == gap:
                for idx in range(left_idx + 1, right_idx):
                    inferred[idx] = left_pos + (idx - left_idx)

        last_idx, last_pos = explicit[-1]
        for idx in range(last_idx + 1, len(rows)):
            inferred[idx] = last_pos + (idx - last_idx)
    else:
        for idx in range(len(rows)):
            inferred[idx] = idx + 1

    for idx, row in enumerate(rows):
        if inferred[idx] is not None:
            row["pos_no"] = str(inferred[idx])

    part_counts = defaultdict(int)
    for row in rows:
        part_counts[row.get("mfg_part_no", "")] += 1
    for idx, row in enumerate(rows):
        if not explicit_flags[idx] and part_counts[row.get("mfg_part_no", "")] > 1:
            row["pos_no"] = ""
    return rows


def extract_part_rows_ae(rows):
    extracted = []
    current = None
    pending_prefix = []

    for idx, row in enumerate(rows):
        if not row:
            continue
        part_items = [item for item in row if is_part_no(item["text"]) and 0.08 <= item["rel_x"] <= 0.24]
        if part_items:
            if current:
                extracted.append(current)
            part_items.sort(key=lambda i: i["rel_x"])
            part_no, part_tail = split_part_no_prefix(part_items[0]["text"])
            name_parts = pending_prefix + english_tokens(row)
            pending_prefix = []
            if part_tail:
                name_parts.insert(0, part_tail)
            current = {
                "pos_no": row_pos_no(row),
                "mfg_part_no": correct_part_no(part_no or part_items[0]["text"]),
                "name_parts": name_parts,
            }
            continue

        if current:
            next_row = rows[idx + 1] if idx + 1 < len(rows) else None
            if is_name_prefix_for_next(row, next_row):
                pending_prefix = english_tokens(row)
                continue
            continuation = english_tokens(row)
            if continuation:
                current["name_parts"].extend(continuation)

    if current:
        extracted.append(current)

    for row in extracted:
        row["name_of_spare"] = clean_text(" ".join(row["name_parts"]))
    return infer_missing_positions(extracted)

def group_ocr_into_rows_ae(ocr_results, page_width, page_height, y_tolerance=15):
    if not ocr_results:
        return []
    
    items = []
    for res in ocr_results:
        box, (text, conf) = res
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        center_x = (min(xs) + max(xs)) / 2
        center_y = (min(ys) + max(ys)) / 2
        rel_x = center_x / page_width
        rel_y = center_y / page_height
        
        # Less aggressive filtering for AE. Some AE tables run very close to the
        # bottom edge, so keep more of the lower page than the VOLUME I parser.
        if rel_y > 0.92:
            continue
        if rel_y < 0.05:
            continue
            
        items.append({
            "cx": center_x, "cy": center_y,
            "rel_x": rel_x, "rel_y": rel_y,
            "text": text.strip(), "conf": conf
        })
    
    if not items:
        return []
    
    items.sort(key=lambda i: i["cy"])
    rows = []
    current_row = [items[0]]
    current_row_y = items[0]["cy"]
    
    for item in items[1:]:
        if abs(item["cy"] - current_row_y) <= y_tolerance:
            current_row.append(item)
            current_row_y = sum(i["cy"] for i in current_row) / len(current_row)
        else:
            current_row.sort(key=lambda i: i["cx"])
            rows.append(current_row)
            current_row = [item]
            current_row_y = item["cy"]
    
    if current_row:
        current_row.sort(key=lambda i: i["cx"])
        rows.append(current_row)
    
    return rows

def is_valid_item_no(text):
    cleaned = text.strip().replace(" ", "")
    return len(cleaned) >= 1

def process_pdf_locally(pdf_path, pages_to_process):
    print("Initializing Local PaddleOCR Engine...")
    extractor = OCRExtractor()
    
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    extracted_rows = []
    
    last_drwg_no = ""
    last_sub_component = ""
    last_table_no = ""
    
    for page_no in pages_to_process:
        page_idx = page_no - 1  # 0-based
        if page_idx >= total_pages or page_idx < 0:
            continue
            
        print(f"\nProcessing page {page_no}/{total_pages}...")
        
        img = pdf_page_to_image(pdf_path, page_idx, dpi=200)
        page_height, page_width = img.shape[:2]
        
        ocr_results = extractor.extract_text(img)
        
        if not ocr_results:
            print(f"  No text found on page {page_no}.")
            continue
        
        page_drwg_no, page_sub_component, page_table_no = find_page_context_ae(ocr_results, page_width, page_height)
        if page_drwg_no:
            last_drwg_no = page_drwg_no
        if page_sub_component:
            last_sub_component = page_sub_component
        if page_table_no:
            last_table_no = page_table_no

        if is_table_page_ae(ocr_results, page_width):
            print(f"  [TABLE PAGE] Using Drawing No: {last_drwg_no}, Sub-Component: {last_sub_component}, Table No: {last_table_no}")
            
            rows = group_ocr_into_rows_ae(ocr_results, page_width, page_height, y_tolerance=15)
            table_data = extract_part_rows_ae(rows)
            
            print(f"  Found {len(table_data)} rows of text.")
            
            for row_cols in table_data:
                pos_no = row_cols.get("pos_no", "").strip().replace(" ", "")
                raw_name_of_spare = row_cols.get("name_of_spare", "").strip()
                mfg_part_no = row_cols.get("mfg_part_no", "").strip()
                name_of_spare, size_dimension, material = PART_NAME_OVERRIDES.get(
                    mfg_part_no,
                    split_spare_name_details(raw_name_of_spare),
                )
                
                if pos_no and not pos_no.isdigit():
                    continue
                
                if VERBOSE_ROWS:
                    print(f"    Row: Pos={pos_no}, Name={name_of_spare}, Size={size_dimension}, Material={material}, Part={mfg_part_no}, Table={last_table_no}")
                
                row_data_arr = [
                    COMPONENT_NAME,           
                    last_sub_component,       
                    MANUFACTURER,             
                    MODEL,                    
                    name_of_spare,            
                    mfg_part_no,              
                    last_drwg_no,             
                    pos_no,                   
                    size_dimension,           
                    material,                 
                    "",                       
                    last_table_no,            
                    page_no,                  
                    extracted_pdf_name(last_sub_component),
                    "",                       
                    DEFAULT_UOM,              
                    "",                       
                    "",                       
                    DRAWING_PAGE_WITH_POS,    
                    "",                       
                    ""                        
                ]
                extracted_rows.append(row_data_arr)
        else:
            print(f"  [DRAWING PAGE] Drawing No: {last_drwg_no}, Sub-Component: {last_sub_component}, Table No: {last_table_no}")
                
    doc.close()
    return extracted_rows

def write_to_excel(extracted_rows, template_path, output_path):
    print(f"\nWriting {len(extracted_rows)} rows to Excel...")
    wb = openpyxl.load_workbook(template_path, keep_vba=True)
    sheet = wb.active
    
    start_row = 3
    for i, row_data in enumerate(extracted_rows):
        current_row = start_row + i
        for col_idx, val in enumerate(row_data):
            sheet.cell(row=current_row, column=col_idx+1).value = val
            
    base, ext = os.path.splitext(output_path)
    candidates = [
        output_path,
        f"{base}_extracted{ext}",
        os.path.join(project_root, f"Auxiliary Engine 1_extracted{ext}"),
    ]

    last_error = None
    for candidate in candidates:
        try:
            wb.save(candidate)
            print(f"Saved Excel to {candidate}")
            return candidate
        except PermissionError as exc:
            last_error = exc
            print(f"Could not write {candidate}: {exc}")

    raise last_error

if __name__ == "__main__":
    pdf_path = os.path.join(project_root, "input", "AE D2842LE spare parts manual 1.pdf")
    template_path = os.path.join(project_root, "template", "Spares_Capture_Template_Ver12 2.xlsm")
    output_path = os.path.join(project_root, "output", "Auxiliary Engine 1.xlsm")
    
    # 12-37,38-76,78-130,133
    page_ranges = [(12, 37), (38, 76), (78, 130), (133, 133)]
    pages_to_process = []
    for start, end in page_ranges:
        pages_to_process.extend(list(range(start, end + 1)))
        
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    if os.path.exists(pdf_path):
        rows = process_pdf_locally(pdf_path, pages_to_process)
        write_to_excel(rows, template_path, output_path)
        print("Done!")
    else:
        print(f"Test PDF not found at {pdf_path}")
