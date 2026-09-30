
from app.llm import client as llm_client
from app.llm.schema import InvoiceFields


def _fields(conf: float, is_invoice: bool = True) -> InvoiceFields:
    return InvoiceFields(is_invoice=is_invoice, confidence=conf, invoice_number="INV-1")


def _patch(monkeypatch, outcomes):
    calls = []

    def fake(model, text):
        calls.append(model)
        out = outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out, 100

    monkeypatch.setattr(llm_client, "_call", fake)
    return calls


def test_confident_result_uses_default_model_only(monkeypatch):
    calls = _patch(monkeypatch, [_fields(0.95)])
    r = llm_client.extract_invoice("text", 0.99)
    assert not r.escalated and len(calls) == 1 and r.tokens == 100


def test_low_confidence_escalates(monkeypatch):
    calls = _patch(monkeypatch, [_fields(0.4), _fields(0.9)])
    r = llm_client.extract_invoice("text", 0.99)
    assert r.escalated and len(calls) == 2 and calls[0] != calls[1]
    assert r.fields.confidence == 0.9 and r.tokens == 200


def test_low_ocr_confidence_escalates(monkeypatch):
    calls = _patch(monkeypatch, [_fields(0.95), _fields(0.95)])
    assert llm_client.extract_invoice("text", 0.3).escalated and len(calls) == 2


def test_non_invoice_does_not_escalate(monkeypatch):
    calls = _patch(monkeypatch, [_fields(0.2, is_invoice=False)])
    assert not llm_client.extract_invoice("hello", 0.99).escalated and len(calls) == 1


def test_bad_json_retries_with_escalation_model(monkeypatch):
    calls = _patch(monkeypatch, [ValueError("bad json"), _fields(0.9)])
    r = llm_client.extract_invoice("text", None)
    assert r.escalated and len(calls) == 2


def test_upload_pipeline_stores_fields(monkeypatch, client, sample_files):
    from app import pipeline

    monkeypatch.setattr(
        pipeline,
        "extract_invoice",
        lambda text, conf: llm_client.LLMResult(_fields(0.9), "m", False, 50, 5),
    )
    data = sample_files["scanned_png"].read_bytes()
    r = client.post("/invoices/upload", files={"file": ("a.png", data, "image/png")})
    job = client.get(f"/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "done" and job["fields"]["invoice_number"] == "INV-1"
    assert job["llm_tokens"] == 50


