"""RapidOCR engine singleton. Loaded once at startup (see app.main lifespan)."""

import logging
import threading

import numpy as np

from app.config import get_settings

log = logging.getLogger(__name__)

_engine = None
_lock = threading.Lock()
_ocr_lock = threading.Lock()  # one OCR at a time: concurrent runs would stack their memory


def load_engine():
    """Create the RapidOCR engine once; later calls return the same instance."""
    global _engine
    with _lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR

            log.info("Loading RapidOCR engine...")
            _engine = RapidOCR()
            # Cap the longest image side: ONNX memory scales with it (2000px -> ~700 MB peak,
            # 1280px -> ~320 MB), and Render's free tier has 512 MB.
            _engine.max_side_len = get_settings().ocr_max_side_len
    return _engine


def run_ocr(image: np.ndarray | bytes) -> tuple[str, float, int]:
    """OCR an image. Returns (text, mean line confidence, line count).

    Lines are regrouped into visual rows (by vertical position) so table rows like
    "Widget  2  500.00  1000.00" stay on one line instead of being split up.
    """
    engine = load_engine()
    with _ocr_lock:
        result, _ = engine(image)
    if not result:
        return "", 0.0, 0

    boxes = []
    for box, text, score in result:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        boxes.append(
            {
                "text": text,
                "score": float(score),
                "x": min(xs),
                "yc": (min(ys) + max(ys)) / 2,
                "h": max(ys) - min(ys),
            }
        )

    boxes.sort(key=lambda b: b["yc"])
    rows: list[list[dict]] = []
    for b in boxes:
        if rows and abs(b["yc"] - rows[-1][0]["yc"]) < 0.5 * max(b["h"], rows[-1][0]["h"]):
            rows[-1].append(b)
        else:
            rows.append([b])

    lines = ["  ".join(b["text"] for b in sorted(row, key=lambda b: b["x"])) for row in rows]
    confidence = sum(b["score"] for b in boxes) / len(boxes)
    return "\n".join(lines), confidence, len(boxes)
