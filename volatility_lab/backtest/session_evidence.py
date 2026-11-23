"""Session evidence for the hosted paper worker, based on one archive listing and its checkpoint."""

from collections import Counter
from datetime import date, timedelta
import re

from volatility_lab.backtest import forward_test as engine
from volatility_lab.core.freeze_policy import RULES
from volatility_lab.core.market_calendar import IST, session_window


SNAPSHOT_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})_snapshot_([01]\d|2[0-3])([0-5]\d)[0-5]\d_(complete|partial)\.json\.gz")


def record_applied(state, previous_time):
    """Record distinct availability minutes only after the engine accepts a report."""
    stamp = engine._parse_aware_timestamp(state["last_capture_at"])
    receipts = state["worker_checkpoint"].setdefault("session_receipts", {"unknown_through": previous_time, "days": {}})
    ranges = receipts["days"].setdefault(stamp.date().isoformat(), [])
    minute = stamp.hour * 60 + stamp.minute
    if ranges and minute <= ranges[-1][1] + 1:
        ranges[-1][1] = max(ranges[-1][1], minute)
    else:
        ranges.append([minute, minute])


def _paper_events(state):
    # Count an entry or close only if its archived execution recorded a quote and price for each leg.
    counts = Counter()
    for trade in state["open_positions"] + state["closed_trades"]:
        for kind, time_field, leg_field, price_field in (
            ("entry", "triggerTime", "entryQuoteAt", "entryPx"), ("exit", "exit_at", "exitQuoteAt", "exitPx")
        ):
            stamp = engine._parse_aware_timestamp(trade.get(time_field))
            legs = trade.get("legs")
            if (stamp and trade.get(kind + "_capture_sha256") and legs
                    and all(leg.get(leg_field) and leg.get(price_field) is not None for leg in legs)):
                counts[stamp.date().isoformat(), stamp.hour * 60 + stamp.minute, kind] += 1
    return counts


def _positions_at_session(state, opening, observed_until):
    carried = marked = overdue = 0
    for trade in state["open_positions"] + state["closed_trades"]:
        entry = engine._parse_aware_timestamp(trade.get("triggerTime"))
        exit_at = engine._parse_aware_timestamp(trade.get("exit_at"))
        if observed_until < opening or not entry or entry > observed_until or (exit_at and exit_at < opening):
            continue
        deadline = engine._parse_aware_timestamp(trade.get("scheduled_exit_at"))
        if deadline and deadline <= observed_until and (not exit_at or exit_at > observed_until):
            overdue += 1
        if entry >= opening:
            continue
        carried += 1
        legs = len(trade.get("legs", []))
        for mark in trade.get("mtmPath") or []:
            at = engine._parse_aware_timestamp(mark.get("at"))
            quotes = mark.get("quote_times") or []
            prices = mark.get("exit_prices") or []
            if (at and opening <= at <= observed_until and mark.get("fresh") is True and legs
                    and len(quotes) == len(prices) == legs and all(quotes)):
                marked += 1
                break
    return {"carried_in": carried, "carry_with_fresh_mark": marked, "overdue_open": overdue}


def session_evidence(inventory, state, as_of, first_queued=None):
    """Report the last five scheduled sessions. The active clock minute is still collecting."""
    as_of = as_of.astimezone(IST)
    first_day = date.fromisoformat(state["prospective_start"])
    day = as_of.date()
    cp = state["worker_checkpoint"]
    receipts = cp.get("session_receipts", {})
    # A pre-upgrade cursor cannot prove which minutes were accepted.
    unknown = engine._parse_aware_timestamp(receipts.get("unknown_through", state["last_capture_at"]))
    queued = SNAPSHOT_NAME.fullmatch(first_queued) if first_queued else None
    pending_from = (queued[1], int(queued[2]) * 60 + int(queued[3])) if queued else None
    events = _paper_events(state)
    decision_minutes = sorted({rule["minute"] for rule in RULES.values() if "minute" in rule})
    sessions = []
    while day >= first_day and len(sessions) < 5:
        window = session_window(day)
        iso_day = day.isoformat()
        day -= timedelta(days=1)
        if window is None:
            continue
        opening, closing = window
        start, end = opening.hour * 60 + opening.minute, closing.hour * 60 + closing.minute
        elapsed = max(0, min(end - start + 1, int((as_of - opening).total_seconds() // 60)))
        due = range(start, start + elapsed)
        slots = inventory.get(iso_day, {})
        complete = slots.get("complete", set())
        partial = slots.get("partial", set()) - complete
        applied = {m for a, b in receipts.get("days", {}).get(iso_day, []) for m in range(a, b + 1)}
        unseen = unknown and iso_day <= unknown.date().isoformat()
        exit_at = engine.scheduled_exit_at(iso_day, iso_day, "intraday")
        checkpoints = [("entry", m) for m in decision_minutes if start <= m <= end]
        checkpoints += [("exit", exit_at.hour * 60 + exit_at.minute), ("close", end)]
        windows = []
        for kind, minute in checkpoints:
            available_at = opening + timedelta(minutes=minute - start)
            due_now = minute in due
            archive = ("complete" if minute in complete else "partial" if minute in partial
                       else "absent" if due_now else "not_due")
            if minute in applied:
                paper = "accepted"
            elif unseen and available_at <= unknown:
                paper = "unverified"
            elif not due_now:
                paper = "not_due"
            elif pending_from and pending_from <= (iso_day, minute):
                paper = "processing_pending"
            else:
                paper = "no_accepted_report"
            windows.append({"kind": kind, "time": available_at.strftime("%H:%M"), "archive": archive, "paper": paper,
                            "paper_entries": events[iso_day, minute, "entry"], "paper_exits": events[iso_day, minute, "exit"]})
        complete_count = len(complete.intersection(due))
        partial_count = len(partial.intersection(due))
        total = end - start + 1
        sessions.append({
            "day": iso_day, "phase": "preopen" if as_of < opening else "ended" if elapsed == total else "in_progress",
            "total_slots": total, "elapsed_slots": elapsed, "complete_slots": complete_count,
            "partial_only_slots": partial_count, "absent_slots": elapsed - complete_count - partial_count,
            "applied_slots": len(applied.intersection(due)), "applied_history_partial": bool(unseen), "windows": windows,
            **_positions_at_session(state, opening, min(as_of, closing + timedelta(minutes=1, microseconds=-1))),
        })
    return {"as_of": as_of.isoformat(timespec="seconds"), "sessions": sessions}


def format_session_evidence(evidence):
    lines = ["## Trading session coverage", "", f"Archive listing as of {evidence['as_of']} (IST).",
             "Minute counts cover elapsed slots. Complete and partial refer to archive filenames at capture start. "
             "Accepted reports use the time data became available to the paper engine.", "",
             "| Session | Status | Elapsed / scheduled | Complete | Partial only | Absent | Accepted |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for row in evidence["sessions"]:
        accepted = str(row["applied_slots"]) + (" (tracked portion)" if row["applied_history_partial"] else "")
        lines.append(f"| {row['day']} | {row['phase']} | {row['elapsed_slots']} / {row['total_slots']} | "
                     f"{row['complete_slots']} | {row['partial_only_slots']} | {row['absent_slots']} | {accepted} |")
    lines.extend(["", "| Session | Scheduled minute (IST) | Archive | Paper report | Quote-backed paper entries / exits |",
                  "|---|---|---|---|---:|"])
    for row in evidence["sessions"]:
        for window in row["windows"]:
            lines.append(f"| {row['day']} | {window['time']} {window['kind']} | {window['archive']} | "
                         f"{window['paper']} | {window['paper_entries']} / {window['paper_exits']} |")
    lines.extend(["", "| Session | Paper positions carried in | Carried with a fresh mark | Due and still open |",
                  "|---|---:|---:|---:|"])
    for row in evidence["sessions"]:
        lines.append(f"| {row['day']} | {row['carried_in']} | {row['carry_with_fresh_mark']} | {row['overdue_open']} |")
    lines.extend(["", "A recorded report can produce zero trades. Paper entry and exit counts require a recorded "
                  "price and timestamp for every leg of the basket. Position counts use the processed paper book at "
                  "the session cutoff. Processing pending means the worker has outstanding files at or before that minute.", ""])
    return "\n".join(lines)
