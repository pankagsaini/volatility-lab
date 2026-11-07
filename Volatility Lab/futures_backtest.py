"""Dated futures carry backtest engine with LRU archive caching, dual DataFrame/dict dispatch, and expiry roll/exit handling."""
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
_FILE_CACHE_LOCK = _CACHE_LOCK
_MAX_CACHE_FILES = 2048
_MAX_CACHE_ROWS = 75000
_CACHED_ROWS = 0
_FILE_CACHE_ROWS = 0


def clear_load_cache():
    global _FILE_CACHE_ROWS, _CACHED_ROWS
    with _CACHE_LOCK:
        _FILE_CACHE.clear()
        _FILE_CACHE_ROWS = 0
        _CACHED_ROWS = 0


def _daily_archive(path_or_symbol: Any, day: str | None = None) -> Any:
    """Thread-safe LRU cached reader for daily compressed futures archives (supports path or (symbol, day))."""
    global _CACHED_ROWS, _FILE_CACHE_ROWS
    if day is None:
        path = Path(path_or_symbol)
        stat = path.stat()
        revision = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        key = str(path)
        with _CACHE_LOCK:
            saved = _FILE_CACHE.get(key)
            if saved is not None and saved[0] == revision:
                _FILE_CACHE.move_to_end(key)
                return saved[1]
        with gzip.open(path, "rt", encoding="utf-8") as f:
            o = json.load(f)
        meta = o.get("meta", {})
        contract = meta.get("contract", {})
        route = o.get("path", "")
        value = None
        weight = 0
        if o.get("provider") == "Upstox" and contract.get("instrument_type") == "FUT" and ("/day/" in route or "/days/1/" in route or "/historical/day/" in route):
            candles = o.get("data", {}).get("candles", [])
            value = (meta.get("symbol"), contract, candles)
            weight = len(candles) if isinstance(candles, list) else 0
        with _CACHE_LOCK:
            old = _FILE_CACHE.pop(key, None)
            if old is not None:
                _FILE_CACHE_ROWS -= old[2]
                _CACHED_ROWS = max(0, _CACHED_ROWS - old[2])
            if weight <= _MAX_CACHE_ROWS:
                while _FILE_CACHE and (len(_FILE_CACHE) >= _MAX_CACHE_FILES or _FILE_CACHE_ROWS + weight > _MAX_CACHE_ROWS):
                    _, removed = _FILE_CACHE.popitem(last=False)
                    _FILE_CACHE_ROWS -= removed[2]
                    _CACHED_ROWS = max(0, _CACHED_ROWS - removed[2])
                _FILE_CACHE[key] = (revision, value, weight)
                _FILE_CACHE_ROWS += weight
                _CACHED_ROWS += weight
        return value
    else:
        symbol = str(path_or_symbol)
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
            _FILE_CACHE_ROWS += row_count
            while len(_FILE_CACHE) > _MAX_CACHE_FILES or _CACHED_ROWS > _MAX_CACHE_ROWS:
                _, evicted = _FILE_CACHE.popitem(last=False)
                cnt = len(evicted) if isinstance(evicted, (dict, list)) else 1
                _CACHED_ROWS = max(0, _CACHED_ROWS - cnt)
                _FILE_CACHE_ROWS = max(0, _FILE_CACHE_ROWS - cnt)
        return payload


def broker_lot_size(contract: dict[str, Any] | int | None, default: int | None = None) -> int | None:
    if isinstance(contract, dict):
        v = contract.get("lot_size")
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    elif isinstance(contract, (int, float)) and contract > 0:
        return int(contract)
    return default


def contract_lot_sizes(symbol: str) -> dict[str, int]:
    path = ROOT / "futures_lots" / f"{symbol}.json"
    if path.exists():
        try:
            return {k: int(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
        except Exception:
            pass
    sizes = {}
    for p in sorted(ROOT.resolve().glob("*.json.gz")):
        saved = _daily_archive(p)
        if saved is None or saved[0] != symbol:
            continue
        _, r, _ = saved
        exp = r.get("expiry")
        if not exp:
            continue
        exp_str = str(pd.to_datetime(exp, unit="ms", utc=True).date()) if isinstance(exp, (int, float)) else str(exp)[:10]
        if is_blacklisted_future(symbol, exp_str):
            continue
        lot = broker_lot_size(r)
        if lot and exp_str not in sizes:
            sizes[exp_str] = lot
    return sizes


def load(symbol: str, start: str, end: str):
    """Load futures bars; returns (bars, conflicts) if json.gz archives exist, otherwise returns DataFrame."""
    gz_files = list(ROOT.resolve().glob("*.json.gz"))
    if gz_files:
        bars = {}
        conflicts = set()
        first, last = str(start), str(end)
        for p in sorted(gz_files):
            saved = _daily_archive(p)
            if saved is None or saved[0] != symbol:
                continue
            _, r, candles = saved
            exp = r["expiry"]
            exp = str(pd.to_datetime(exp, unit="ms", utc=True).date()) if isinstance(exp, (int, float)) else str(exp)[:10]
            if is_blacklisted_future(symbol, exp):
                continue
            for row in candles:
                day = str(row[0])[:10]
                if not first <= day <= last:
                    continue
                key = (day, exp)
                value = tuple(float(v) for v in row[1:6])
                if key in bars and bars[key] != value:
                    conflicts.add(key)
                bars[key] = value
        for key in conflicts:
            bars.pop(key, None)
        return bars, len(conflicts)

    try:
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
    except Exception:
        return pd.DataFrame()


def roll_schedule(
    symbol: str,
    start: str,
    end: str,
    pair: tuple[int, int] = (0, 1),
    lot_size: int = 75,
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


def _simulate_dict(
    bars: dict[tuple[str, str], tuple[float, float, float, float, float]],
    pair: tuple[int, int] = (0, 2),
    entry: float = 6.0,
    target: float = 6.66,
    quantity: int = 75,
    near_side: int = -1,
    slip_bps: float = 1.0,
    max_days: int = 30,
    brokerage_per_order: float = 20.0,
    lots: int | None = None,
    contracts: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any] | None]:
    if entry == target or near_side not in (-1, 1):
        raise ValueError("Choose distinct thresholds and a direction")
    by_day: dict[str, list[str]] = {}
    for day, expiry in bars:
        by_day.setdefault(day, [])
        if expiry > day:
            by_day[day].append(expiry)
    for expiries in by_day.values():
        expiries.sort()
    days = sorted(by_day)
    position: dict[str, Any] | None = None
    pending = None
    trades = []
    skips = []
    stamps: dict[str, pd.Timestamp] = {}

    def stamp(day: str) -> pd.Timestamp:
        if day not in stamps:
            stamps[day] = pd.Timestamp(day)
        return stamps[day]

    def eligible(day: str) -> list[str]:
        return by_day[day]

    def prices(day: str, exps: tuple[str, str] | list[str], index: int) -> list[float] | None:
        values = [bars.get((day, e)) for e in exps]
        if any(v is None or v[4] <= 0 or not all(math.isfinite(x) and x > 0 for x in v[:4]) for v in values):
            return None
        return [v[index] for v in values]

    def carry(px: list[float], exps: tuple[str, str] | list[str]) -> float:
        return math.log(px[1] / px[0]) * 365 / (stamp(exps[1]) - stamp(exps[0])).days * 100

    def leg_quantities(exps: tuple[str, str] | list[str]) -> tuple[int, int]:
        if lots is not None and lots > 0:
            c_near = (contracts or {}).get(exps[0])
            c_far = (contracts or {}).get(exps[1])
            lot_near = broker_lot_size(c_near) or quantity
            lot_far = broker_lot_size(c_far) or quantity
            return int(lots) * lot_near, int(lots) * lot_far
        return quantity, quantity

    def costs(day: str, px: list[float], opening: bool, qn: int, qf: int) -> dict[str, float]:
        out: dict[str, float] = {}
        for value, side, q in zip(px, [near_side, -near_side], [qn, qf]):
            action = "buy" if side * (1 if opening else -1) == 1 else "sell"
            for k, v in charge(day, "Futures", value, q, action, brokerage_per_order).items():
                out[k] = out.get(k, 0.0) + v
        return out

    def mark_position(day: str) -> bool:
        if not position:
            return False
        px = prices(day, position["exps"], 3)
        if not px:
            position["last_mark_issue"] = "Missing exact-contract close"
            return False
        sides = [near_side, -near_side]
        qs = [position["q_near"], position["q_far"]]
        gross = sum(s * (b - a) * q for s, a, b, q in zip(sides, position["raw"], px, qs))
        after_entry = sum(s * (b - a) * q for s, a, b, q in zip(sides, position["fills"], px, qs)) - sum(position["costs"].values())
        position["last_mark"] = {"date": day, "prices": px, "carry": carry(px, position["exps"]), "gross": gross, "after_entry_costs": after_entry}
        position["last_mark_issue"] = None
        return True

    def unfinished(reason: str) -> dict[str, Any] | None:
        if not position:
            return None
        last = position.get("last_mark") or {}
        qn, qf = position["q_near"], position["q_far"]
        rec = {
            "Status": reason,
            "Entry": position["day"],
            "Near expiry": position["exps"][0],
            "Far expiry": position["exps"][1],
            "Direction": "Buy near / sell far" if near_side == 1 else "Sell near / buy far",
            "Quantity per leg": qn if qn == qf else f"{qn} (near) / {qf} (far)",
            "Entry carry %": position["carry"],
            "Entry fill near": position["fills"][0],
            "Entry fill far": position["fills"][1],
            "Estimated entry costs": sum(position["costs"].values()),
            "Last mark date": last.get("date"),
            "Last mark near close": (last.get("prices") or [None, None])[0],
            "Last mark far close": (last.get("prices") or [None, None])[1],
            "Last mark carry %": last.get("carry"),
            "Unrealised gross": last.get("gross"),
            "Unrealised P&L before exit costs": last.get("after_entry_costs"),
            "Mark status": "Latest exact-contract close" if last else position.get("last_mark_issue", "No exact-contract close after entry"),
        }
        if lots is not None and lots > 0:
            rec["Sizing"] = "broker_lot_size"
            rec["Strategy lots"] = int(lots)
        return rec

    final_reason = None
    for day in days:
        if pending:
            kind, exps, signalday, signalcarry, reason = pending
            px = prices(day, exps, 0)
            if kind == "entry" and (stamp(day) - stamp(signalday)).days > 7:
                px = None
            if min(exps) <= day:
                if position:
                    final_reason = "Expiry boundary reached before an executable exit"
                    skips.append({"Date": day, "Reason": final_reason})
                    break
                pending = None
            elif px:
                sides = [near_side, -near_side]
                fills = [v * (1 + s * (1 if kind == "entry" else -1) * slip_bps / 10000) for v, s in zip(px, sides)]
                if kind == "entry":
                    qn, qf = leg_quantities(exps)
                    position = {
                        "day": day,
                        "exps": exps,
                        "raw": px,
                        "fills": fills,
                        "q_near": qn,
                        "q_far": qf,
                        "costs": costs(day, fills, True, qn, qf),
                        "carry": signalcarry,
                        "last_mark": None,
                        "last_mark_issue": None,
                    }
                else:
                    qn, qf = position["q_near"], position["q_far"]
                    gross = sum(s * (b - a) * q for s, a, b, q in zip(sides, position["raw"], px, [qn, qf]))
                    net_before = sum(s * (b - a) * q for s, a, b, q in zip(sides, position["fills"], fills, [qn, qf]))
                    components = costs(day, fills, False, qn, qf)
                    for k, v in position["costs"].items():
                        components[k] += v
                    trade = {
                        "Entry": position["day"],
                        "Exit": day,
                        "Near expiry": exps[0],
                        "Far expiry": exps[1],
                        "Entry carry %": position["carry"],
                        "Exit signal carry %": signalcarry,
                        "Gross": gross,
                        "Slippage": gross - net_before,
                        "Charges": sum(components.values()),
                        "Net": net_before - sum(components.values()),
                        "Reason": reason,
                        **components,
                    }
                    if lots is not None and lots > 0:
                        trade["Sizing"] = "broker_lot_size"
                        trade["Strategy lots"] = int(lots)
                        trade["Near lot size"] = qn // int(lots) if lots else None
                        trade["Far lot size"] = qf // int(lots) if lots else None
                        trade["Near quantity"] = qn
                        trade["Far quantity"] = qf
                    trades.append(trade)
                    position = None
                pending = None
            else:
                missing = "Missing exact contract open; " + kind + " not filled"
                skips.append({"Date": day, "Reason": missing})
                if kind == "entry":
                    pending = None
                elif position:
                    position["last_exit_issue"] = missing
        if pending:
            mark_position(day)
            continue
        if position:
            if min(position["exps"]) <= day:
                final_reason = "Expiry boundary reached before an executable exit"
                skips.append({"Date": day, "Reason": final_reason})
                break
            marked = mark_position(day)
            if not marked:
                skips.append({"Date": day, "Reason": "Missing held-contract close"})
                continue
            exps = position["exps"]
            c = position["last_mark"]["carry"]
            reason = "Carry target" if (c >= target if target > entry else c <= target) else "Max holding period" if (stamp(day) - stamp(position["day"])).days >= max_days else "Pre-expiry exit" if (stamp(exps[0]) - stamp(day)).days <= 3 else None
            if reason:
                pending = ("exit", exps, day, c, reason)
        else:
            exps = eligible(day)
            if len(exps) <= max(pair):
                continue
            exps = [exps[i] for i in pair]
            px = prices(day, exps, 3)
            if not px or (stamp(exps[0]) - stamp(day)).days <= 4:
                continue
            c = carry(px, exps)
            if (c <= entry if target > entry else c >= entry):
                pending = ("entry", exps, day, c, "Entry threshold")

    if position:
        if final_reason is None:
            if pending and pending[0] == "exit":
                final_reason = "Pending exit: " + pending[4]
                if position.get("last_exit_issue"):
                    final_reason += "; exact exit open unavailable"
            else:
                final_reason = "End of selected period"
        return pd.DataFrame(trades), pd.DataFrame(skips), unfinished(final_reason)
    return pd.DataFrame(trades), pd.DataFrame(skips), None


def _simulate_df(
    bars: pd.DataFrame,
    pair: tuple[int, int] = (0, 1),
    entry_carry: float = 8.0,
    target_carry: float = 2.0,
    quantity: int = 75,
    near_side: int = 1,
    slip_bps: float = 5.0,
    max_hold_days: int | None = None,
    brokerage_per_order: float = 20.0,
    lots: int | None = None,
    contracts: dict[str, Any] | None = None,
    auto_roll_on_expiry: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    contracts = contracts or {}
    skips: list[dict[str, str]] = []
    trades: list[dict[str, Any]] = []
    position: dict[str, Any] | None = None

    def leg_quantities(exps: tuple[str, str]) -> tuple[int, int]:
        if lots is not None and int(lots) > 0:
            ln = broker_lot_size(contracts.get(exps[0]), default=int(quantity)) or int(quantity)
            lf = broker_lot_size(contracts.get(exps[1]), default=int(quantity)) or int(quantity)
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


def simulate(
    bars: Any,
    pair: tuple[int, int] = (0, 1),
    entry: float | None = None,
    target: float | None = None,
    quantity: int = 75,
    near_side: int = -1,
    slip_bps: float = 1.0,
    max_days: int | None = None,
    brokerage_per_order: float = 20.0,
    lots: int | None = None,
    contracts: dict[str, Any] | None = None,
    entry_carry: float | None = None,
    target_carry: float | None = None,
    max_hold_days: int | None = None,
    auto_roll_on_expiry: bool = False,
    **kwargs: Any,
) -> tuple[pd.DataFrame, pd.DataFrame, Any]:
    """Unified entrypoint dispatching to dictionary simulation or DataFrame simulation based on bars type."""
    if isinstance(bars, dict):
        e_val = entry if entry is not None else (entry_carry if entry_carry is not None else 6.0)
        t_val = target if target is not None else (target_carry if target_carry is not None else 6.66)
        m_val = max_days if max_days is not None else (max_hold_days if max_hold_days is not None else 30)
        return _simulate_dict(
            bars=bars,
            pair=pair,
            entry=e_val,
            target=t_val,
            quantity=quantity,
            near_side=near_side,
            slip_bps=slip_bps,
            max_days=m_val,
            brokerage_per_order=brokerage_per_order,
            lots=lots,
            contracts=contracts,
        )
    elif isinstance(bars, pd.DataFrame):
        e_val = entry_carry if entry_carry is not None else (entry if entry is not None else 8.0)
        t_val = target_carry if target_carry is not None else (target if target is not None else 2.0)
        m_val = max_hold_days if max_hold_days is not None else max_days
        return _simulate_df(
            bars=bars,
            pair=pair,
            entry_carry=e_val,
            target_carry=t_val,
            quantity=quantity,
            near_side=near_side,
            slip_bps=slip_bps,
            max_hold_days=m_val,
            brokerage_per_order=brokerage_per_order,
            lots=lots,
            contracts=contracts,
            auto_roll_on_expiry=auto_roll_on_expiry,
        )
    else:
        raise TypeError(f"Unsupported bars type: {type(bars)}")
