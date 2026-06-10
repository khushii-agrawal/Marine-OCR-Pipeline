import fitz  # PyMuPDF
import numpy as np
import cv2

def pdf_page_to_image(pdf_path: str, page_index: int, dpi: int = 300) -> np.ndarray:
    """
    Converts a specific page of a PDF into a numpy array (BGR format) suitable for OpenCV.
    
    Args:
        pdf_path: Path to the PDF file.
        page_index: 0-based index of the page to extract.
        dpi: Resolution of the extracted image (higher = better OCR).
        
    Returns:
        A numpy ndarray representing the image in BGR format.
    """
    doc = fitz.open(pdf_path)
    if page_index < 0 or page_index >= len(doc):
        raise IndexError(f"Page index {page_index} out of bounds for document with {len(doc)} pages.")
        
    page = doc.load_page(page_index)
    pix = page.get_pixmap(dpi=dpi)
    
    # Convert PyMuPDF pixmap to numpy array
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    
    # OpenCV expects BGR, but PyMuPDF outputs RGB/RGBA
    if pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    elif pix.n == 3:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    elif pix.n == 1:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        
    doc.close()
    return img

def preprocess_for_ocr(image: np.ndarray) -> np.ndarray:
    """
    Applies basic preprocessing (grayscale + adaptive thresholding) 
    to make text pop out clearly for PaddleOCR.
    """
    # Convert to grayscale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    
    # Apply adaptive thresholding to handle variations in page brightness
    binary = cv2.adaptiveThreshold(
        gray, 255, 
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
        cv2.THRESH_BINARY, 11, 2
    )
    
    return binary

if __name__ == "__main__":
    # Quick test function if someone runs this file directly
    import os
    test_pdf = "../../input/test_pages.pdf"
    if os.path.exists(test_pdf):
        print(f"Testing PDF conversion on {test_pdf}...")
        img = pdf_page_to_image(test_pdf, 0, dpi=150)
        print(f"Successfully loaded image with shape: {img.shape}")
        
        # Test preprocessing
        processed = preprocess_for_ocr(img)
        print(f"Successfully preprocessed image. New shape: {processed.shape}")
