"""Tool-calling agent loop: the LLM picks tools, code produces the verdicts."""

import json
import logging
from dataclasses import dataclass, field

from app.agent.tools import TOOL_SPECS, ToolContext, run_tool
from app.config import get_settings
from app.llm.provider import GeminiProvider, ToolChatProvider, ToolResult

log = logging.getLogger(__name__)

MAX_ITERATIONS = 6

SYSTEM_PROMPT = (
    "You verify supplier invoices for accounts payable. Use the tools; never do the math or "
    "judge GSTINs yourself. Call check_duplicate, validate_totals, validate_gstin for vendor "
    "and buyer, lookup_vendor and flag_anomaly. When done, reply with 1-3 short sentences "
    "listing any problems found, or: No issues."
)

# (tool, args) the backstop runs if the model skipped them
REQUIRED_CALLS = [
    ("check_duplicate", {}),
    ("validate_totals", {}),
    ("validate_gstin", {"party": "vendor"}),
    ("validate_gstin", {"party": "buyer"}),
    ("lookup_vendor", {}),
    ("flag_anomaly", {}),
]


def _key(name: str, args: dict) -> str:
    return f"{name}:{args['party']}" if name == "validate_gstin" else name


@dataclass
class AgentResult:
    results: dict[str, dict] = field(default_factory=dict)  # tool key -> latest output
    trace: list[dict] = field(default_factory=list)
    notes: str = ""
    iterations: int = 0
    tokens: int = 0
    backfilled: list[str] = field(default_factory=list)
    error: str | None = None


def get_provider() -> ToolChatProvider:
    return GeminiProvider(get_settings().default_model)


def run_agent(ctx: ToolContext, provider: ToolChatProvider | None = None) -> AgentResult:
    """Loop function_call -> function_response until no calls or MAX_ITERATIONS."""
    res = AgentResult()
    try:
        session = (provider or get_provider()).start(SYSTEM_PROMPT, TOOL_SPECS)
        turn = session.send("Invoice:\n" + ctx.fields.model_dump_json(exclude={"reason"}))
        res.tokens += turn.tokens
        while turn.calls and res.iterations < MAX_ITERATIONS:
            res.iterations += 1
            outputs = []
            for call in turn.calls:
                args = dict(call.args)
                if call.name == "validate_gstin" and args.get("party") not in ("vendor", "buyer"):
                    args["party"] = "vendor"
                out = run_tool(call.name, args, ctx)
                res.results[_key(call.name, args)] = out
                res.trace.append({"tool": call.name, "args": args, "output": out})
                outputs.append(ToolResult(call, json.loads(json.dumps(out, default=str))))
            turn = session.send(outputs)
            res.tokens += turn.tokens
        res.notes = "iteration cap reached" if turn.calls else turn.text.strip()
    except Exception as exc:  # LLM failure must not skip verification
        log.exception("Agent LLM loop failed")
        res.error = f"{type(exc).__name__}: {exc}"[:300]

    for name, args in REQUIRED_CALLS:  # deterministic backstop
        key = _key(name, args)
        if key not in res.results:
            res.results[key] = run_tool(name, args, ctx)
            res.backfilled.append(key)
    return res
