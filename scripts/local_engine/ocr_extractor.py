import re
from typing import Dict, Any, List, Tuple
from paddleocr import PaddleOCR
import numpy as np

class OCRExtractor:
    def __init__(self, lang='en', use_angle_cls=True):
        """
        Initializes the PaddleOCR engine.
        """
        # Set show_log=False to prevent PaddleOCR from printing too much to the console
        self.ocr = PaddleOCR(use_angle_cls=use_angle_cls, lang=lang, show_log=False)
        
    def extract_text(self, image: np.ndarray) -> List[Tuple[List[List[float]], Tuple[str, float]]]:
        """
        Extracts text from an image using PaddleOCR.
        
        Returns:
            A list of results where each result is:
            [box_coordinates, (text, confidence)]
            box_coordinates: [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
        """
        result = self.ocr.ocr(image, cls=True)
        # PaddleOCR returns a list of lists (for each batch/image), we want the first image's results
        if not result or not result[0]:
            return []
        return result[0]
        
    def find_drawing_and_subcomponent(self, ocr_results: List[Any]) -> Tuple[str, str]:
        """
        Applies heuristics to find the Drawing Number and Sub-Component Name.
        """
        drwg_no = ""
        sub_component = ""
        
        stop_words = ["designation", "name of spare", "component name", "remarks", "material", "item no", "qty", "code no"]
        
        for res in ocr_results:
            box, (text, conf) = res
            
            # 1. Heuristic for Drawing No: e.g., 0570-0100-0001
            # Contains '-' and digits and is sufficiently long
            if "-" in text and any(c.isdigit() for c in text) and len(text) >= 10:
                if not drwg_no:
                    cleaned = text.replace(" ", "")
                    if "-" in cleaned:
                        drwg_no = cleaned
                        
            # 2. Heuristic for Sub-Component: e.g., "Safety Equipment"
            # Titlecased, no numbers, > 5 characters, doesn't contain manufacturer names or table headers
            if text.istitle() and not any(c.isdigit() for c in text) and len(text) > 5:
                if not sub_component and "HYUNDAI" not in text.upper() and "MAN" not in text.upper():
                    if text.strip().lower() not in stop_words:
                        sub_component = text.strip()
                        
        return drwg_no, sub_component

if __name__ == "__main__":
    print("ocr_extractor.py is ready to be imported.")
