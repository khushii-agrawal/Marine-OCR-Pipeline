import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.append(str((SCRIPT_DIR / "local_engine").resolve()))

from ocr_extractor import OCRExtractor
from pdf_converter import pdf_page_to_image

PDF_PATH = PROJECT_ROOT / "Test" / "Test 17" / "Emergency diesel engine 1 1.pdf"

def main():
    extractor = OCRExtractor()
    candidate_pages = []
    
    # We will sample every 10th page first, or maybe just go through all pages quickly
    # Actually, let's just go through all pages but downsample DPI to make it faster
    import fitz
    doc = fitz.open(PDF_PATH)
    total_pages = len(doc)
    doc.close()
    
    print(f"Total pages: {total_pages}")
    for i in range(total_pages):
        try:
            image = pdf_page_to_image(str(PDF_PATH), i, dpi=72) # very low dpi just to catch large words
            text_blocks = []
            for box, (text, conf) in extractor.extract_text(image):
                text_blocks.append(text.upper())
            full_text = " ".join(text_blocks)
            if "PART" in full_text or "SPARE" in full_text or "ITEM" in full_text or "DESCRIPTION" in full_text:
                candidate_pages.append(i + 1)
                print(f"Page {i+1} might have parts: {full_text[:100]}")
        except Exception as e:
            print(f"Error on page {i+1}: {e}")
            
    print(f"Candidate pages: {candidate_pages}")

if __name__ == '__main__':
    main()
