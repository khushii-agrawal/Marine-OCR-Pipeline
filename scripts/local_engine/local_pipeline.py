import os
import fitz
import openpyxl
from pdf_converter import pdf_page_to_image, preprocess_for_ocr
from table_detector import group_ocr_into_rows, assign_columns
from ocr_extractor import OCRExtractor

# --- Configuration Constants ---
COMPONENT_NAME = "Main Engine"
MANUFACTURER = "HYUNDAI MAN B&W"
MODEL = "6G80ME-C9.2"
MANUAL_PDF_NAME = "VOLUME I.pdf"
DEFAULT_UOM = "Pcs"
DRAWING_PAGE_WITH_POS = "Yes"

# Table header keywords to detect table pages and skip header rows
HEADER_KEYWORDS = {"item no", "item", "qty", "designation", "code no", "name", "no"}


def is_table_page(ocr_results, page_width, page_height):
    """
    Detect if a page is a table page by looking for table header keywords
    ("Item no", "Qty", "Designation") in the expected header region.
    """
    for res in ocr_results:
        box, (text, conf) = res
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        rel_y = ((min(ys) + max(ys)) / 2) / page_height

        # Table headers are typically in the top 20% of the page
        if rel_y < 0.20:
            if text.strip().lower() in {"item no", "qty", "designation", "item"}:
                return True
    return False


def is_header_row(row_data):
    """Check if a row is a table header by looking for header keywords."""
    for col_idx, text in row_data.items():
        if text.strip().lower() in HEADER_KEYWORDS:
            return True
    return False


def is_valid_item_no(text):
    """Check if text looks like a valid item number (e.g., 012, 024, 036)."""
    cleaned = text.strip().replace(" ", "")
    return cleaned.isdigit() and len(cleaned) >= 2


def process_pdf_locally(pdf_path):
    print("Initializing Local PaddleOCR Engine...")
    extractor = OCRExtractor()
    
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    extracted_rows = []
    
    # Track the last known drawing number and sub-component
    last_drwg_no = ""
    last_sub_component = ""
    
    for page_idx in range(total_pages):
        page_no = page_idx + 1  # 1-based
        print(f"\nProcessing page {page_no}/{total_pages}...")
        
        img = pdf_page_to_image(pdf_path, page_idx, dpi=200)
        page_height, page_width = img.shape[:2]
        
        ocr_results = extractor.extract_text(img)
        
        if not ocr_results:
            print(f"  No text found on page {page_no}.")
            continue
        
        # Determine if this is a table page or a drawing page
        if is_table_page(ocr_results, page_width, page_height):
            # === TABLE PAGE ===
            print(f"  [TABLE PAGE] Using Drawing No: {last_drwg_no}, Sub-Component: {last_sub_component}")
            
            rows = group_ocr_into_rows(ocr_results, page_width, page_height, y_tolerance=15)
            table_data = assign_columns(rows)
            
            print(f"  Found {len(table_data)} rows of text.")
            
            for row_cols in table_data:
                if is_header_row(row_cols):
                    continue
                
                pos_no = row_cols.get(0, "").strip().replace(" ", "")
                qty = row_cols.get(1, "").strip()
                name_of_spare = row_cols.get(2, "").strip()
                
                if not is_valid_item_no(pos_no):
                    continue
                    
                mfg_part_no = f"{last_drwg_no}-{pos_no}" if last_drwg_no else ""
                
                print(f"    Row: Pos={pos_no}, Qty={qty}, Name={name_of_spare}")
                
                row_data_arr = [
                    COMPONENT_NAME,           # A: Component Name
                    last_sub_component,       # B: Sub Component Name
                    MANUFACTURER,             # C: Manufacturer
                    MODEL,                    # D: Model
                    name_of_spare,            # E: Name Of Spare
                    mfg_part_no,              # F: MfgPart No
                    last_drwg_no,             # G: Drwg.No
                    pos_no,                   # H: Pos. No.
                    "",                       # I: Size & Dimension
                    "",                       # J: Material
                    "",                       # K: Remarks
                    "",                       # L: Other details if any
                    page_no,                  # M: Page No
                    MANUAL_PDF_NAME,          # N: Manual Pdf Name
                    "",                       # O: Referance No 1
                    DEFAULT_UOM,              # P: Uom
                    "",                       # Q: Extracted Pdf name if required
                    "",                       # R: Drawing Page Without Pos.No
                    DRAWING_PAGE_WITH_POS,    # S: Drawing Page With Pos.No 
                    "",                       # T: Colour Identification
                    ""                        # U: Component Linking
                ]
                extracted_rows.append(row_data_arr)
        else:
            # === DRAWING PAGE ===
            drwg_no, sub_component = extractor.find_drawing_and_subcomponent(ocr_results)
            if drwg_no:
                last_drwg_no = drwg_no
            
            # Always update sub_component for a new drawing page to prevent bleed-over from previous pages
            last_sub_component = sub_component if sub_component else ""
            
            print(f"  [DRAWING PAGE] Drawing No: {last_drwg_no}, Sub-Component: {last_sub_component}")
                
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
            
    wb.save(output_path)
    print(f"Saved Excel to {output_path}")


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.join(script_dir, "..", "..")
    
    pdf_path = os.path.join(project_root, "input", "test_pages.pdf")
    template_path = os.path.join(project_root, "template", "Spares_Capture_Template_Ver12 2.xlsm")
    output_path = os.path.join(project_root, "output", "VOLUME_I_extracted_local.xlsm")
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    if os.path.exists(pdf_path):
        rows = process_pdf_locally(pdf_path)
        write_to_excel(rows, template_path, output_path)
    else:
        print(f"Test PDF not found at {pdf_path}")
