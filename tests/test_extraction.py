import pytest

from app.extraction.text import UnsupportedFileError, clean_text, detect_file_type, extract_text


def test_detect_file_type():
    assert detect_file_type(b"%PDF-1.7 ...") == "pdf"
    assert detect_file_type(b"\x89PNG\r\n\x1a\n....") == "png"
    assert detect_file_type(b"\xff\xd8\xff\xe0....") == "jpg"
    with pytest.raises(UnsupportedFileError):
        detect_file_type(b"hello world")


def test_clean_text_trims_and_keeps_columns():
    assert clean_text("  a     b  \n\n\n c\x00 ") == "a  b\nc"
    assert clean_text("x" * 50, max_chars=10).endswith("[...truncated]")


def test_digital_pdf_uses_embedded_text(sample_files):
    r = extract_text(sample_files["digital"].read_bytes())
    assert r.method == "text"
    assert r.ocr_confidence == 1.0
    assert "27AAPFU0939F1ZV" in r.text
    assert "Steel Bolts M8  7318  500  4.50  2,250.00" in r.text  # row kept together


@pytest.mark.parametrize("key", ["scanned_pdf", "scanned_png"])
def test_scanned_files_use_ocr(sample_files, key):
    r = extract_text(sample_files[key].read_bytes())
    assert r.method == "ocr"
    assert r.ocr_confidence > 0.8
    for token in ["27AAPFU0939F1ZV", "SGT/2026/0457", "13,050.00", "15,399.00"]:
        assert token in r.text
