import os
import fitz
import openpyxl
from pdf_converter import pdf_page_to_image, preprocess_for_ocr
from table_detector import detect_table_cells
from ocr_extractor import OCRExtractor

# --- Configuration Constants ---
COMPONENT_NAME = "Main Engine"
MANUFACTURER = "HYUNDAI MAN B&W"
MODEL = "6G80ME-C9.2"
MANUAL_PDF_NAME = "VOLUME I.pdf"
DEFAULT_UOM = "Pcs"
DRAWING_PAGE_WITH_POS = "Yes"

def map_text_to_cells(cells, ocr_results):
    """
    Maps PaddleOCR text bounding boxes to OpenCV detected table cells.
    """
    if not cells:
        return []

    # Reconstruct the grid by grouping cells into rows
    rows = []
    current_row = []
    last_y = -1
    for cell in cells:
        x, y, w, h = cell
        # Allow 15px variance for row grouping
        if last_y == -1 or abs(y - last_y) < 15:
            current_row.append(cell)
        else:
            # Sort the row by x-coordinate to ensure columns are in order
            rows.append(sorted(current_row, key=lambda c: c[0]))
            current_row = [cell]
        last_y = y
    if current_row:
        rows.append(sorted(current_row, key=lambda c: c[0]))

    table_data = []
    for row in rows:
        row_data = {}
        for col_idx, cell in enumerate(row):
            cx, cy, cw, ch = cell
            cell_text = []
            
            for res in ocr_results:
                box, (text, conf) = res
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                tx, ty, tw, th = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
                
                # Check center of text box is inside the cell
                center_x = tx + tw / 2
                center_y = ty + th / 2
                
                if cx <= center_x <= cx + cw and cy <= center_y <= cy + ch:
                    cell_text.append(text)
            
            row_data[col_idx] = " ".join(cell_text).strip()
        
        # Only add row if it's not completely empty
        if any(row_data.values()):
            table_data.append(row_data)
            
    return table_data

def process_pdf_locally(pdf_path):
    print("Initializing Local PaddleOCR Engine...")
    extractor = OCRExtractor()
    
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    extracted_rows = []
    
    # Process in 2-page chunks (Drawing page, Table page)
    for i in range(0, total_pages, 2):
        print(f"Processing pages {i+1} and {min(i+2, total_pages)}...")
        
        drwg_no = ""
        sub_component = ""
        
        # 1. Process Drawing Page
        drwg_img = pdf_page_to_image(pdf_path, i, dpi=200) # 200 dpi is usually enough for text
        ocr_results_drwg = extractor.extract_text(drwg_img)
        drwg_no, sub_component = extractor.find_drawing_and_subcomponent(ocr_results_drwg)
        
        # 2. Process Table Page (if exists)
        if i + 1 < total_pages:
            table_page_no = i + 2
            table_img = pdf_page_to_image(pdf_path, i + 1, dpi=200)
            binary_img = preprocess_for_ocr(table_img)
            
            print(f"  Detecting table cells on page {table_page_no}...")
            cells = detect_table_cells(binary_img)
            
            print(f"  Running OCR on page {table_page_no}...")
            ocr_results_table = extractor.extract_text(table_img)
            
            print(f"  Mapping text to {len(cells)} cells...")
            table_data = map_text_to_cells(cells, ocr_results_table)
            
            # Skip header row (assumed to be row 0)
            for row_idx in range(1, len(table_data)):
                row_cols = table_data[row_idx]
                
                # Basic column assumption: 0=ItemNo, 1=Qty, 2=Designation
                pos_no = row_cols.get(0, "")
                qty = row_cols.get(1, "")
                name_of_spare = row_cols.get(2, "")
                
                if not pos_no or pos_no == "-":
                    continue
                    
                mfg_part_no = f"{drwg_no}-{pos_no}" if drwg_no else ""
                
                row_data_arr = [
                    COMPONENT_NAME,           # A: Component Name
                    sub_component,            # B: Sub Component Name
                    MANUFACTURER,             # C: Manufacturer
                    MODEL,                    # D: Model
                    name_of_spare,            # E: Name Of Spare
                    mfg_part_no,              # F: MfgPart No
                    drwg_no,                  # G: Drwg.No
                    pos_no,                   # H: Pos. No.
                    "",                       # I: Size & Dimension
                    "",                       # J: Material
                    "",                       # K: Remarks
                    "",                       # L: Other details if any
                    table_page_no,            # M: Page No
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
                
    doc.close()
    return extracted_rows

def write_to_excel(extracted_rows, template_path, output_path):
    print(f"Writing {len(extracted_rows)} rows to Excel...")
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
