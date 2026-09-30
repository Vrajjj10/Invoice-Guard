"""IsolationForest amount anomaly detector with a cold-start rule for new vendors."""

import logging
import math
import re
from functools import lru_cache
from pathlib import Path

import joblib

from app.config import get_settings

log = logging.getLogger(__name__)


def vendor_key(gstin: str | None, name: str | None) -> str | None:
    """GSTIN if present, else normalised name."""
    g = re.sub(r"[^A-Z0-9]", "", (gstin or "").upper())
    return g or re.sub(r"[^A-Z0-9]", "", (name or "").upper()) or None


@lru_cache
def load_bundle(path: str | None = None) -> dict | None:
    """Trained bundle {models, counts, median}; None if not trained yet."""
    p = Path(path or get_settings().anomaly_model_path)
    return joblib.load(p) if p.exists() else None


def _none(reason: str) -> dict:
    return {"ok": None, "anomaly": None, "score": None, "method": "none", "reason": reason}


def _cold_start(amount: float, median: float | None, why: str) -> dict:
    if not median:
        return _none(f"{why}; no overall history to compare")
    limit = get_settings().anomaly_cold_start_multiple
    ratio = amount / median
    flagged = ratio > limit
    reason = f"{why}; amount is {ratio:.1f}x overall median"
    if flagged:
        reason += f" (limit {limit:g}x)"
    return {"ok": not flagged, "anomaly": flagged, "score": round(min(ratio / 10, 1.0), 3),
            "method": "rule", "reason": reason}


def flag_anomaly(gstin: str | None, name: str | None, amount: float | None,
                 bundle: dict | None = None) -> dict:
    """Score `amount` against the vendor history. score is higher for more unusual amounts."""
    if amount is None or amount <= 0:
        return _none("amount missing")
    bundle = bundle if bundle is not None else load_bundle()
    if bundle is None:
        return _none("model not trained (run scripts/train_anomaly.py)")
    key = vendor_key(gstin, name)
    model = bundle["models"].get(key)
    n = bundle["counts"].get(key, 0)
    if model is None or n < get_settings().anomaly_min_history:
        return _cold_start(amount, bundle.get("median"), f"new vendor ({n} past invoices)")
    x = [[math.log(amount)]]
    score = float(-model.score_samples(x)[0])
    flagged = int(model.predict(x)[0]) == -1
    return {"ok": not flagged, "anomaly": flagged, "score": round(score, 3), "method": "model",
            "reason": "amount unusual for this vendor" if flagged
                      else "amount within the vendor's usual range"}
