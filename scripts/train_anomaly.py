"""Train per-vendor IsolationForests on synthetic invoice history and save with joblib.

Usage: python scripts/train_anomaly.py [--out models/anomaly.joblib] [--seed 42]
"""

import argparse
import math
import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# (gstin, typical amount in INR, log-spread). First three match data/vendors.json.
VENDORS = [
    ("27AAPFU0939F1ZV", 15_000, 0.35), ("29AAGCB7383J1Z4", 60_000, 0.40),
    ("24AABCT1332L1ZK", 120_000, 0.30), ("07AABCA1234C1Z5", 8_000, 0.25),
    ("33AABCD5678E1Z2", 250_000, 0.45), ("06AABCE2345F1Z8", 35_000, 0.30),
    ("19AABCF3456G1Z1", 5_000, 0.30), ("09AABCG4567H1Z9", 90_000, 0.35),
    ("36AABCH5678J1Z3", 22_000, 0.40), ("03AABCI6789K1Z7", 450_000, 0.50),
]


def generate_history(seed: int = 42, per_vendor: int = 80) -> dict[str, np.ndarray]:
    """Log-normal amounts per vendor (seeded)."""
    rng = np.random.default_rng(seed)
    return {g: rng.lognormal(math.log(mu), sigma, per_vendor).round(2)
            for g, mu, sigma in VENDORS}


def train(history: dict[str, np.ndarray], seed: int = 42) -> dict:
    """One IsolationForest per vendor on log(amount), plus overall median for cold start."""
    models = {}
    for g, amounts in history.items():
        m = IsolationForest(n_estimators=100, contamination=0.02, random_state=seed)
        m.fit(np.log(amounts).reshape(-1, 1))
        models[g] = m
    allv = np.concatenate(list(history.values()))
    return {"models": models, "counts": {g: len(a) for g, a in history.items()},
            "median": float(np.median(allv))}


def main() -> None:
    from app.config import get_settings

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=get_settings().anomaly_model_path)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    bundle = train(generate_history(a.seed), a.seed)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, a.out)
    print(f"Saved {len(bundle['models'])} vendor models to {a.out} "
          f"(overall median {bundle['median']:,.0f})")


if __name__ == "__main__":
    main()
