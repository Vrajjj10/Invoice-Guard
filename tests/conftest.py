import os
import sys
import tempfile
from pathlib import Path

import pytest

# Isolated DB + upload dir for tests; must be set before app modules are imported.
_tmp = Path(tempfile.mkdtemp(prefix="invoiceguard-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(_tmp / "uploads")
os.environ["GEMINI_API_KEY"] = ""  # tests must never need (or use) a real key
os.environ["ANOMALY_MODEL_PATH"] = str(_tmp / "none.joblib")  # untrained by default

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


@pytest.fixture(autouse=True)
def _no_real_agent_llm(monkeypatch):
    """Agent loop never reaches Gemini in tests; tests may override get_provider."""
    from app.agent import loop
    from app.llm.provider import ChatTurn

    class _Silent:
        def start(self, system, tools):
            return self

        def send(self, message):
            return ChatTurn("No issues.")

    monkeypatch.setattr(loop, "get_provider", lambda: _Silent())


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch):
    """Mock LLM provider: the pipeline gets a fixed extraction, never a network call."""
    from app import pipeline
    from app.llm import client as llm_client
    from tests.test_validators import _inv

    monkeypatch.setattr(
        pipeline, "extract_invoice",
        lambda text, conf=None: llm_client.LLMResult(_inv(), "mock-model", False, 10, 1),
    )

    def _no_network():
        raise AssertionError("tests must not create a real Gemini client")

    monkeypatch.setattr(llm_client, "_get_client", _no_network)
