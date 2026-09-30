"""GSTIN format + checksum validation."""

import re

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_VALID_STATES = {f"{n:02d}" for n in range(1, 39)} | {"97", "99"}


def normalize_gstin(gstin: str | None) -> str:
    """Uppercase and strip spaces/hyphens."""
    return re.sub(r"[\s-]", "", gstin or "").upper()


def gstin_check_char(first14: str) -> str:
    """Mod-36 check character for the first 14 GSTIN characters."""
    total = 0
    for i, ch in enumerate(first14):
        product = _CHARS.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _CHARS[(36 - total % 36) % 36]


def validate_gstin(gstin: str | None) -> dict:
    """Return {ok, gstin, state_code, detail}."""
    g = normalize_gstin(gstin)
    if not g:
        return {"ok": None, "gstin": None, "state_code": None, "detail": "missing"}
    if not GSTIN_RE.match(g):
        return {"ok": False, "gstin": g, "state_code": None, "detail": "bad format"}
    if g[:2] not in _VALID_STATES:
        return {"ok": False, "gstin": g, "state_code": g[:2], "detail": "unknown state code"}
    expected = gstin_check_char(g[:14])
    if g[14] != expected:
        return {"ok": False, "gstin": g, "state_code": g[:2],
                "detail": f"checksum mismatch (expected {expected})"}
    return {"ok": True, "gstin": g, "state_code": g[:2], "detail": "valid"}
