import cv2
import numpy as np
from typing import List, Tuple, Dict


def group_ocr_into_rows(ocr_results: list, page_width: int, page_height: int, y_tolerance: int = 15) -> List[List]:
    """
    Groups OCR text blocks into rows based on their vertical (y) position.
    Filters out noise regions (headers, footers, vertical labels).
    
    Args:
        ocr_results: Raw PaddleOCR results [(box, (text, conf)), ...]
        page_width: Width of the page image in pixels.
        page_height: Height of the page image in pixels.
        y_tolerance: Maximum vertical pixel difference to consider blocks on the same row.
        
    Returns:
        A list of rows, where each row is a list of item dicts sorted left-to-right.
    """
    if not ocr_results:
        return []
    
    # Extract center coordinates and text, filtering noise
    items = []
    for res in ocr_results:
        box, (text, conf) = res
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        center_x = (min(xs) + max(xs)) / 2
        center_y = (min(ys) + max(ys)) / 2
        rel_x = center_x / page_width
        rel_y = center_y / page_height
        
        # Filter out noise regions:
        # - Far left (< 0.20): Vertical text like "Safety Equipment"
        # - Bottom of page (> 0.70): Repeated drawing number, date, "Plate" text
        # - Top of page (< 0.10): "HYUNDAI", "MAN B&W", drawing number header
        if rel_x < 0.20:
            continue
        if rel_y > 0.70:
            continue
        if rel_y < 0.10:
            continue
            
        items.append({
            "cx": center_x, "cy": center_y,
            "rel_x": rel_x, "rel_y": rel_y,
            "text": text.strip(), "conf": conf
        })
    
    if not items:
        return []
    
    # Sort by y-coordinate
    items.sort(key=lambda i: i["cy"])
    
    # Group into rows
    rows = []
    current_row = [items[0]]
    
    for item in items[1:]:
        if abs(item["cy"] - current_row[-1]["cy"]) <= y_tolerance:
            current_row.append(item)
        else:
            # Sort row left-to-right
            current_row.sort(key=lambda i: i["cx"])
            rows.append(current_row)
            current_row = [item]
    
    if current_row:
        current_row.sort(key=lambda i: i["cx"])
        rows.append(current_row)
    
    return rows


def assign_columns(rows: List[List]) -> List[Dict[int, str]]:
    """
    Assigns OCR text to columns (Item No, Qty, Designation) based on horizontal position.
    
    Based on actual measured positions from the marine manuals:
    - Item No:     RelX 0.30 - 0.375  (centered around 0.338)
    - Qty:         RelX 0.375 - 0.45  (centered around 0.397)
    - Designation: RelX > 0.45        (centered around 0.47-0.52)
    
    Returns:
        A list of dicts, one per row, mapping column_index -> text.
    """
    table_data = []
    
    for row in rows:
        row_data = {}
        
        for item in row:
            rel_x = item["rel_x"]
            
            if rel_x < 0.375:
                # Item No column (RelX ~0.338)
                if 0 not in row_data:
                    row_data[0] = item["text"]
                else:
                    row_data[0] += " " + item["text"]
            elif rel_x < 0.45:
                # Qty column (RelX ~0.397)
                if 1 not in row_data:
                    row_data[1] = item["text"]
                else:
                    row_data[1] += " " + item["text"]
            else:
                # Designation column (RelX > 0.45)
                if 2 not in row_data:
                    row_data[2] = item["text"]
                else:
                    row_data[2] += " " + item["text"]
        
        if any(row_data.values()):
            table_data.append(row_data)
    
    return table_data


if __name__ == "__main__":
    print("table_detector.py is ready to be imported.")
