"""After-cost paper outcomes on the frozen prospective book, grouped by exit session."""

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from math import isfinite, sqrt
from statistics import stdev

from volatility_lab.backtest.forward_test import _parse_aware_timestamp, _quote_timestamp_is_fresh


def _finite(value):
    return type(value) in (int, float) and isfinite(value)


def _close_status(trade, start, ids):
    trade_id = trade.get("id")
    if not trade_id or ids[trade_id] != 1:
        return "missing_or_duplicate_id"
    entry = _parse_aware_timestamp(trade.get("triggerTime"))
    exit_at = _parse_aware_timestamp(trade.get("exit_at"))
    if (trade.get("policy_version") != "research-v1" or entry is None or exit_at is None
            or entry.date() < start or exit_at < entry or trade.get("session_date") != entry.date().isoformat()
            or trade.get("exit_session_date") != exit_at.date().isoformat()):
        return "outside_cohort_or_undated"
    if (trade.get("integrity_status") != "VALIDATED_FRESH_EXECUTABLE_MARK"
            or trade.get("provenance_status") not in {"VALIDATED_EVENT_QUOTES", "GAP_AFFECTED"}
            or trade.get("manual_reconciliation_required") or not trade.get("legs")):
        return "unverified_execution"
    for leg in trade["legs"]:
        if not isinstance(leg, dict):
            return "unverified_execution"
        for px, stamp, observed in (("entryPx", "entryQuoteAt", entry), ("exitPx", "exitQuoteAt", exit_at)):
            if (not _finite(leg.get(px)) or leg[px] <= 0
                    or not _quote_timestamp_is_fresh(leg.get(stamp), observed)):
                return "unverified_execution"
    if not all(_finite(trade.get(key)) for key in ("gross", "charges", "net")):
        return "unreconciled_amounts"
    sums = [Decimal(str(trade[key])) * 100 for key in ("gross", "charges", "net")]
    gross, fees, net = sums
    if any(value != value.to_integral_value() for value in sums) or fees < 0 or gross - fees != net:
        return "unreconciled_amounts"
    if trade.get("path_status") == "INCOMPLETE_QUOTE_PATH":
        return "quote_gap"
    if trade.get("path_status") == "INCOMPLETE_CAPTURE_PATH" or trade["provenance_status"] == "GAP_AFFECTED":
        return "capture_gap"
    return "eligible"


def _stats(rows):
    # Aggregate actual rounded ledger values in paise. Same-day closes share one observation.
    days = defaultdict(int)
    gross = fees = 0
    for row in rows:
        gross += round(row["gross"] * 100)
        fees += round(row["charges"] * 100)
        days[row["exit_session_date"]] += round(row["net"] * 100)
    net = sum(days.values())
    count = len(days)
    return {
        "closes": len(rows), "exit_sessions": count,
        "gross_inr": gross / 100 if rows else None,
        "fees_inr": fees / 100 if rows else None,
        "net_inr": net / 100 if rows else None,
        "mean_net_per_close_inr": round(net / len(rows) / 100, 2) if rows else None,
        "mean_net_per_exit_session_inr": round(net / count / 100, 2) if count else None,
        "exit_session_mean_se_inr": round(stdev(days.values()) / sqrt(count) / 100, 2) if count > 1 else None,
    }


def _entry_vrp(row):
    value = row.get("entry_vrp_pp")
    if not _finite(value):
        return "unavailable"
    if value > 0:
        return "IV above HV30"
    if value < 0:
        return "IV below HV30"
    return "IV equals HV30"


def prospective_outcomes(book):
    """Summarize closed paper P&L conditional on recorded endpoints and no known capture gap."""
    if (book.get("source_authority") != "python_paper_worker" or book.get("cohort_kind") != "prospective"
            or book.get("policy_version") != "research-v1"):
        raise ValueError("Prospective research-v1 ledger required")
    start = date.fromisoformat(book["prospective_start"])
    rows = book["closed_trades"]
    ids = Counter(row.get("id") for row in rows)
    eligible, gaps = [], []
    exclusions = Counter()
    for row in rows:
        status = _close_status(row, start, ids)
        if status == "eligible":
            eligible.append(row)
        else:
            exclusions[status] += 1
            if status in {"capture_gap", "quote_gap"}:
                gaps.append(row)
    result = {
        "totals": _stats(eligible), "gap_affected": _stats(gaps), "exclusions": dict(sorted(exclusions.items())),
        "closed_records": len(rows), "open_positions": len(book["open_positions"]),
    }
    for group, key in (("by_underlying", "sym"), ("by_holding", "holding"),
                       ("by_entry_vrp", "entry_vrp_pp"), ("by_exit_session", "exit_session_date")):
        grouped = defaultdict(list)
        for row in eligible:
            label = _entry_vrp(row) if key == "entry_vrp_pp" else row.get(key) or "unavailable"
            grouped[label].append(row)
        result[group] = {label: _stats(items) for label, items in sorted(grouped.items())}
    return result


def format_outcomes(report):
    """Render the same cohort statistics in a hosted Actions summary."""
    totals = report["totals"]
    lines = [
        "### Prospective paper outcomes", "",
        f'{totals["closes"]} eligible closes across {totals["exit_sessions"]} exit sessions. '
        f'{report["closed_records"]} closed records reviewed. {report["open_positions"]} positions remain open.',
    ]
    if report["exclusions"]:
        reasons = ", ".join(f"{key.replace('_', ' ')} ({count})" for key, count in report["exclusions"].items())
        lines.append(f"Excluded closes by reason — {reasons}.")
    if totals["closes"] or report["gap_affected"]["closes"]:
        lines.extend(["", "| Group | Closes | Exit sessions | Gross ₹ | Fees ₹ | Net ₹ | Mean net / exit session ₹ | SE ₹ |",
                      "|---|---:|---:|---:|---:|---:|---:|---:|"])

        def add(label, item):
            fields = ("gross_inr", "fees_inr", "net_inr", "mean_net_per_exit_session_inr", "exit_session_mean_se_inr")
            money = " | ".join(f"{item[field]:,.2f}" if item[field] is not None else "—" for field in fields)
            lines.append(f'| {label} | {item["closes"]} | {item["exit_sessions"]} | {money} |')

        add("Eligible closes", totals)
        if report["gap_affected"]["closes"]:
            add("Excluded · incomplete observation path", report["gap_affected"])
        for group in ("by_underlying", "by_holding", "by_entry_vrp"):
            for label, item in report[group].items():
                add(label.replace("|", " "), item)
        lines.append("")
        lines.append("Each exit session contributes its combined net P&L. Sessions without an eligible close "
                     "are outside the eligible mean. SE measures uncertainty in each group's mean across active "
                     "exit sessions, assuming independence between days. It requires at least two days.")
    else:
        lines.append("Realized paper P&L and sampling uncertainty are unavailable.")
    return "\n".join(lines)
