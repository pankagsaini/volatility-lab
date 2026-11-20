"""Research-v1 paper entry policy, also embedded verbatim in the collector and browser."""
import json
import math
from pathlib import Path

RULES = json.loads((Path(__file__).parents[1] / "data" / "freeze_policy.json").read_text(encoding="utf-8"))


def entry_allowed(strategy, *, minute, dte, vrp, hv30, rr25):
    rule = RULES.get(strategy, {})
    if rule.get("minute") != minute or rule.get("holding") not in ("intraday", "positional"):
        return False
    metrics = {"dte": dte, "vrp": vrp, "hv30": hv30, "rr25": rr25}
    return all(
        isinstance(metrics[key], (int, float)) and math.isfinite(metrics[key]) and low <= metrics[key] <= high
        for key, (low, high) in {"dte": rule["dte"], **rule["filters"]}.items()
    )
