import cv2
import numpy as np
from typing import List, Tuple

def detect_table_cells(binary_image: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """
    Detects table cells using OpenCV morphological operations to find horizontal and vertical lines.
    
    Args:
        binary_image: A binary (black and white) numpy array. Assumes background is white and text/lines are black.
        
    Returns:
        A list of bounding boxes (x, y, w, h) for each detected cell, sorted top-to-bottom, left-to-right.
    """
    # Invert the image (so lines become white, background becomes black)
    inverted = cv2.bitwise_not(binary_image)
    
    height, width = inverted.shape
    
    # Kernel length dictates how long a line must be to be detected.
    # width // 40 is a standard heuristic for finding table lines without picking up text
    horiz_len = max(width // 40, 10)
    vert_len = max(height // 40, 10)
    
    # Define morphological kernels
    horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (horiz_len, 1))
    vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, vert_len))
    
    # Extract horizontal lines
    horiz_lines = cv2.morphologyEx(inverted, cv2.MORPH_OPEN, horiz_kernel, iterations=2)
    
    # Extract vertical lines
    vert_lines = cv2.morphologyEx(inverted, cv2.MORPH_OPEN, vert_kernel, iterations=2)
    
    # Combine lines to create a table grid mask
    table_mask = cv2.addWeighted(horiz_lines, 0.5, vert_lines, 0.5, 0.0)
    _, table_mask = cv2.threshold(table_mask, 128, 255, cv2.THRESH_BINARY)
    
    # Find contours which will represent the empty spaces (cells) inside the grid
    contours, hierarchy = cv2.findContours(table_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    
    cells = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        
        # Filter out extreme noise or the contour of the entire image/table
        if w > 20 and h > 10 and w < width * 0.9 and h < height * 0.9:
            cells.append((x, y, w, h))
            
    # Sort cells top-to-bottom, then left-to-right.
    # We group rows by y-coordinate (allowing a small 10-pixel variance for row alignment)
    cells = sorted(cells, key=lambda b: (b[1] // 10, b[0]))
    
    return cells

if __name__ == "__main__":
    # Test script if run directly
    print("table_detector.py is ready to be imported.")
