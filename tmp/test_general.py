import sys
from pathlib import Path

# Add scripts to sys.path
SCRIPT_DIR = Path("d:/OCRProject/scripts")
sys.path.insert(0, str(SCRIPT_DIR))

import general_pipeline

pdf_path = "d:/OCRProject/test/Test 13/M-212-M0000011-Centrifugal Pump REV1.1 (3).pdf"
pages = [4, 5, 13, 40]

rows = general_pipeline.process_pdf(pdf_path, pages)
print(f"Extracted {len(rows)} rows.")
for row in rows:
    print(f"Page {row[12]} | Pos: {row[7]} | Part: {row[5]} | Name: {row[4]} | Qty: {row[11]} | Sub: {row[1]}")
