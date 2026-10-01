"""Text extraction: PyMuPDF embedded text first, RapidOCR fallback for scanned pages/images."""

import re
import time
from dataclasses import dataclass, field

import fitz  # PyMuPDF
import numpy as np

from app.extraction.ocr import run_ocr

MIN_PAGE_CHARS = 30  # below this, a PDF page is treated as scanned and OCR'd
RENDER_DPI = 150
MAX_TEXT_CHARS = 12_000  # keep LLM input small
MAX_PAGES = 10


class UnsupportedFileError(ValueError):
    pass


@dataclass
class PageResult:
    page: int
    method: str  # "text" | "ocr"
    chars: int
    ocr_confidence: float | None = None


@dataclass
class ExtractionResult:
    text: str
    method: str  # "text" | "ocr" | "mixed"
    ocr_confidence: float  # mean over OCR'd lines; 1.0 when all text was embedded
    page_count: int
    elapsed_ms: int
    pages: list[PageResult] = field(default_factory=list)


def detect_file_type(data: bytes) -> str:
    """Identify file type from magic bytes (don't trust the filename)."""
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    raise UnsupportedFileError("Unsupported file type (expected PDF, PNG or JPEG)")


def clean_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Normalize whitespace, drop empty lines, trim length."""
    text = text.replace("\x00", "").replace(" ", " ")
    # keep double spaces (column separators), collapse anything longer
    lines = [re.sub(r" {3,}", "  ", line.replace("\t", "  ")).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[...truncated]"
    return text


def _pixmap_to_array(pix: fitz.Pixmap) -> np.ndarray:
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = arr[:, :, :3]
    return np.ascontiguousarray(arr[:, :, ::-1])  # RGB -> BGR for OpenCV-based OCR


def _page_text_by_rows(page: fitz.Page) -> str:
    """Embedded text with words regrouped into visual rows, so table rows stay on one line."""
    words = page.get_text("words")  # (x0, y0, x1, y1, word, block, line, word_no)
    if not words:
        return ""
    words.sort(key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    rows: list[list[tuple]] = []
    for w in words:
        yc, h = (w[1] + w[3]) / 2, w[3] - w[1]
        if rows:
            ref = rows[-1][0]
            if abs(yc - (ref[1] + ref[3]) / 2) < 0.5 * max(h, ref[3] - ref[1]):
                rows[-1].append(w)
                continue
        rows.append([w])

    lines = []
    for row in rows:
        row.sort(key=lambda w: w[0])
        parts = [row[0][4]]
        for prev, cur in zip(row, row[1:], strict=False):
            gap = cur[0] - prev[2]
            parts.append(("  " if gap > 15 else " ") + cur[4])  # wide gap = column break
        lines.append("".join(parts))
    return "\n".join(lines)


def _extract_pdf(data: bytes) -> tuple[list[str], list[PageResult], list[float]]:
    texts, pages, ocr_scores = [], [], []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for i, page in enumerate(doc):
            if i >= MAX_PAGES:
                break
            embedded = _page_text_by_rows(page).strip()
            if len(embedded) >= MIN_PAGE_CHARS:
                texts.append(embedded)
                pages.append(PageResult(page=i + 1, method="text", chars=len(embedded)))
                continue
            pix = page.get_pixmap(dpi=RENDER_DPI)
            ocr_text, conf, n_lines = run_ocr(_pixmap_to_array(pix))
            texts.append(ocr_text)
            pages.append(
                PageResult(page=i + 1, method="ocr", chars=len(ocr_text), ocr_confidence=conf)
            )
            if n_lines:
                ocr_scores.extend([conf] * n_lines)
    return texts, pages, ocr_scores


def extract_text(data: bytes) -> ExtractionResult:
    start = time.perf_counter()
    file_type = detect_file_type(data)

    if file_type == "pdf":
        texts, pages, ocr_scores = _extract_pdf(data)
    else:
        ocr_text, conf, n_lines = run_ocr(data)
        texts = [ocr_text]
        pages = [PageResult(page=1, method="ocr", chars=len(ocr_text), ocr_confidence=conf)]
        ocr_scores = [conf] * n_lines

    methods = {p.method for p in pages}
    method = methods.pop() if len(methods) == 1 else "mixed"
    if ocr_scores:
        confidence = sum(ocr_scores) / len(ocr_scores)
    else:
        confidence = 0.0 if "ocr" in {p.method for p in pages} else 1.0

    return ExtractionResult(
        text=clean_text("\n\n".join(texts)),
        method=method,
        ocr_confidence=round(confidence, 4),
        page_count=len(pages),
        elapsed_ms=int((time.perf_counter() - start) * 1000),
        pages=pages,
    )
