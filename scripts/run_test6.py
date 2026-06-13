import os
import re
import sys
import unicodedata
from pathlib import Path

import fitz


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.append(str(SCRIPT_DIR))

import run_ae as ae


PDF_PATH = PROJECT_ROOT / "test" / "Test 6" / "Extracted pages from MAN B&W SPARES PARTS CATALOGUE.pdf"
TEMPLATE_PATH = PROJECT_ROOT / "template" / "Spares_Capture_Template_Ver12 2.xlsm"
OUTPUT_PATH = PROJECT_ROOT / "output" / "Test6_MAN_BW_spares_extracted.xlsm"

START_PAGE = int(os.environ.get("TEST6_START_PAGE", "1"))
END_PAGE = int(os.environ.get("TEST6_END_PAGE", "0"))

COMPONENT_NAME = "Main Engine"
MANUFACTURER = "MAN B&W"
MODEL = "L 40/45"
DEFAULT_UOM = "PCS"
DRAWING_PAGE_WITH_POS = "Yes"

CATALOG_RE = re.compile(r"^\d{3}\.\d{2}\.(?:\d{3}|[A-Z]{1,4})$")
TURBO_PART_RE = re.compile(r"^(?:[S5]\d{2}|[S5]\d{2}|[A-Z]?\d{3})[.,]?\d{3}[~%]?$")
TABLE_RE = re.compile(r"\b\d{3}[.,]\d{2}\b")
DATE_RE = re.compile(r"^\d{1,2}[.,]\d{2}[.,]\d{4}$")
QUANTITY_RE = re.compile(r"^\d+(?:[.,]\d+)?$")

HEADER_SKIP = {
    "MAN", "MA+N", "POS", "POS.", "BENENNUNG", "DESIGNATION", "DESIGNACION",
    "DESIGNACIÓN", "ORDER NO", "ORDER", "NO", "NO.", "BESTELL-NR",
    "BESTELL.-NR.", "FIGURE", "FIGURA", "BILD",
}
FOREIGN_STARTS = {
    "colector", "junta", "tornillo", "clavija", "racor", "sombrerete",
    "capuchon", "vissage", "goujon", "soupape", "vilebrequin", "moteur",
    "tuyau", "tubo", "eje", "rueda", "cojinete", "casquillo", "boulon",
    "vis", "disque", "robient", "robinet", "support,", "anneau", "carter",
    "caja", "anillo", "brida", "tuerca", "tapa", "figura", "de", "para",
    "prisionero", "abrazadera", "adhesivo", "masilla",
}
SPANISH_BREAK_WORDS = {
    "caja", "anillo", "brida", "tornillo", "tuerca", "tapa", "junta",
    "figura", "tubo", "prisionero", "abrazadera", "adhesivo", "masilla",
    "soporte", "remache", "pieza", "piezas", "contra", "cristal",
    "instalacion", "medicion", "dinamo", "racor",
}


def clean_text(text):
    text = ae.clean_text(text)
    text = text.replace("0IL", "OIL").replace("0il", "Oil")
    text = text.replace("Thrüst", "Thrust")
    text = text.replace("Additlonal", "Additional")
    text = re.sub(r"\s+", " ", text).strip(" -")
    return text


def ascii_key(text):
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def word_items(page):
    items = []
    for w in page.get_text("words"):
        text = clean_text(w[4])
        if not text or text == "<br>" or w[1] < 0:
            continue
        items.append({
            "x0": float(w[0]),
            "y0": float(w[1]),
            "x1": float(w[2]),
            "y1": float(w[3]),
            "cx": (float(w[0]) + float(w[2])) / 2,
            "cy": (float(w[1]) + float(w[3])) / 2,
            "text": text,
        })
    return items


def group_lines(items, y_tolerance=4):
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


def is_foreign_line(text):
    key = ascii_key(text)
    if not key:
        return True
    first = key.split()[0]
    if first in FOREIGN_STARTS:
        return True
    foreign_hits = sum(1 for token in ("aceite", "cilindro", "cilindros", "motor", "moteur", "avec", "pour", "pieza", "piezas", "bielle", "vilebrequin") if token in key)
    return foreign_hits >= 2


def valid_name_line(text):
    key = ascii_key(text)
    if not key:
        return False
    if key.upper() in HEADER_SKIP:
        return False
    if "designation" in key or "designacion" in key:
        return False
    if "bestell" in key or "commande" in key or "pedido" in key:
        return False
    if DATE_RE.match(text) or CATALOG_RE.match(text):
        return False
    return not is_foreign_line(text)


def normalize_pos(text):
    text = clean_text(text).replace(" ", "")
    text = text.replace("g", "9").replace("G", "9")
    text = text.replace("O", "0").replace("o", "0")
    if re.fullmatch(r"[A-Z]|\d{1,3}", text):
        return text
    return ""


def table_no_from_text(text, fallback=""):
    slash_match = re.search(r"\b(\d{3}[.,]\d{2})/\d+\b", text)
    if slash_match:
        return slash_match.group(1).replace(",", ".")
    matches = TABLE_RE.findall(text.replace("/", " "))
    if not matches:
        return fallback
    return matches[0].replace(",", ".")


def candidate_header_lines(items):
    header = [item for item in items if 20 <= item["y0"] <= 115 and 180 <= item["x0"] <= 435]
    return [line_text(line) for line in group_lines(header, y_tolerance=5)]


def footer_context(text):
    tokens = text_tokens(text)
    for idx, token in enumerate(tokens):
        if not re.fullmatch(r"\d{3}\.\d{2}", token):
            continue
        if idx + 1 >= len(tokens) or tokens[idx + 1].lower() != "for":
            continue
        words = []
        for follow in tokens[idx + 2: idx + 8]:
            if DATE_RE.match(follow) or re.search(r"\d", follow):
                break
            words.append(follow)
        if words:
            return clean_text(" ".join(words)).upper(), token

    footer_match = re.search(r"\b(\d{3}\.\d{2})\s+For\s+(.+?)(?:\n|<br>|$)", text, re.IGNORECASE)
    if footer_match:
        return clean_text(footer_match.group(2)).upper(), footer_match.group(1)
    return "", ""


def page_context(items, text, previous_subcomponent, previous_table_no):
    top_footer_items = [item for item in items if item["y0"] <= 120 or item["y0"] >= 760]
    top_footer_text = "\n".join(line_text(line) for line in group_lines(top_footer_items, y_tolerance=5))
    table_no = table_no_from_text(top_footer_text, previous_table_no)
    subcomponent = previous_subcomponent

    lines = candidate_header_lines(items)
    cleaned_lines = []
    for text_line in lines:
        cleaned = clean_text(text_line).upper()
        key = ascii_key(cleaned)
        if not cleaned or any(ch.isdigit() for ch in cleaned):
            continue
        cleaned = re.sub(r"\bL\s*$", "", cleaned).strip()
        key = ascii_key(cleaned)
        if len(key) < 4 or key in {"pc", "cm", "set", "na"}:
            continue
        tokens = key.split()
        if tokens and sum(1 for token in tokens if token in {"pc", "cm", "set", "l"}) / len(tokens) > 0.4:
            continue
        if key.upper() in HEADER_SKIP or "DESIGN" in cleaned:
            continue
        if is_foreign_line(cleaned):
            continue
        cleaned_lines.append(cleaned)

    if cleaned_lines:
        # The English title is usually the second title line; when only one useful
        # line exists, use that and carry it forward for following list pages.
        subcomponent = cleaned_lines[1] if len(cleaned_lines) > 1 else cleaned_lines[0]
        subcomponent = clean_text(subcomponent).upper()

    footer_subcomponent, footer_table_no = footer_context(text)
    if footer_table_no and not table_no:
        table_no = footer_table_no
    if footer_subcomponent and not subcomponent:
        subcomponent = footer_subcomponent

    return subcomponent, table_no


def english_lines_between(items, order_item, next_y):
    x_left = 255
    x_right = max(430, order_item["x0"] - 18)
    y_top = max(0, order_item["y0"] - 10)
    y_bottom = min(next_y, order_item["y0"] + 42)
    candidates = [
        item for item in items
        if x_left <= item["x0"] <= x_right and y_top <= item["y0"] < y_bottom
    ]
    lines = []
    for line in group_lines(candidates, y_tolerance=4):
        text = line_text(line)
        if not text:
            continue
        if valid_name_line(text):
            lines.append(text)
        elif lines:
            break
    return lines


def fallback_name_lines(items, order_item, next_y):
    y_top = max(0, order_item["y0"] - 10)
    y_bottom = min(next_y, order_item["y0"] + 36)
    candidates = [
        item for item in items
        if 95 <= item["x0"] <= 255 and y_top <= item["y0"] < y_bottom
    ]
    lines = []
    for line in group_lines(candidates, y_tolerance=4):
        text = line_text(line)
        if not text or is_foreign_line(text):
            continue
        if "designation" in ascii_key(text):
            continue
        lines.append(text)
        if len(lines) >= 2:
            break
    return lines


def find_pos_for_order(items, order_item):
    near = [
        item for item in items
        if 40 <= item["x0"] <= 105 and abs(item["y0"] - order_item["y0"]) <= 18
    ]
    near.sort(key=lambda item: abs(item["y0"] - order_item["y0"]))
    for item in near:
        pos = normalize_pos(item["text"])
        if pos:
            return pos
    return ""


def normalize_turbo_part(text):
    text = clean_text(text).replace(" ", "")
    text = text.strip("()[]-|")
    text = text.replace(",", ".").replace("~", "").replace("%", "6")
    text = text.replace("S", "5").replace("s", "5")
    text = text.replace("I", "1").replace("l", "1")
    text = re.sub(r"^5h6", "546", text, flags=re.IGNORECASE)
    text = re.sub(r"^SH6", "546", text, flags=re.IGNORECASE)
    if re.fullmatch(r"\d{6}", text):
        text = f"{text[:3]}.{text[3:]}"
    return text if re.fullmatch(r"\d{3}\.\d{3}", text) else ""


def extract_left_catalog_page(items, page_no, subcomponent, table_no):
    order_items = []
    for item in items:
        if not (45 <= item["x0"] <= 115 and 80 <= item["y0"] <= 750):
            continue
        if CATALOG_RE.match(item["text"]):
            continue
        if TURBO_PART_RE.match(item["text"]):
            normalized = normalize_turbo_part(item["text"])
            if normalized:
                order_items.append({**item, "text": normalized})

    order_items.sort(key=lambda item: item["y0"])
    if len(order_items) < 4:
        return []

    extracted = []
    for idx, order in enumerate(order_items):
        next_y = order_items[idx + 1]["y0"] - 2 if idx + 1 < len(order_items) else order["y0"] + 55
        y_top = max(0, order["y0"] - 10)
        y_bottom = min(next_y, order["y0"] + 48)
        candidates = [
            item for item in items
            if 260 <= item["x0"] <= 430 and y_top <= item["y0"] < y_bottom
        ]
        name_lines = []
        for line in group_lines(candidates, y_tolerance=4):
            text = line_text(line)
            if valid_name_line(text):
                name_lines.append(text)
            elif name_lines:
                break
        name = clean_text(" ".join(name_lines))
        if not name:
            continue
        extracted.append({
            "page_no": page_no,
            "sub_component": subcomponent,
            "table_no": table_no or "500.01",
            "pos_no": "",
            "mfg_part_no": order["text"],
            "name": name,
        })
    return extracted


def extract_portrait_page(items, page_no, subcomponent, table_no):
    orders = [
        item for item in items
        if item["x0"] >= 420 and CATALOG_RE.match(item["text"])
    ]
    orders.sort(key=lambda item: (item["y0"], item["x0"]))
    extracted = []

    for idx, order in enumerate(orders):
        next_y = orders[idx + 1]["y0"] - 2 if idx + 1 < len(orders) else order["y0"] + 55
        lines = english_lines_between(items, order, next_y)
        if not lines:
            lines = fallback_name_lines(items, order, next_y)
        name = clean_text(" ".join(lines))
        if not name:
            continue
        extracted.append({
            "page_no": page_no,
            "sub_component": subcomponent,
            "table_no": table_no or ".".join(order["text"].split(".")[:2]),
            "pos_no": find_pos_for_order(items, order),
            "mfg_part_no": order["text"],
            "name": name,
        })
    return extracted


def text_tokens(text):
    return [clean_text(token) for token in text.splitlines() if clean_text(token) and clean_text(token) != "<br>"]


def extract_rotated_list_page(text, page_no, subcomponent, table_no):
    tokens = text_tokens(text)
    start = 0
    for idx, token in enumerate(tokens):
        if token.lower().startswith("item"):
            start = idx + 1
            break

    records = []
    pending_pos = ""
    pending_name = []
    pending_qty = ""
    table_no = table_no_from_text(text, table_no)

    for token in tokens[start:]:
        if DATE_RE.match(token):
            break
        if token in {"PC", "CM", "SET"}:
            continue
        if QUANTITY_RE.match(token) and pending_name:
            pending_qty = token
            continue
        if CATALOG_RE.match(token):
            name = clean_text(" ".join(pending_name))
            if name:
                records.append({
                    "page_no": page_no,
                    "sub_component": subcomponent,
                    "table_no": table_no or ".".join(token.split(".")[:2]),
                    "pos_no": pending_pos,
                    "mfg_part_no": token,
                    "name": name,
                    "qty": pending_qty,
                })
            pending_name = []
            pending_qty = ""
            pending_pos = ""
            continue
        if not pending_name:
            pos = normalize_pos(token)
            if pos and len(token) <= 4:
                pending_pos = pos
                continue
        if token.lower() in {"catalog", "no.", "description", "quantity", "uom", "additional", "info"}:
            continue
        pending_name.append(token)

    return records


def is_rotated_list_page(text, items):
    has_headers = "Catalog" in text and "Description" in text and "Item" in text
    catalog_items = [item for item in items if CATALOG_RE.match(item["text"])]
    bottom_spread = [item for item in catalog_items if item["y0"] > 650]
    return has_headers and len(bottom_spread) >= 4


def is_left_catalog_page(items):
    left_parts = [
        item for item in items
        if 45 <= item["x0"] <= 115 and 80 <= item["y0"] <= 750 and normalize_turbo_part(item["text"])
    ]
    return len(left_parts) >= 4


def clean_subcomponent(subcomponent, table_no):
    subcomponent = clean_text(subcomponent).upper()
    if not subcomponent:
        return f"TABLE {table_no}" if table_no else "MAN B&W SPARES"
    if len(subcomponent) > 80:
        subcomponent = subcomponent[:80].strip()
    return subcomponent


def extracted_pdf_name(subcomponent):
    token = re.sub(r"[^A-Za-z0-9]+", "", subcomponent.upper())
    return f"ME{token}.PDF" if token else "MEMANBWSPARES.PDF"


def split_test6_name_details(raw_name):
    text = clean_text(raw_name).upper()
    original = text
    text = text.replace("HE XAGON", "HEXAGON")
    text = text.replace("HEXAQON", "HEXAGON")
    text = text.replace("SEREW", "SCREW")
    text = text.replace("FIQURE", "FIGURE")
    text = re.sub(r"\bFIGURE\s*\d+.*$", "", text)
    text = re.sub(r"\bDIN\b.*$", "", text)

    kept = []
    for token in text.split():
        key = ascii_key(token)
        if key in SPANISH_BREAK_WORDS and kept:
            break
        kept.append(token)
    text = clean_text(" ".join(kept))
    text = re.sub(r"^\bDE\b\s*\??\s*", "", text)
    text = re.sub(r"\b(?:CON|DEL|DE LA|DE EL)\b.*$", "", text)
    if not text:
        text = original
    return clean_text(text), "", ""


def to_template_row(record):
    raw_name = clean_text(record["name"])
    name, size, material = split_test6_name_details(raw_name)
    subcomponent = clean_subcomponent(record["sub_component"], record["table_no"])
    return [
        COMPONENT_NAME,
        subcomponent,
        MANUFACTURER,
        MODEL,
        name,
        record["mfg_part_no"],
        "",
        record.get("pos_no", ""),
        size,
        material,
        "",
        record["table_no"],
        record["page_no"],
        extracted_pdf_name(subcomponent),
        "",
        DEFAULT_UOM,
        "",
        "",
        DRAWING_PAGE_WITH_POS,
        "",
        "",
    ]


def extract_test6():
    doc = fitz.open(PDF_PATH)
    end_page = END_PAGE or len(doc)
    records = []
    last_subcomponent = ""
    last_table_no = ""

    for page_no in range(START_PAGE, min(end_page, len(doc)) + 1):
        page = doc[page_no - 1]
        text = page.get_text("text")
        items = word_items(page)
        rotated_page = is_rotated_list_page(text, items)
        subcomponent, table_no = page_context(items, text, last_subcomponent, last_table_no)
        if rotated_page:
            footer_subcomponent, footer_table_no = footer_context(text)
            if footer_subcomponent:
                subcomponent = footer_subcomponent
            if footer_table_no:
                table_no = footer_table_no
        subcomponent = clean_subcomponent(subcomponent, table_no)
        if subcomponent:
            last_subcomponent = subcomponent
        if table_no:
            last_table_no = table_no

        if rotated_page:
            page_records = extract_rotated_list_page(text, page_no, last_subcomponent, last_table_no)
        elif is_left_catalog_page(items):
            page_records = extract_left_catalog_page(items, page_no, last_subcomponent, last_table_no)
        else:
            page_records = extract_portrait_page(items, page_no, last_subcomponent, last_table_no)

        records.extend(page_records)
        print(f"Page {page_no}: {len(page_records)} rows ({last_subcomponent}, table {last_table_no})")

    doc.close()
    return records


def main():
    records = extract_test6()
    rows = [to_template_row(record) for record in records]
    os.makedirs(OUTPUT_PATH.parent, exist_ok=True)
    saved_path = ae.write_to_excel(rows, str(TEMPLATE_PATH), str(OUTPUT_PATH))
    print(f"Extracted rows: {len(rows)}")
    print(f"Output: {saved_path}")


if __name__ == "__main__":
    main()
