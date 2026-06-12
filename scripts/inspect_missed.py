import os
import sys

script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(script_dir, 'local_engine'))
from pdf_converter import pdf_page_to_image
from ocr_extractor import OCRExtractor

def test_pages():
    pdf_path = os.path.join(script_dir, "..", "input", "AE D2842LE spare parts manual 1.pdf")
    extractor = OCRExtractor()
    
    # Check pages 15, 45, 80
    for page_idx in [14, 44, 79]:
        print(f"\n--- PAGE {page_idx + 1} ---")
        img = pdf_page_to_image(pdf_path, page_idx, dpi=200)
        ocr_results = extractor.extract_text(img)
        page_height, page_width = img.shape[:2]
        
        for res in ocr_results:
            box, (text, conf) = res
            ys = [p[1] for p in box]
            rel_y = ((min(ys) + max(ys)) / 2) / page_height
            x_min = min(p[0] for p in box)
            x_max = max(p[0] for p in box)
            print(f"[{rel_y:.3f}] X:{x_min:.1f}-{x_max:.1f} | {text}")

if __name__ == '__main__':
    test_pages()
