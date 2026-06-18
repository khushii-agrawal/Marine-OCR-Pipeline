from pathlib import Path
import run_test12 as base
from local_engine.ocr_extractor import OCRExtractor

PDF_PATH = Path(r"D:\OCRProject\test\Test16\your_pdf.pdf")

extractor = OCRExtractor()

output_dir = Path("inspect_test16")
output_dir.mkdir(exist_ok=True)

for page_no in [1,3,5,8,11]:
    items = base.ocr_items(PDF_PATH, page_no - 1, extractor)

    with open(output_dir / f"page_{page_no}.txt", "w", encoding="utf-8") as f:
        for item in sorted(items, key=lambda x: (x["rel_y0"], x["rel_x0"])):
            f.write(
                f"{item['rel_x0']:.3f}\t"
                f"{item['rel_y0']:.3f}\t"
                f"{item['text']}\n"
            )