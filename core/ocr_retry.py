"""Cell-level OCR retry helpers for fields that fail the Medium gate."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

import cv2
import numpy as np

logging.getLogger("ppocr").setLevel(logging.ERROR)


@dataclass(frozen=True)
class OCRRetryResult:
    text: str
    confidence: float
    engine: str
    attempted_engines: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "engine": self.engine,
            "attempted_engines": self.attempted_engines,
        }


def enhance_cell_crop(cell_crop: Any, scale: float = 2.0) -> np.ndarray:
    """Upscale and lightly sharpen one cell crop before OCR retry."""
    image = np.asarray(cell_crop)
    if image.size == 0:
        raise ValueError("cell_crop is empty")

    if scale != 1.0:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image

    blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=1.0)
    sharpened = cv2.addWeighted(gray, 1.5, blurred, -0.5, 0)
    return sharpened


def _parse_paddle_result(result: Any) -> tuple[str, float]:
    if not result:
        return "", 0.0
    lines = result[0] if isinstance(result, list) and result and isinstance(result[0], list) else result
    texts: list[str] = []
    confidences: list[float] = []
    for line in lines or []:
        try:
            text, confidence = line[1]
        except (TypeError, IndexError, ValueError):
            continue
        if text:
            texts.append(str(text))
            confidences.append(float(confidence))
    if not texts:
        return "", 0.0
    return " ".join(texts).strip(), sum(confidences) / len(confidences)


def _run_paddle(image: np.ndarray, paddle_ocr: Any) -> tuple[str, float]:
    if paddle_ocr is None:
        try:
            from paddleocr import PaddleOCR
        except ImportError:
            return "", 0.0
        paddle_ocr = PaddleOCR(use_angle_cls=True, lang="en")
    return _parse_paddle_result(paddle_ocr.ocr(image, cls=True))


def _run_easyocr(image: np.ndarray, easy_reader: Any) -> tuple[str, float]:
    if easy_reader is None:
        try:
            import easyocr
        except ImportError:
            return "", 0.0
        easy_reader = easyocr.Reader(["en"], gpu=False)

    results = easy_reader.readtext(image, detail=1, paragraph=False)
    texts = []
    confidences = []
    for result in results:
        try:
            _, text, confidence = result
        except (TypeError, ValueError):
            continue
        if text:
            texts.append(str(text))
            confidences.append(float(confidence))
    if not texts:
        return "", 0.0
    return " ".join(texts).strip(), sum(confidences) / len(confidences)


def retry_cell_ocr(
    cell_crop: Any,
    min_confidence: float = 0.75,
    paddle_ocr: Any = None,
    easy_reader: Any = None,
    use_easyocr_fallback: bool = False,
    paddle_runner: Callable[[np.ndarray, Any], tuple[str, float]] = _run_paddle,
    easyocr_runner: Callable[[np.ndarray, Any], tuple[str, float]] = _run_easyocr,
) -> dict[str, Any]:
    """
    Retry OCR on a single failing cell crop.

    The caller must pass the individual cell crop, not a full row or page image.
    PaddleOCR runs first with enhanced preprocessing. EasyOCR is an opt-in
    fallback only; Phase 4 defaults to the Option B Paddle-only retry path.
    """
    enhanced = enhance_cell_crop(cell_crop)
    attempted: list[str] = []

    attempted.append("paddleocr")
    paddle_text, paddle_confidence = paddle_runner(enhanced, paddle_ocr)
    best = OCRRetryResult(paddle_text, paddle_confidence, "paddleocr", attempted.copy())
    if paddle_confidence >= min_confidence:
        return best.as_dict()

    if use_easyocr_fallback:
        attempted.append("easyocr")
        easy_text, easy_confidence = easyocr_runner(enhanced, easy_reader)
        if easy_confidence > best.confidence:
            best = OCRRetryResult(easy_text, easy_confidence, "easyocr", attempted.copy())
        else:
            best = OCRRetryResult(best.text, best.confidence, best.engine, attempted.copy())

    return best.as_dict()
