"""Research-v1 paper entry policy, also embedded verbatim in the collector and browser."""
import json
import math
from pathlib import Path

RULES = json.loads((Path(__file__).parents[1] / "data" / "freeze_policy.json").read_text(encoding="utf-8"))


def entry_decision(strategy, *, minute, dte, vrp, hv30, rr25):
    """Return the frozen entry-rule decision and the first applicable reason."""
    rule = RULES.get(strategy, {})
    if rule.get("minute") != minute or rule.get("holding") not in ("intraday", "positional"):
        return "not_scheduled", "outside_entry_schedule"
    if not isinstance(dte, (int, float)) or not math.isfinite(dte):
        return "inputs_unavailable", "dte_unavailable"
    if not rule["dte"][0] <= dte <= rule["dte"][1]:
        return "expiry_ineligible", "dte_outside_range"
    metrics = {"vrp": vrp, "hv30": hv30, "rr25": rr25}
    for key in rule["filters"]:
        if not isinstance(metrics[key], (int, float)) or not math.isfinite(metrics[key]):
            return "inputs_unavailable", key + "_unavailable"
    for key, (low, high) in rule["filters"].items():
        if not low <= metrics[key] <= high:
            return "rule_rejected", key + "_outside_range"
    return "qualified", "entry_rule_passed"


def entry_allowed(strategy, *, minute, dte, vrp, hv30, rr25):
    return entry_decision(strategy, minute=minute, dte=dte, vrp=vrp, hv30=hv30, rr25=rr25)[0] == "qualified"
