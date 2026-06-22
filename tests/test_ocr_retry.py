import numpy as np

from core.ocr_retry import retry_cell_ocr


def test_retry_cell_ocr_uses_paddle_first_and_stops_when_confident():
    calls = []

    def paddle_runner(image, engine):
        calls.append(("paddle", image.shape))
        return "51.08308-0029", 0.91

    def easy_runner(image, engine):
        calls.append(("easy", image.shape))
        return "unused", 0.99

    result = retry_cell_ocr(
        np.zeros((12, 36), dtype=np.uint8),
        min_confidence=0.75,
        paddle_runner=paddle_runner,
        easyocr_runner=easy_runner,
    )

    assert result["engine"] == "paddleocr"
    assert result["text"] == "51.08308-0029"
    assert [call[0] for call in calls] == ["paddle"]


def test_retry_cell_ocr_does_not_use_easyocr_by_default():
    calls = []

    def paddle_runner(image, engine):
        calls.append("paddle")
        return "51.O8308-0029", 0.60

    def easy_runner(image, engine):
        calls.append("easy")
        return "51.08308-0029", 0.82

    result = retry_cell_ocr(
        np.zeros((12, 36), dtype=np.uint8),
        min_confidence=0.75,
        paddle_runner=paddle_runner,
        easyocr_runner=easy_runner,
    )

    assert result["engine"] == "paddleocr"
    assert result["confidence"] == 0.60
    assert calls == ["paddle"]


def test_retry_cell_ocr_can_use_easyocr_fallback_when_explicitly_enabled():
    calls = []

    def paddle_runner(image, engine):
        calls.append("paddle")
        return "51.O8308-0029", 0.60

    def easy_runner(image, engine):
        calls.append("easy")
        return "51.08308-0029", 0.82

    result = retry_cell_ocr(
        np.zeros((12, 36), dtype=np.uint8),
        min_confidence=0.75,
        use_easyocr_fallback=True,
        paddle_runner=paddle_runner,
        easyocr_runner=easy_runner,
    )

    assert result["engine"] == "easyocr"
    assert result["confidence"] == 0.82
    assert calls == ["paddle", "easy"]
