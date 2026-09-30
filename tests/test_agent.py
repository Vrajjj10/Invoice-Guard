import uuid
from types import SimpleNamespace

from google.genai import types

from app.agent import loop
from app.agent.tools import ToolContext, find_vendor, load_vendors, run_tool
from app.llm import provider as prov
from app.llm.provider import ChatTurn, ToolCall
from tests.test_validators import _inv


class FakeSession:
    def __init__(self, turns):
        self.turns, self.sent = list(turns), []

    def send(self, message):
        self.sent.append(message)
        t = self.turns.pop(0)
        if isinstance(t, Exception):
            raise t
        return t


class FakeProvider:
    def __init__(self, turns):
        self.session = FakeSession(turns)

    def start(self, system, tools):
        self.tools = tools
        return self.session


def _all_calls():
    return ChatTurn("", [
        ToolCall("check_duplicate", {}, "1"),
        ToolCall("validate_totals", {}, "2"),
        ToolCall("validate_gstin", {"party": "vendor"}, "3"),
        ToolCall("validate_gstin", {"party": "buyer"}, "4"),
        ToolCall("lookup_vendor", {}, "5"),
        ToolCall("flag_anomaly", {}, "6"),
    ], tokens=10)


def test_loop_runs_tools_and_returns_notes():
    p = FakeProvider([_all_calls(), ChatTurn("No issues.", tokens=5)])
    r = loop.run_agent(ToolContext(_inv()), p)
    assert r.notes == "No issues." and r.iterations == 1 and r.tokens == 15
    assert not r.backfilled and r.error is None
    assert r.results["validate_totals"]["ok"] is True
    assert r.results["validate_gstin:buyer"]["ok"] is True
    assert r.results["lookup_vendor"]["found"] is True
    assert r.results["flag_anomaly"]["ok"] is None
    results = p.session.sent[1]
    assert [x.call.id for x in results] == ["1", "2", "3", "4", "5", "6"]
    assert {t.name for t in p.tools} == {
        "check_duplicate", "validate_totals", "validate_gstin", "lookup_vendor", "flag_anomaly"
    }


def test_multi_turn_and_backfill_skipped_tools():
    p = FakeProvider([
        ChatTurn("", [ToolCall("validate_totals", {})]),
        ChatTurn("", [ToolCall("validate_gstin", {"party": "vendor"})]),
        ChatTurn("Totals fine."),
    ])
    r = loop.run_agent(ToolContext(_inv()), p)
    assert r.iterations == 2 and r.notes == "Totals fine."
    assert set(r.backfilled) == {
        "check_duplicate", "validate_gstin:buyer", "lookup_vendor", "flag_anomaly"
    }
    assert len(r.results) == 6


def test_iteration_cap():
    p = FakeProvider([ChatTurn("", [ToolCall("validate_totals", {})])] * 20)
    r = loop.run_agent(ToolContext(_inv()), p)
    assert r.iterations == loop.MAX_ITERATIONS and r.notes == "iteration cap reached"


def test_llm_error_still_runs_all_checks():
    r = loop.run_agent(ToolContext(_inv(grand_total=1)), FakeProvider([RuntimeError("boom")]))
    assert "boom" in r.error and len(r.backfilled) == 6
    assert r.results["validate_totals"]["ok"] is False


def test_unknown_tool_and_bad_party():
    p = FakeProvider([
        ChatTurn("", [ToolCall("delete_db", {}), ToolCall("validate_gstin", {"party": "x"})]),
        ChatTurn("done"),
    ])
    r = loop.run_agent(ToolContext(_inv()), p)
    assert "unknown tool" in r.trace[0]["output"]["error"]
    assert r.trace[1]["args"]["party"] == "vendor"


def test_model_cannot_override_verdict():
    """Tool outputs come from code even if the model passes misleading args."""
    ctx = ToolContext(_inv(grand_total=99999))
    assert run_tool("validate_totals", {"grand_total": 15399}, ctx)["ok"] is False


def test_find_vendor():
    vendors = load_vendors()
    assert find_vendor("27AAPFU0939F1ZV", "ShreeGaneshTraders", vendors)["name_matches"]
    by_name = find_vendor(None, "Shree Ganesh Traders Private Limited", vendors)
    assert by_name["match"] == "name"
    assert not find_vendor("07AAAAA0000A1Z5", "Nobody Corp", vendors)["found"]


def test_check_duplicate_without_db():
    r = run_tool("check_duplicate", {}, ToolContext(_inv()))
    assert r["ok"] is True and r["duplicate"] is False
    assert run_tool("check_duplicate", {}, ToolContext(_inv(invoice_number=None)))["ok"] is None


def test_gemini_session_maps_calls_and_responses(monkeypatch):
    sent = []
    fc = types.FunctionCall(id="c1", name="validate_totals", args={})
    responses = [
        SimpleNamespace(
            candidates=[SimpleNamespace(content=types.Content(
                role="model", parts=[types.Part(function_call=fc)]))],
            function_calls=[fc], text=None,
            usage_metadata=SimpleNamespace(total_token_count=7),
        ),
        SimpleNamespace(candidates=[], function_calls=None, text="ok", usage_metadata=None),
    ]

    def gen(model, contents, config):
        sent.append(list(contents))
        return responses.pop(0)

    fake = SimpleNamespace(models=SimpleNamespace(generate_content=gen))
    monkeypatch.setattr(prov, "_get_client", lambda: fake)
    s = prov.GeminiProvider("m").start("sys", [])
    t1 = s.send("hi")
    assert t1.calls[0].name == "validate_totals" and t1.tokens == 7
    t2 = s.send([prov.ToolResult(t1.calls[0], {"ok": True})])
    assert t2.text == "ok" and not t2.calls
    fr = sent[1][-1].parts[0].function_response
    assert fr.id == "c1" and fr.response == {"ok": True}
    assert sent[1][1].role == "model"  # model turn kept in history


def test_pipeline_stores_checks_and_detects_duplicate(monkeypatch, client, sample_files):
    from app import pipeline
    from app.llm import client as llm_client

    monkeypatch.setattr(
        pipeline, "extract_invoice",
        lambda text, conf: llm_client.LLMResult(_inv(), "m", False, 50, 5),
    )
    monkeypatch.setattr(loop, "get_provider", lambda: FakeProvider([_all_calls(), ChatTurn("ok")]))
    jobs = []
    base = sample_files["digital"].read_bytes()
    for _ in range(2):  # different file bytes (defeats file cache), same invoice fields
        data = base + f"\n% dup-test {uuid.uuid4()}\n".encode()
        r = client.post("/invoices/upload", files={"file": ("d.pdf", data, "application/pdf")})
        jobs.append(client.get(f"/jobs/{r.json()['job_id']}").json())
    first, second = jobs
    assert first["status"] == "done" and first["agent_notes"] == "ok"
    assert first["checks"]["results"]["validate_totals"]["ok"] is True
    assert second["checks"]["results"]["check_duplicate"]["duplicate"] is True
    assert first["job_id"] in second["checks"]["results"]["check_duplicate"]["matching_jobs"]
