from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


LAYOUT_CLASSES = [
    "drawing_material_list",
    "drawing_only",
    "index_page",
    "repair_kit",
    "spare_parts_table",
]

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


@dataclass(frozen=True)
class PageFeatures:
    foreground_density: float
    text_density: float
    text_components_per_mp: float
    line_density: float
    hv_line_density: float
    image_area_ratio: float


class LayoutClassifier:
    def __init__(
        self,
        model_path: str | Path = "model.onnx",
        confidence_threshold: float = 0.6,
        class_names: Optional[list[str]] = None,
        metadata_path: str | Path | None = None,
    ):
        self.model_path = Path(model_path)
        self.confidence_threshold = confidence_threshold
        self.class_names = class_names or self._load_class_names(metadata_path) or LAYOUT_CLASSES
        self.session = None
        self.input_name = "input"
        self.output_name = "logits"
        self._load_model()

    def _load_class_names(self, metadata_path):
        candidates = []
        if metadata_path:
            candidates.append(Path(metadata_path))
        candidates.append(self.model_path.with_name("model_metadata.json"))
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            class_names = data.get("class_names")
            if isinstance(class_names, list) and set(class_names) == set(LAYOUT_CLASSES):
                return class_names
        return None

    def _load_model(self):
        if not self.model_path.exists():
            return
        try:
            import onnxruntime as ort
        except ImportError:
            return
        self.session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

    def classify(self, page_image) -> tuple[str, float]:
        model_label = None
        model_confidence = 0.0

        if self.session is not None:
            logits = self.session.run([self.output_name], {self.input_name: self._preprocess(page_image)})[0]
            probs = self._softmax(logits[0])
            pred_idx = int(np.argmax(probs))
            model_label = self.class_names[pred_idx]
            model_confidence = float(probs[pred_idx])
            if model_confidence >= self.confidence_threshold:
                return model_label, model_confidence

        heuristic_label, heuristic_confidence = heuristic_classify(page_image)
        if model_label is None:
            return heuristic_label, heuristic_confidence
        return heuristic_label, min(heuristic_confidence, self.confidence_threshold)

    @staticmethod
    def _softmax(logits):
        logits = logits.astype(np.float32)
        logits = logits - np.max(logits)
        exp = np.exp(logits)
        return exp / np.sum(exp)

    @staticmethod
    def _preprocess(page_image):
        image = ensure_bgr(page_image)
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (256, 256), interpolation=cv2.INTER_AREA)
        start = (256 - 224) // 2
        cropped = resized[start:start + 224, start:start + 224]
        tensor = cropped.astype(np.float32) / 255.0
        tensor = (tensor - IMAGENET_MEAN) / IMAGENET_STD
        tensor = np.transpose(tensor, (2, 0, 1))[None, ...]
        return tensor.astype(np.float32)


def classify(page_image) -> tuple[str, float]:
    return LayoutClassifier().classify(page_image)


def ensure_bgr(page_image):
    image = np.asarray(page_image)
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.ndim == 3 and image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    if image.ndim == 3 and image.shape[2] == 3:
        return image
    raise ValueError(f"Unsupported page image shape: {image.shape}")


def extract_page_features(page_image) -> PageFeatures:
    image = ensure_bgr(page_image)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    max_dim = max(gray.shape[:2])
    if max_dim > 1400:
        scale = 1400 / max_dim
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    height, width = gray.shape[:2]
    page_area = float(height * width)
    mp = page_area / 1_000_000.0

    foreground = gray < 210
    foreground_density = float(np.count_nonzero(foreground) / page_area)

    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        15,
    )

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    text_area = 0
    text_components = 0
    large_area = 0
    for idx in range(1, num_labels):
        x, y, w, h, area = stats[idx]
        if area <= 0:
            continue
        aspect = w / max(h, 1)
        if 6 <= area <= 900 and 4 <= h <= 70 and 2 <= w <= 220 and 0.05 <= aspect <= 18:
            text_area += int(area)
            text_components += 1
        if area >= max(1500, page_area * 0.0015):
            large_area += int(area)

    text_density = float(text_area / page_area)
    text_components_per_mp = float(text_components / max(mp, 0.001))

    edges = cv2.Canny(gray, 60, 180)
    min_line_length = max(35, int(min(height, width) * 0.035))
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80, minLineLength=min_line_length, maxLineGap=8)
    line_count = 0 if lines is None else len(lines)
    hv_count = 0
    if lines is not None:
        for line in lines[:, 0, :]:
            x1, y1, x2, y2 = line
            angle = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
            angle = min(angle, 180 - angle)
            if angle <= 10 or angle >= 80:
                hv_count += 1

    line_density = float(line_count / max(mp, 0.001))
    hv_line_density = float(hv_count / max(mp, 0.001))
    image_area_ratio = float(min(large_area / page_area, 1.0))

    return PageFeatures(
        foreground_density=foreground_density,
        text_density=text_density,
        text_components_per_mp=text_components_per_mp,
        line_density=line_density,
        hv_line_density=hv_line_density,
        image_area_ratio=image_area_ratio,
    )


def heuristic_classify(page_image) -> tuple[str, float]:
    features = extract_page_features(page_image)

    if features.hv_line_density >= 170 and features.text_components_per_mp >= 900:
        return "index_page", 0.58

    if features.line_density >= 120 and features.image_area_ratio >= 0.045:
        if features.text_density >= 0.018 or features.text_components_per_mp >= 550:
            return "drawing_material_list", 0.56
        return "drawing_only", 0.56

    if features.text_components_per_mp >= 950 and features.hv_line_density >= 55:
        return "spare_parts_table", 0.55

    if features.text_density >= 0.020 and features.line_density < 95:
        return "repair_kit", 0.52

    if features.foreground_density < 0.018 and features.line_density < 45:
        return "drawing_only", 0.50

    return "spare_parts_table", 0.48
