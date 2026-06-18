import sys
from pathlib import Path
sys.path.append(str(Path(r'd:\OCRProject\scripts\local_engine')))
from ocr_extractor import OCRExtractor
from pdf_converter import pdf_page_to_image

extractor = OCRExtractor()
pdf_path = r'd:\OCRProject\Test\Test 17\Emergency diesel engine 1 1.pdf'
img = pdf_page_to_image(pdf_path, 9, dpi=180) # page 10
height, width = img.shape[:2]

items = []
for box, (text, conf) in extractor.extract_text(img):
    xs = [point[0] for point in box]
    ys = [point[1] for point in box]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    rel_x = ((x0 + x1) / 2) / width
    rel_y = ((y0 + y1) / 2) / height
    items.append({
        "text": text,
        "x0": x0, "x1": x1, "y0": y0, "y1": y1,
        "rel_x": rel_x, "rel_y": rel_y,
        "cy": (y0 + y1) / 2, "cx": (x0 + x1) / 2,
    })

# Group rows
rows = []
current = []
current_y = None
for item in sorted(items, key=lambda i: (i["cy"], i["cx"])):
    if current_y is None or abs(item["cy"] - current_y) <= 14:
        current.append(item)
        current_y = item["cy"] if current_y is None else (current_y + item["cy"]) / 2
    else:
        current.sort(key=lambda i: i["cx"])
        rows.append(current)
        current = [item]
        current_y = item["cy"]
if current:
    current.sort(key=lambda i: i["cx"])
    rows.append(current)

for row in rows:
    print(" | ".join(f"{i['text']} ({i['rel_x']:.2f})" for i in row))
