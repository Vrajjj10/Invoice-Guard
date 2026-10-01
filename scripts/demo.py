"""Upload every demo invoice to a running InvoiceGuard and print each decision.

Usage:  python scripts/demo.py [--url URL] [--key KEY] [files...]
Defaults: URL from INVOICEGUARD_URL (else the live Render app), key from DEMO_API_KEY,
files = data/samples/[1-5]_*.pdf
"""

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

DEFAULT_URL = "https://invoice-guard-df71.onrender.com"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("INVOICEGUARD_URL", DEFAULT_URL))
    ap.add_argument("--key", default=os.environ.get("DEMO_API_KEY", ""))
    ap.add_argument("files", nargs="*", type=Path)
    a = ap.parse_args()
    files = a.files or sorted(Path("data/samples").glob("[1-5]_*.pdf"))
    headers = {"X-API-Key": a.key}
    base = a.url.rstrip("/")
    with httpx.Client(base_url=base, headers=headers, timeout=60) as c:
        for f in files:
            r = c.post("/invoices/upload", files={"file": (f.name, f.read_bytes())})
            if r.status_code != 202:
                print(f"{f.name}: upload failed {r.status_code} {r.text[:100]}")
                continue
            job = r.json()
            for _ in range(60):
                d = c.get(f"/jobs/{job['job_id']}").json()
                if d["status"] in ("done", "failed"):
                    break
                time.sleep(2)
            cached = " (cached)" if job["cached"] else ""
            print(f"{f.name}{cached}: {d['status']} -> {d.get('decision')}")
            for reason in d.get("decision_reasons") or []:
                print(f"    - {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
