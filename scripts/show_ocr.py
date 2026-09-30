"""Print extraction output for one or more files.

Usage: python scripts/show_ocr.py data/samples/sample_scanned.pdf [more files...]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extraction.text import extract_text  # noqa: E402


def main() -> None:
    for path in sys.argv[1:]:
        r = extract_text(Path(path).read_bytes())
        print("=" * 70)
        print(f"{path}\nmethod={r.method} ocr_confidence={r.ocr_confidence} "
              f"pages={r.page_count} time={r.elapsed_ms}ms")
        print("-" * 70)
        print(r.text)


if __name__ == "__main__":
    main()
