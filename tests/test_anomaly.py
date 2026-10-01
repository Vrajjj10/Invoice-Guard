import sys
from pathlib import Path

import numpy as np

from app.agent.tools import ToolContext, run_tool
from app.ml import anomaly
from tests.test_validators import _inv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import train_anomaly  # noqa: E402

BUNDLE = train_anomaly.train(train_anomaly.generate_history(seed=1, per_vendor=60), seed=1)
SGT = "27AAPFU0939F1ZV"  # typical amount ~15k


def test_history_is_seeded():
    a = train_anomaly.generate_history(seed=7)
    b = train_anomaly.generate_history(seed=7)
    assert all(np.array_equal(a[k], b[k]) for k in a) and len(a) == len(train_anomaly.VENDORS)


def test_normal_amount_ok():
    r = anomaly.flag_anomaly(SGT, None, 15_000, BUNDLE)
    assert r["method"] == "model" and r["anomaly"] is False and r["ok"] is True


def test_extreme_amount_flagged_with_higher_score():
    normal = anomaly.flag_anomaly(SGT, None, 15_000, BUNDLE)
    r = anomaly.flag_anomaly(SGT, None, 900_000, BUNDLE)
    assert r["anomaly"] is True and r["score"] > normal["score"] and r["reason"]


def test_new_vendor_cold_start_rule():
    ok = anomaly.flag_anomaly("99NEWVENDOR", None, 50_000, BUNDLE)
    bad = anomaly.flag_anomaly("99NEWVENDOR", None, 1_000_000, BUNDLE)
    assert ok["method"] == bad["method"] == "rule"
    assert ok["anomaly"] is False and bad["anomaly"] is True
    assert "median" in bad["reason"]


def test_low_history_vendor_uses_rule():
    thin = {**BUNDLE, "counts": {**BUNDLE["counts"], SGT: 3}}
    assert anomaly.flag_anomaly(SGT, None, 15_000, thin)["method"] == "rule"


def test_missing_amount_or_model():
    assert anomaly.flag_anomaly(SGT, None, None, BUNDLE)["ok"] is None
    assert anomaly.flag_anomaly(SGT, None, 100, {"models": {}, "counts": {}})["ok"] is None


def test_untrained_returns_not_checkable(monkeypatch):
    monkeypatch.setattr(anomaly, "load_bundle", lambda *a: None)
    assert anomaly.flag_anomaly(SGT, None, 100)["ok"] is None


def test_tool_dispatch_uses_invoice_total(monkeypatch):
    monkeypatch.setattr(anomaly, "load_bundle", lambda *a: BUNDLE)
    out = run_tool("flag_anomaly", {}, ToolContext(_inv(grand_total=15_399)))
    assert out["ok"] is True and out["method"] == "model"
    out = run_tool("flag_anomaly", {}, ToolContext(_inv(grand_total=5_000_000)))
    assert out["anomaly"] is True
