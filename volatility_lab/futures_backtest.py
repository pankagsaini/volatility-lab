"""Dated futures carry backtest engine with LRU archive caching and expiry roll/exit handling."""
from __future__ import annotations

import gzip
import json
import math
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path
from threading import RLock
from typing import Any

import numpy as np
import pandas as pd
from backtest_common import is_blacklisted_future, record_skip
from broker_charges import charge
from market_calendar import last_trading_day_on_or_before

ROOT = Path(__file__).resolve().parent / "data"
_FILE_CACHE: OrderedDict[str, Any] = OrderedDict()
_CACHE_LOCK = RLock()
_MAX_CACHE_FILES = 128
_MAX_CACHE_ROWS = 15000
_CACHED_ROWS = 0


def _daily_archive(symbol: str, day: str) -> dict[str, Any]:
    """Thread-safe LRU cached reader for daily compressed futures archives."""
    global _CACHED_ROWS
    key = f"{symbol}:{day}"
    with _CACHE_LOCK:
        if key in _FILE_CACHE:
            _FILE_CACHE.move_to_end(key)
            return _FILE_CACHE[key]

    archive_path = ROOT / "futures_archive" / symbol / f"{day}.json.gz"
    if not archive_path.exists():
        return {}
    with gzip.open(archive_path, "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    row_count = len(payload) if isinstance(payload, (dict, list)) else 1

    with _CACHE_LOCK:
        _FILE_CACHE[key] = payload
        _CACHED_ROWS += row_count
        while len(_FILE_CACHE) > _MAX_CACHE_FILES or _CACHED_ROWS > _MAX_CACHE_ROWS:
            _, evicted = _FILE_CACHE.popitem(last=False)
            _CACHED_ROWS = max(0, _CACHED_ROWS - (len(evicted) if isinstance(evicted, (dict, list)) else 1))
    return payload


def load(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Load daily futures contract bars between `start` and `end` excluding blacklisted contracts."""
    from futures_data import connection
    with connection() as conn:
        df = pd.read_sql_query(
            "SELECT symbol, expiry, day, open, high, low, close, volume, oi "
            "FROM candles WHERE symbol = ? AND day >= ? AND day <= ? ORDER BY day, expiry",
            conn,
            params=(symbol, start, end),
        )
    if df.empty:
        return df
    mask = ~df.apply(lambda r: is_blacklisted_future(r["symbol"], r["expiry"]), axis=1)
    return df.loc[mask].reset_index(drop=True)


def broker_lot_size(contract: dict[str, Any] | int | None, default: int = 65) -> int:
    if isinstance(contract, int) and contract > 0:
        return contract
    if isinstance(contract, dict) and int(contract.get("lot_size", 0)) > 0:
        return int(contract["lot_size"])
    return default


def contract_lot_sizes(symbol: str) -> dict[str, int]:
    path = ROOT / "futures_lots" / f"{symbol}.json"
    if path.exists():
        return {k: int(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
    return {}


def roll_schedule(
    symbol: str,
    start: str,
    end: str,
    pair: tuple[int, int] = (0, 1),
    lot_size: int = 65,
) -> pd.DataFrame:
    try:
        from futures_rolling import roll_calendar
        cal = roll_calendar(symbol, start, end, pair=pair)
    except ImportError:
        cal = []
    lots = contract_lot_sizes(symbol)
    rows = []
    for item in cal:
        near_exp, far_exp = item["near_expiry"], item["far_expiry"]
        rows.append({
            **item,
            "near_lot_size": lots.get(near_exp, lot_size),
            "far_lot_size": lots.get(far_exp, lot_size),
        })
    return pd.DataFrame(rows)


def simulate(
    bars: pd.DataFrame,
    pair: tuple[int, int],
    entry_carry: float,
    target_carry: float,
    quantity: int,
    near_side: int = 1,
    slip_bps: float = 5.0,
    max_hold_days: int | None = None,
    brokerage_per_order: float = 20.0,
    lots: int | None = None,
    contracts: dict[str, Any] | None = None,
    auto_roll_on_expiry: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Simulate futures calendar spread strategy with executable expiry-day open exits/rolls."""
    contracts = contracts or {}
    skips: list[dict[str, str]] = []
    trades: list[dict[str, Any]] = []
    position: dict[str, Any] | None = None

    def leg_quantities(exps: tuple[str, str]) -> tuple[int, int]:
        if lots is not None and int(lots) > 0:
            ln = broker_lot_size(contracts.get(exps[0]), default=int(quantity))
            lf = broker_lot_size(contracts.get(exps[1]), default=int(quantity))
            return int(lots) * ln, int(lots) * lf
        return int(quantity), int(quantity)

    def prices(day_str: str, exps: tuple[str, str], idx: int = 0) -> tuple[float, float] | None:
        col = "open" if idx == 0 and "open" in bars.columns else "close"
        sub = bars[(bars["day"] == day_str) & (bars["expiry"].isin(exps))]
        if len(sub) < 2:
            return None
        by_exp = dict(zip(sub["expiry"], sub[col]))
        p0, p1 = by_exp.get(exps[0]), by_exp.get(exps[1])
        if p0 is None or p1 is None or p0 <= 0 or p1 <= 0 or not (math.isfinite(p0) and math.isfinite(p1)):
            return None
        return float(p0), float(p1)

    def costs(day_str: str, px: tuple[float, float], opening: bool, qn: int, qf: int) -> tuple[float, float, float]:
        s_near = near_side if opening else -near_side
        s_far = -near_side if opening else near_side
        fn = px[0] * (1.0 + s_near * slip_bps / 10000.0)
        ff = px[1] * (1.0 + s_far * slip_bps / 10000.0)
        act_near = "buy" if s_near > 0 else "sell"
        act_far = "buy" if s_far > 0 else "sell"
        c_near = sum(charge(day_str, "Futures", fn, qn, act_near, brokerage_per_order).values())
        c_far = sum(charge(day_str, "Futures", ff, qf, act_far, brokerage_per_order).values())
        return fn, ff, float(c_near + c_far)

    def mark_position(day_str: str) -> dict[str, Any] | None:
        if position is None:
            return None
        px = prices(day_str, position["exps"], idx=1)
        if px is None:
            return None
        qn, qf = position["q_near"], position["q_far"]
        fn, ff, exit_fee = costs(day_str, px, False, qn, qf)
        gross = (
            near_side * (fn - position["entry_near"]) * qn
            - near_side * (ff - position["entry_far"]) * qf
        )
        return {
            "mark_day": day_str,
            "near_mark": fn,
            "far_mark": ff,
            "unrealized_gross": round(gross, 2),
            "unrealized_net": round(gross - position["entry_fee"] - exit_fee, 2),
        }

    def unfinished(reason: str) -> dict[str, Any]:
        if position is None:
            return {}
        last_day = sorted_days[-1] if sorted_days else position["entry_day"]
        marked = mark_position(last_day) or {}
        return {**position, **marked, "status": "open", "reason": reason}

    sorted_days = sorted(bars["day"].unique()) if not bars.empty else []
    pending_entry_exps: tuple[str, str] | None = None
    pending_exit_reason: str | None = None

    for day_str in sorted_days:
        day_exps = sorted(e for e in bars.loc[bars["day"] == day_str, "expiry"].unique() if str(e) > day_str)
        active_exps = (day_exps[pair[0]], day_exps[pair[1]]) if len(day_exps) > max(pair) else None

        # 1. Handle open position at or approaching front-leg expiry
        if position is not None:
            front_exp = min(position["exps"])
            hold_days = (date.fromisoformat(day_str) - date.fromisoformat(position["entry_day"])).days
            if day_str >= front_exp:
                pending_exit_reason = "Expiry roll/close at front-leg expiry open"
            elif max_hold_days is not None and hold_days >= int(max_hold_days):
                pending_exit_reason = "Max hold days reached"

            if pending_exit_reason is not None:
                px = prices(day_str, position["exps"], idx=0)
                if px is not None:
                    qn, qf = position["q_near"], position["q_far"]
                    fn, ff, exit_fee = costs(day_str, px, False, qn, qf)
                    gross = (
                        near_side * (fn - position["entry_near"]) * qn
                        - near_side * (ff - position["entry_far"]) * qf
                    )
                    total_fees = position["entry_fee"] + exit_fee
                    trades.append({
                        "EntryDay": position["entry_day"],
                        "ExitDay": day_str,
                        "NearExpiry": position["exps"][0],
                        "FarExpiry": position["exps"][1],
                        "ExitReason": pending_exit_reason,
                        "GrossPnL": round(gross, 2),
                        "Charges": round(total_fees, 2),
                        "NetPnL": round(gross - total_fees, 2),
                    })
                    was_expiry_roll = (day_str >= front_exp and auto_roll_on_expiry and active_exps and min(active_exps) > day_str)
                    position = None
                    pending_exit_reason = None
                    if was_expiry_roll:
                        pending_entry_exps = active_exps
                else:
                    record_skip(skips, day_str, "Missing executable exit quotes on scheduled exit/expiry day")

        # 2. Execute pending next-session open entry
        if position is None and pending_entry_exps is not None:
            if min(pending_entry_exps) > day_str:
                px = prices(day_str, pending_entry_exps, idx=0)
                if px is not None:
                    qn, qf = leg_quantities(pending_entry_exps)
                    fn, ff, entry_fee = costs(day_str, px, True, qn, qf)
                    position = {
                        "entry_day": day_str,
                        "exps": pending_entry_exps,
                        "q_near": qn,
                        "q_far": qf,
                        "entry_near": fn,
                        "entry_far": ff,
                        "entry_fee": entry_fee,
                    }
                else:
                    record_skip(skips, day_str, "Missing next-session open quote for entry")
            pending_entry_exps = None

        # 3. Evaluate end-of-day carry signals for next-session execution
        target_exps = position["exps"] if position is not None else active_exps
        if target_exps is None:
            continue
        close_px = prices(day_str, target_exps, idx=1)
        if close_px is None:
            continue
        gap = (date.fromisoformat(target_exps[1]) - date.fromisoformat(target_exps[0])).days
        if gap <= 0:
            continue
        carry = float(np.log(close_px[1] / close_px[0]) * 365.0 / gap * 100.0)

        if position is None:
            if (near_side == 1 and carry >= entry_carry) or (near_side == -1 and carry <= entry_carry):
                pending_entry_exps = target_exps
        else:
            if (near_side == 1 and carry <= target_carry) or (near_side == -1 and carry >= target_carry):
                pending_exit_reason = "Target carry reached"

    coverage = {"unfinished": unfinished("End of sample reached with open position") if position else None}
    return pd.DataFrame(trades), pd.DataFrame(skips), coverage
