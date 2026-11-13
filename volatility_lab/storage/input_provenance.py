"""Retain pricing inputs beside raw broker captures; never store credentials."""
from __future__ import annotations
from volatility_lab.runtime.settings import data_dir as runtime_data_dir
from volatility_lab import PACKAGE_ROOT

import hashlib
import json
import math
from pathlib import Path
from typing import Any

ROOT = runtime_data_dir()


def _sanitize_for_json(obj: Any) -> Any:
    """Recursively normalize NaN/Infinity floats to None so strict JSON serialization succeeds."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {str(k): _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    return obj


def record(kind: str, payload: Any) -> str:
    """Serialize sanitized pricing input payload and persist content-addressed provenance JSON."""
    clean_payload = _sanitize_for_json(payload)
    body = json.dumps(
        {"kind": kind, "input": clean_payload},
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    target_dir = ROOT / "input_history"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{kind}_{digest}.json"
    if not path.exists():
        path.write_bytes(body)
    return digest


def snapshot(rate: float, dividend: float, symbol: str) -> dict[str, Any]:
    """Capture rate and dividend provenance while surfacing file/mismatch warnings explicitly."""
    result: dict[str, Any] = {
        "symbol": symbol,
        "rate": float(rate),
        "dividend": float(dividend),
        "rate_digest": None,
        "dividend_digest": None,
        "warnings": [],
    }
    rate_path = ROOT / "risk_free_rate.json"
    try:
        raw_rate = json.loads(rate_path.read_text(encoding="utf-8"))
        expected_rate = float(raw_rate["model_rate"])
        if not math.isclose(float(rate), expected_rate, rel_tol=1e-9, abs_tol=1e-9):
            result["warnings"].append(
                f"Rate mismatch: supplied {rate} vs stored model_rate {expected_rate}"
            )
        result["rate_source"] = raw_rate
        result["rate_digest"] = record("risk_free_rate", raw_rate)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["warnings"].append(f"risk_free_rate provenance unavailable: {exc}")

    div_path = ROOT / "dividend_yields.json"
    try:
        raw_div = json.loads(div_path.read_text(encoding="utf-8"))
        entry = raw_div[symbol] if symbol in raw_div else raw_div.get("symbols", {}).get(symbol)
        if entry is None:
            raise KeyError(symbol)
        expected_div = float(entry["model_yield"] if isinstance(entry, dict) else entry)
        if not math.isclose(float(dividend), expected_div, rel_tol=1e-9, abs_tol=1e-9):
            result["warnings"].append(
                f"Dividend yield mismatch for {symbol}: supplied {dividend} vs stored {expected_div}"
            )
        result["dividend_source"] = entry
        result["dividend_digest"] = record("dividend_yield", {"symbol": symbol, "entry": entry})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["warnings"].append(f"dividend_yields provenance unavailable for {symbol}: {exc}")

    return result
