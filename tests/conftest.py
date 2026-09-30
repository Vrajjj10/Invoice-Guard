import os
import sys
import tempfile
from pathlib import Path

import pytest

# Isolated DB + upload dir for tests; must be set before app modules are imported.
_tmp = Path(tempfile.mkdtemp(prefix="invoiceguard-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(_tmp / "uploads")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


@pytest.fixture(scope="session")
def sample_files(tmp_path_factory) -> dict[str, Path]:
    import make_sample

    out = tmp_path_factory.mktemp("samples")
    digital = out / "digital.pdf"
    make_sample.make_invoice_pdf(digital)
    img = make_sample.scanned_image(digital)
    img.save(out / "scanned.png")
    img.convert("RGB").save(out / "scanned.pdf")
    return {
        "digital": digital,
        "scanned_png": out / "scanned.png",
        "scanned_pdf": out / "scanned.pdf",
    }


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:  # "with" runs startup (DB init, OCR engine load)
        yield c
