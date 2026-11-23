"""Single writer for the prospective paper ledger. Inputs are private, read-only Drive snapshots."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from volatility_lab.backtest import forward_test as engine
from volatility_lab.backtest.execution_evidence import verify_execution_evidence
from volatility_lab.ingestion.google_archive_sync import validate_report
from volatility_lab.runtime.settings import load_settings
from volatility_lab.storage.collector_support import atomic_json, data_lock
from volatility_lab.core.market_calendar import session_window

AUTHORITY = "python_paper_worker"
CAPTURE = re.compile(r"(\d{4}-\d{2}-\d{2})_snapshot_(\d{6})_complete\.json\.gz")


class PendingCapture(ValueError):
    """The newest snapshot needs another read after the previous captures were committed."""


def initial_state(start, replay=False):
    if not replay and date.fromisoformat(start) < datetime.now(engine.IST_TZ).date():
        raise ValueError("Cannot backdate a new prospective book. Use --replay in a separate directory.")

    if not session_window(start):
        raise ValueError("Choose a trading session for the prospective cohort start.")
    return dict(
        copy.deepcopy(engine.CLEAN_SLATE_LEDGER),
        source_authority=AUTHORITY,
        policy_version="research-v1",
        cohort_kind="historical_replay" if replay else "prospective",
        prospective_start=start,
        inception_date=start,
        min_trigger_date=start,
        updated_at=None,
        status="ARMED_WAITING_FOR_FIRST_CAPTURE",
        last_capture_at=None,
        worker_checkpoint={"cursor": "", "completed": 0, "quarantine": [], "last_day": None},
    )


def public_state(state):
    """Allowlist public research fields. Full capture paths and provenance stay private."""

    def select(row, keys):
        return {k: row[k] for k in keys.split() if k in row}

    result = select(
        state,
        "schema version provenance_schema status source_authority cohort_kind policy_version "
        "inception_date prospective_start min_trigger_date updated_at last_capture_at",
    )
    trade_fields = (
        "id date session_date sym exp kind dte strat stratName frontScope holding policy_version "
        "triggerTime available_at markedAt mark_at trigTime entryTime exitTime exit_at exit_session_date "
        "scheduled_exit_at exitReason freshness exit_pending manual_reconciliation_required integrity_status "
        "provenance_status path_status entryIv entry_vrp_pp curIv capital maxLoss maxCredit netPremium maxProfit "
        "lots grossMtm estCharges netMtm gross charges net metrics sig trigReason"
    )
    for bucket in ("open_positions", "closed_trades"):
        result[bucket] = []
        for trade in state[bucket]:
            row = select(trade, trade_fields)
            row["legs"] = [
                select(leg, "strike type side mult entryPx curPx exitPx entryQuoteAt markQuoteAt exitQuoteAt")
                for leg in trade.get("legs", [])
            ]
            result[bucket].append(row)
    result["intraday_mtm_series"] = [
        select(point, "session_date t time_ist gross_mtm net_mtm open_count closed_count integrity_status")
        for point in state["intraday_mtm_series"]
        if point.get("session_date") == state["worker_checkpoint"]["last_day"]
    ]
    checkpoint = state["worker_checkpoint"]
    result["capture_audit"] = {"processed": checkpoint["completed"], "exceptions": len(checkpoint["quarantine"])}
    return result


def _read_report(path):
    if path.stat().st_size > 20_000_000:
        raise ValueError("Compressed capture exceeds the archive limit")
    with gzip.open(path, "rb") as fp:
        body = fp.read(40_000_001)
    if len(body) > 40_000_000:
        raise ValueError("Decompressed capture exceeds the archive limit")
    report = validate_report(json.loads(body))
    if report.get("mode") != "snapshot" or report.get("day") != path.name[:10]:
        raise ValueError("Capture identity mismatch")
    return report, hashlib.sha256(body).hexdigest()  # Hash of uncompressed JSON bytes.


def _note_gap(state, detail):
    state["worker_checkpoint"]["quarantine"].append(detail)
    for pos in state["open_positions"]:
        pos.setdefault("unobserved_path_since", detail["from"])
        pos["path_status"] = "INCOMPLETE_CAPTURE_PATH"
        pos["provenance_status"] = "GAP_AFFECTED"


def _minutes_missing(previous, current):
    if not previous:
        window = session_window(current.date())
        return window[0] if window and current.replace(second=0, microsecond=0) > window[0] else None
    minute = previous.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = current.replace(second=0, microsecond=0)
    while minute < end:
        window = session_window(minute.date())
        if window and max(minute, window[0]) < min(end, window[1] + timedelta(minutes=1)):
            return max(minute, window[0])
        minute = (minute + timedelta(days=1)).replace(hour=0, minute=0)
    return None


def _apply(state, report, name, digest):
    cp = state["worker_checkpoint"]
    current = engine._parse_aware_timestamp(report.get("available_at") or report["retrieved_at"])
    if current is None:
        raise ValueError("Undated capture")
    if current > datetime.now(engine.IST_TZ):
        raise ValueError("Future capture")
    previous = engine._parse_aware_timestamp(state["last_capture_at"])
    if previous and current <= previous:
        _note_gap(state, {"from": name, "reason": "non-increasing capture time"})
        return state
    if not engine.get_nse_market_status_ist(current)["open"]:
        return state
    missing = _minutes_missing(previous, current)
    if missing is not None:
        _note_gap(state, {"from": missing.isoformat(), "next": current.isoformat(), "reason": "missing market capture"})
    if not previous and report["day"] > state["prospective_start"]:
        _note_gap(
            state, {"from": state["prospective_start"], "next": report["day"], "reason": "no initial session capture"}
        )
    decisions = []
    processed = engine.process_live_upstox_snapshot(report, ledger=state, decision_receipts=decisions)
    if processed.get("gate_blocked"):
        raise ValueError("Engine blocked the market capture")
    for key in (
        "source_authority",
        "policy_version",
        "prospective_start",
        "inception_date",
        "min_trigger_date",
        "cohort_kind",
    ):
        processed[key] = state[key]
    for trade in processed["closed_trades"]:
        if trade.get("exit_at") == current.isoformat():
            verify_execution_evidence(report, trade, "exit")
            trade["exit_capture"] = name
            trade["exit_capture_sha256"] = digest
    for pos in processed["open_positions"] + processed["closed_trades"]:
        if pos.get("triggerTime") == current.isoformat():
            verify_execution_evidence(report, pos, "entry")
            pos["entry_capture"] = name
            pos["entry_capture_sha256"] = digest
    processed.update(worker_checkpoint=cp, last_capture_at=current.isoformat())
    if decisions:
        cp.setdefault("entry_decisions", {}).setdefault(report["day"], []).append({
            "available_at": current.isoformat(), "capture_sha256": digest, "decisions": decisions,
        })
    cp["completed"] += 1
    cp["last_day"] = report["day"]
    return processed


def run_once(archive, directory, start, output=None, *, replay=False):
    """Commit results and cursor together; retrying after interruption cannot duplicate trades."""
    if replay and output:
        raise ValueError("Historical replay cannot publish a prospective artifact.")
    archive, directory = Path(archive).resolve(), Path(directory).resolve()
    if not archive.is_dir():
        raise ValueError("Private archive is unavailable. Mount it and retry.")
    if directory == archive or archive in directory.parents:
        raise ValueError("Keep paper state outside the raw collector archive.")
    ledger = directory / engine.LEDGER_FILENAME
    if output and (Path(output).resolve() == ledger or archive in Path(output).resolve().parents):
        raise ValueError("Public export must be separate from the ledger and raw archive.")
    directory.mkdir(parents=True, exist_ok=True)
    with data_lock(lock_path=ledger.with_suffix(".lock"), timeout=10):
        if ledger.exists():
            state = json.loads(ledger.read_bytes())
            engine._validate_ledger(state)
            if not (
                state.get("source_authority") == AUTHORITY
                and isinstance(state.get("worker_checkpoint"), dict)
                and state.get("prospective_start") == start
                and state.get("cohort_kind", "prospective") == ("historical_replay" if replay else "prospective")
            ):
                raise ValueError("Directory contains another ledger or cohort. Use its original configuration.")
        else:
            if ledger.with_suffix(".bak").exists():
                raise ValueError("Ledger is missing while its backup exists. Reconcile before continuing.")
            state = initial_state(start, replay)
            state["worker_checkpoint"]["archive_dir"] = str(archive)
            atomic_json(ledger, state)
        state.setdefault("cohort_kind", "prospective")
        checkpoint = state["worker_checkpoint"]
        if checkpoint.get("archive_dir") and Path(checkpoint["archive_dir"]) != archive:
            raise ValueError("Private archive directory differs from the checkpoint. Reconcile before switching.")
        checkpoint["archive_dir"] = str(archive)
        pending = None
        cursor = checkpoint["cursor"]
        paths = sorted(
            p
            for p in archive.glob("*_snapshot_*_complete.json.gz")
            if CAPTURE.fullmatch(p.name) and p.name[:10] >= start and p.name > cursor
        )
        for i, path in enumerate(paths):
            try:
                report, digest = _read_report(path)
                state = _apply(state, report, path.name, digest)
            except (EOFError, OSError, ValueError) as exc:
                # A partially hydrated newest Drive file can complete on the next pass.
                if i == len(paths) - 1:
                    pending = f"Pending capture {path.name}: {type(exc).__name__}"
                    break
                _note_gap(state, {"from": path.name, "reason": type(exc).__name__})
            checkpoint["cursor"] = path.name
            atomic_json(ledger, state)
        if output:
            candidate = public_state(state)
            target = Path(output)
            if not target.exists() or json.loads(target.read_bytes()) != candidate:
                atomic_json(target, candidate)
        if pending:
            raise PendingCapture(pending)
        return state


def worker_options(parser):
    parser.add_argument("--process-archive", action="store_true", help="Process local Drive snapshots")
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--start-date", help="First prospective trading date (YYYY-MM-DD)")
    parser.add_argument("--public-output", type=Path, help="Independent local JSON export for the dashboard")
    parser.add_argument(
        "--replay", action="store_true", help="Isolated retrospective validation; public export disabled"
    )
    parser.add_argument("--watch", action="store_true", help="Poll during runtime; restart safely via the cursor")


def run_command(args):
    settings = load_settings()
    archive = args.archive_dir or settings.archive_dir
    if not archive:
        from volatility_lab.ingestion.google_archive_sync import source_folder

        archive = source_folder()
    if not archive:
        raise ValueError("Configure a local --archive-dir or VOL_LAB_ARCHIVE_DIR.")
    directory = args.data_dir or (settings.data_dir / "paper-worker")
    start = args.start_date
    if not start:
        existing = Path(directory) / engine.LEDGER_FILENAME
        if existing.exists():
            start = json.loads(existing.read_bytes()).get("prospective_start")
        if not start:
            raise ValueError("First launch requires an explicit --start-date.")
    while True:
        try:
            state = run_once(archive, directory, start, args.public_output, replay=args.replay)
        except (OSError, ValueError, RuntimeError) as exc:
            if not args.watch:
                raise
            print(f"Paper worker pending: {exc}", flush=True)
        else:
            print(
                f"{state['status']} | last capture {state['last_capture_at']} | "
                f"{state['worker_checkpoint']['completed']} captured",
                flush=True,
            )
        if not args.watch:
            break
        time.sleep(30)
