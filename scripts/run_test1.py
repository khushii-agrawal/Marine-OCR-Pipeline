import os
import re
from collections import OrderedDict
from pathlib import Path

import fitz
import openpyxl


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = PROJECT_ROOT / "test" / "Test 1"
PDF_PATH = TEST_DIR / "23000143 11L REV 1 AS BUILT 5-12-2013 Pg No 47-53.pdf"
TEMPLATE_PATH = PROJECT_ROOT / "template" / "Spares_Capture_Template_Ver12 2.xlsm"
OUTPUT_PATH = PROJECT_ROOT / "output" / "Test1_extracted.xlsm"

COMPONENT = "Main Distribution Board 3X230V"
SUB_COMPONENT = "Main Distribution Board 3X230V"
MANUAL_PDF_NAME = "23000143 11L REV 1 AS BUILT 5-12-2013.pdf"
EXTRACTED_PDF_NAME = "MD_MAINDISTRIBUTIONBOARD3X230V.PDF"
DEFAULT_UOM = "Pcs"

PARTS_PAGES = range(47, 54)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


def titleish(value):
    text = clean_text(value)
    if not text:
        return ""
    replacements = {
        "CIRC.": "Circuit",
        "AUX.": "Auxiliary",
        "PROT.": "Prot.",
        "THER.": "Ther.",
        "MAGN": "Magn",
        "CONN.": "Conn.",
        "CONTACTOR": "Contactor",
        "CONTACT": "Contact",
        "CIRCUIT": "Circuit",
        "BREAKER": "Breaker",
        "PILOT": "Pilot",
        "LIGHT": "Light",
        "SELECTOR": "Selector",
        "SWITCH": "Switch",
        "TERMINAL": "Terminal",
        "END-BRACKET": "End-Bracket",
        "END": "End",
        "PART.": "Part.",
        "CURRENT": "Current",
        "TRAFO": "Trafo",
        "COMPACT": "Compact",
        "FRAME": "Frame",
        "POWER": "Power",
        "SUPPLY": "Supply",
        "GLASS": "Glass",
        "FUSE": "Fuse",
        "FAST": "Fast",
        "CABINET": "Cabinet",
        "HEATER": "Heater",
        "HYGRO": "Hygro",
        "CONTROL": "Control",
        "FAN": "Fan",
        "MOTOR": "Motor",
        "OVER": "Over",
        "VOLTAGE": "Voltage",
        "ELEMENT": "Element",
    }
    words = []
    for token in text.split():
        words.append(replacements.get(token.upper(), token[:1].upper() + token[1:].lower()))
    text = " ".join(words)
    text = text.replace("+", " Plus ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_part_number(value):
    text = clean_text(value)
    text = text.replace(" // ", "/").replace("//", "/")
    text = text.replace("\\", "/")
    text = text.replace("FT2", "PT2")
    text = text.replace("UT4-HESI(5X20)", "UT4-HESI")
    return text


def normalize_model(value):
    text = clean_text(value)
    text = text.replace("\\", "/")
    text = text.replace("FT2", "PT2")
    text = text.replace("PHOE.UT4-HESI(5X20)", "PHOE.UT4-HESI")
    return text


def page_rows(page, pdf_page_no):
    words = page.get_text("words")
    body_words = [w for w in words if 100 <= w[1] <= 820]
    rows = OrderedDict()

    for x0, y0, x1, y1, word, *_ in sorted(body_words, key=lambda w: (w[1], w[0])):
        y_key = round(y0 / 3) * 3
        rows.setdefault(y_key, []).append((x0, word))

    extracted = []
    for _, row_words in rows.items():
        row_words.sort(key=lambda item: item[0])
        cols = {
            "device": [],
            "qty": [],
            "desc": [],
            "nav": [],
            "manufacturer": [],
            "model": [],
            "part": [],
            "drawing_page": [],
        }
        for x, word in row_words:
            if 15 <= x < 130:
                cols["device"].append(word)
            elif 135 <= x < 170:
                cols["qty"].append(word)
            elif 170 <= x < 560:
                cols["desc"].append(word)
            elif 560 <= x < 630:
                cols["nav"].append(word)
            elif 630 <= x < 760:
                cols["manufacturer"].append(word)
            elif 760 <= x < 930:
                cols["model"].append(word)
            elif 930 <= x < 1090:
                cols["part"].append(word)
            elif 1090 <= x:
                cols["drawing_page"].append(word)

        device = clean_text(" ".join(cols["device"]))
        if not re.match(r"^-\d{3}[A-Z]\d+\.", device):
            continue

        extracted.append({
            "source_page": pdf_page_no,
            "device": device,
            "qty": clean_text(" ".join(cols["qty"])),
            "description": clean_text(" ".join(cols["desc"])),
            "nav": clean_text(" ".join(cols["nav"])),
            "manufacturer": clean_text(" ".join(cols["manufacturer"])),
            "model": normalize_model(" ".join(cols["model"])),
            "part": normalize_part_number(" ".join(cols["part"])),
            "drawing_page": clean_text(" ".join(cols["drawing_page"])),
        })

    return extracted


def is_usable_record(record):
    return bool(record["nav"] and record["part"] and record["model"])


def consolidate(records):
    grouped = OrderedDict()
    for record in records:
        if not is_usable_record(record):
            continue

        key = (
            record["nav"],
            record["manufacturer"].upper(),
            record["model"].upper(),
            record["part"].upper(),
            record["description"].upper(),
        )
        if key not in grouped:
            grouped[key] = dict(record)
            grouped[key]["devices"] = []
        grouped[key]["devices"].append(record["device"])

    return list(grouped.values())


def device_details(record):
    pieces = [f"NAV.No:{record['nav']}"]
    for idx, device in enumerate(record["devices"]):
        prefix = "Device:" if idx == 0 else ""
        pieces.append(f"{prefix}{device}")
    return "\\".join(pieces)


def to_template_row(record):
    return [
        COMPONENT,
        SUB_COMPONENT,
        record["manufacturer"].upper(),
        "",
        titleish(record["description"]),
        record["part"],
        None,
        "",
        "",
        "",
        "",
        device_details(record),
        str(record["source_page"]),
        MANUAL_PDF_NAME,
        f"Model: {record['model']}",
        DEFAULT_UOM,
        EXTRACTED_PDF_NAME,
        "Yes",
        "",
        "",
        "",
    ]


def extract_test1():
    doc = fitz.open(PDF_PATH)
    records = []
    for page_no in PARTS_PAGES:
        if page_no <= len(doc):
            records.extend(page_rows(doc[page_no - 1], page_no))
    doc.close()
    return [to_template_row(record) for record in consolidate(records)]


def write_workbook(rows):
    wb = openpyxl.load_workbook(TEMPLATE_PATH, keep_vba=True)
    ws = wb.active
    for row_idx, row in enumerate(rows, start=3):
        for col_idx, value in enumerate(row, start=1):
            ws.cell(row=row_idx, column=col_idx).value = value
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUTPUT_PATH)
    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    write_workbook(extract_test1())
