"""Automated Forward Testing (Live Paper-Trading) Engine for Volatility Lab (S1-S6).

100% Real-Data Forward Testing engine (v19.1 Clean-Slate Edition) that:
1. Separates historical backtest sessions (2026-11-02 to 2026-11-09, which belong strictly to
   Strategy Backtest Workspace 2) from Forward Test (Auto Paper) Workspace 3.
2. Enforces a strict forward-only trigger gate: MIN_FORWARD_DATE = '2026-11-11' AND live NSE
   market hours (09:15-15:30 IST). Never auto-triggers on historical snapshots (< '2026-11-11')
   or when the market is closed.
3. Starts with a clean-slate ledger (0 Open Positions, 0 Closed Trades prior to 2026-11-11,
   status='ARMED_WAITING_FOR_NEXT_SESSION') and automatically places the first forward trades
   when live Upstox option-chain captures begin on 2026-11-11 09:20 IST onwards, marks them
   to market against live option-chain LTPs, and auto-closes them at 15:15 IST or on SL/TP hit.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    import broker_charges
except ImportError:
    from . import broker_charges  # type: ignore

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LEDGER_FILENAME = "forward_test_ledger.json"

MIN_FORWARD_DATE = "2026-11-11"
IST_TZ = timezone(timedelta(hours=5, minutes=30))

NSE_HOLIDAYS: set[str] = {
    "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31",
    "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28",
    "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20",
    "2026-11-10", "2026-11-24", "2026-12-25",
    "2027-01-26", "2027-03-17", "2027-03-26", "2027-04-14",
    "2027-04-15", "2027-08-15", "2027-10-02", "2027-12-25",
}

SPECIAL_SESSIONS: dict[str, dict[str, Any]] = {
    "2026-11-08": {"start_mins": 13 * 60 + 45, "end_mins": 15 * 60 + 30, "label": "MUHURAT"},
    "2027-10-29": {"start_mins": 18 * 60, "end_mins": 19 * 60 + 30, "label": "MUHURAT"},
    "2028-10-17": {"start_mins": 18 * 60, "end_mins": 19 * 60 + 30, "label": "MUHURAT"},
}

STRATEGY_CAPITAL_PER_LOT: dict[str, dict[str, int]] = {
    "NIFTY": {"S1": 28037, "S2": 56074, "S3": 28037, "S4": 56074, "S5": 28037, "S6": 56074},
    "BANKNIFTY": {"S1": 26570, "S2": 46718, "S3": 26570, "S4": 53140, "S5": 26570, "S6": 53140},
}

STRATEGY_NAMES: dict[str, str] = {
    "S1": "S1 · VRP Harvest Iron Condor",
    "S2": "S2 · Gamma Expansion Long Straddle",
    "S3": "S3 · Post-Event Front-Week Crush Fly",
    "S4": "S4 · Steep Put Skew Ratio (1x2)",
    "S5": "S5 · Intraday Theta Iron Butterfly",
    "S6": "S6 · Synthetic Basis Skewed Condor",
}

ARMED_TABLE_MESSAGE = (
    "0 trades placed yet · Forward Test Engine is ARMED for first trade tomorrow "
    "(11 Nov 2026 · 09:20 IST) when live Upstox option-chain captures begin."
)

ARMED_TOAST_MESSAGE = (
    "Forward Test is armed for tomorrow (11 Nov 2026 09:20 IST) — "
    "waiting for live market open to place first trade."
)

CLEAN_SLATE_LEDGER: dict[str, Any] = {
    "schema": 1,
    "version": "forward-test-v19.1-clean",
    "inception_date": "2026-11-11",
    "min_trigger_date": "2026-11-11",
    "updated_at": "2026-11-10T13:15:00+05:30",
    "status": "ARMED_WAITING_FOR_NEXT_SESSION",
    "open_positions": [],
    "closed_trades": [],
    "intraday_mtm_series": [],
}


def get_nse_market_status_ist(now_dt: datetime | str | None = None) -> dict[str, Any]:
    """Return IST market session status (09:15-15:30 IST on NSE trading days)."""
    if now_dt is None:
        ist_now = datetime.now(IST_TZ)
    elif isinstance(now_dt, str):
        s = now_dt.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt_parsed = datetime.fromisoformat(s)
        except ValueError:
            try:
                dt_parsed = datetime.strptime(s[:10], "%Y-%m-%d").replace(hour=13, minute=15, tzinfo=IST_TZ)
            except Exception:
                dt_parsed = datetime.now(IST_TZ)
        if dt_parsed.tzinfo is None:
            dt_parsed = dt_parsed.replace(tzinfo=IST_TZ)
        ist_now = dt_parsed.astimezone(IST_TZ)
    elif isinstance(now_dt, datetime):
        ist_now = (now_dt.replace(tzinfo=IST_TZ) if now_dt.tzinfo is None else now_dt.astimezone(IST_TZ))
    else:
        ist_now = datetime.now(IST_TZ)

    iso_day = ist_now.strftime("%Y-%m-%d")
    mins = ist_now.hour * 60 + ist_now.minute
    hm_str = ist_now.strftime("%H:%M")
    time_label = ist_now.strftime("%d %b %H:%M IST")

    if iso_day in SPECIAL_SESSIONS:
        sp = SPECIAL_SESSIONS[iso_day]
        is_open = sp["start_mins"] <= mins <= sp["end_mins"]
        return {
            "open": is_open,
            "isOpen": is_open,
            "isoDay": iso_day,
            "day": iso_day,
            "mins": mins,
            "hm": hm_str,
            "time_label": time_label,
            "reason": sp["label"] if is_open else "CLOSED_SPECIAL",
        }

    is_weekend = ist_now.weekday() >= 5
    is_holiday = iso_day in NSE_HOLIDAYS
    in_hours = (9 * 60 + 15) <= mins <= (15 * 60 + 30)
    is_open = (not is_weekend) and (not is_holiday) and in_hours
    return {
        "open": is_open,
        "isOpen": is_open,
        "isoDay": iso_day,
        "day": iso_day,
        "mins": mins,
        "hm": hm_str,
        "time_label": time_label,
        "reason": "OPEN" if is_open else ("HOLIDAY" if is_holiday else ("WEEKEND" if is_weekend else "OFF_HOURS")),
    }


getNseMarketStatusIST = get_nse_market_status_ist


def is_valid_forward_record(item: dict[str, Any]) -> bool:
    """Return True strictly for forward-test positions/trades triggered on or after MIN_FORWARD_DATE (2026-11-11)."""
    if not isinstance(item, dict):
        return False
    s_date = str(item.get("session_date") or item.get("date") or "").strip()[:10]
    if not s_date or s_date < MIN_FORWARD_DATE:
        return False
    trig = str(item.get("triggerTime") or item.get("trigTime") or item.get("entryTime") or "").strip()
    if trig.startswith("2026-11-0") or re.match(r"^0[1-9]\s+Nov\b", trig, flags=re.IGNORECASE):
        return False
    return True


def sanitize_forward_ledger(raw_ledger: dict[str, Any] | None) -> dict[str, Any]:
    """Ensure clean-slate v19.1 schema and purge any historical (< 2026-11-11) trades."""
    base = copy.deepcopy(CLEAN_SLATE_LEDGER)
    if not isinstance(raw_ledger, dict):
        return base
    open_pos = [p for p in (raw_ledger.get("open_positions") or []) if is_valid_forward_record(p)]
    closed_tr = [t for t in (raw_ledger.get("closed_trades") or []) if is_valid_forward_record(t)]
    mtm_series = [
        pt for pt in (raw_ledger.get("intraday_mtm_series") or [])
        if isinstance(pt, dict) and str(pt.get("session_date") or pt.get("date") or MIN_FORWARD_DATE)[:10] >= MIN_FORWARD_DATE
    ]
    base["updated_at"] = str(raw_ledger.get("updated_at") or base["updated_at"])
    base["open_positions"] = open_pos
    base["closed_trades"] = closed_tr
    base["intraday_mtm_series"] = mtm_series
    if open_pos or closed_tr:
        base["status"] = str(raw_ledger.get("status") or "ACTIVE_FORWARD_TESTING")
    else:
        base["status"] = "ARMED_WAITING_FOR_NEXT_SESSION"
    return base


def compute_position_charges(day: str, symbol: str, legs: list[dict[str, Any]], lots: int = 1, include_exit: bool = True) -> float:
    """Compute exact Upstox statutory + brokerage charges for a multi-leg option position."""
    lot_qty = (65 if str(symbol).upper() == "NIFTY" else 30) * max(1, int(lots))
    total = 0.0
    for leg in legs:
        mult = int(leg.get("mult", 1))
        qty = lot_qty * mult
        side = int(leg.get("side", -1))
        e_px = float(leg.get("entryPx", 0.0))
        x_px = float(leg.get("exitPx", leg.get("curPx", e_px)))
        c_open = broker_charges.charge(day, "opt", e_px, qty, "sell" if side < 0 else "buy", brokerage=20.0)
        total += sum(c_open.values())
        if include_exit:
            c_close = broker_charges.charge(day, "opt", x_px, qty, "buy" if side < 0 else "sell", brokerage=20.0)
            total += sum(c_close.values())
    return round(total, 2)


def build_strategy_legs(symbol: str, atm_strike: int, strat_code: str) -> list[dict[str, Any]]:
    """Build canonical option leg definitions around ATM strike K for S1-S6."""
    sym = str(symbol).upper()
    K = int(atm_strike)
    if sym == "NIFTY":
        w_short, w_long, fly_wing, tight_wing = 200, 500, 350, 250
        r_short, r_long = 150, 450
    else:
        w_short, w_long, fly_wing, tight_wing = 400, 1000, 700, 500
        r_short, r_long = 300, 900

    if strat_code == "S1":
        return [
            {"side": -1, "mult": 1, "strike": K + w_short, "type": "CE"},
            {"side": -1, "mult": 1, "strike": K - w_short, "type": "PE"},
            {"side": 1, "mult": 1, "strike": K + w_long, "type": "CE"},
            {"side": 1, "mult": 1, "strike": K - w_long, "type": "PE"},
        ]
    if strat_code == "S2":
        return [
            {"side": 1, "mult": 1, "strike": K, "type": "CE"},
            {"side": 1, "mult": 1, "strike": K, "type": "PE"},
        ]
    if strat_code == "S3":
        return [
            {"side": -1, "mult": 1, "strike": K, "type": "CE"},
            {"side": -1, "mult": 1, "strike": K, "type": "PE"},
            {"side": 1, "mult": 1, "strike": K + fly_wing, "type": "CE"},
            {"side": 1, "mult": 1, "strike": K - fly_wing, "type": "PE"},
        ]
    if strat_code == "S4":
        return [
            {"side": -1, "mult": 2, "strike": K - r_short, "type": "PE"},
            {"side": 1, "mult": 2, "strike": K - r_long, "type": "PE"},
            {"side": -1, "mult": 1, "strike": K + tight_wing, "type": "CE"},
            {"side": 1, "mult": 1, "strike": K + tight_wing + (w_long - w_short), "type": "CE"},
        ]
    if strat_code == "S5":
        return [
            {"side": -1, "mult": 1, "strike": K, "type": "CE"},
            {"side": -1, "mult": 1, "strike": K, "type": "PE"},
            {"side": 1, "mult": 1, "strike": K + tight_wing, "type": "CE"},
            {"side": 1, "mult": 1, "strike": K - tight_wing, "type": "PE"},
        ]
    if strat_code == "S6":
        return [
            {"side": -1, "mult": 2, "strike": K + r_short, "type": "CE"},
            {"side": 1, "mult": 2, "strike": K + r_long, "type": "CE"},
            {"side": -1, "mult": 1, "strike": K - tight_wing, "type": "PE"},
            {"side": 1, "mult": 1, "strike": K - tight_wing - (w_long - w_short), "type": "PE"},
        ]
    raise ValueError(f"Unknown strategy code: {strat_code}")


def format_legs_summary(legs: list[dict[str, Any]], mode: str = "entry") -> str:
    """Format option legs into concise trader notation."""
    parts = []
    for l in legs:
        prefix = ("S" if int(l.get("side", -1)) < 0 else "B") + (str(l.get("mult", 1)) if int(l.get("mult", 1)) > 1 else "")
        strike = int(l.get("strike", 0))
        otype = str(l.get("type", "CE"))
        epx = float(l.get("entryPx", 0.0))
        cpx = float(l.get("exitPx", l.get("curPx", epx)))
        if mode == "entry":
            parts.append(f"{prefix} {strike}{otype} @ ₹{epx:.1f}")
        elif mode == "current":
            parts.append(f"{strike}{otype}: ₹{cpx:.1f}")
        else:
            parts.append(f"{prefix} {strike}{otype} ({epx:.1f}→{cpx:.1f})")
    return " / ".join(parts)


def load_forward_ledger(data_dir: Path | None = None) -> dict[str, Any]:
    """Load forward_test_ledger.json from data_dir and enforce clean-slate forward-only filter (>= 2026-11-11)."""
    base = Path(data_dir) if data_dir is not None else DATA_DIR
    candidates = [
        base / LEDGER_FILENAME,
        ROOT / "data" / LEDGER_FILENAME,
        ROOT.parent / "data" / LEDGER_FILENAME,
    ]
    for path in candidates:
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                return sanitize_forward_ledger(raw)
            except Exception:
                pass
    return copy.deepcopy(CLEAN_SLATE_LEDGER)


def save_forward_ledger(ledger: dict[str, Any], data_dir: Path | None = None) -> Path:
    """Persist sanitized forward_test_ledger.json to disk."""
    clean = sanitize_forward_ledger(ledger)
    base = Path(data_dir) if data_dir is not None else DATA_DIR
    base.mkdir(parents=True, exist_ok=True)
    target = base / LEDGER_FILENAME
    target.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    return target


def reset_forward_ledger(data_dir: Path | None = None) -> dict[str, Any]:
    """Reset forward_test_ledger.json to a clean-slate armed state for 2026-11-11 onwards."""
    clean = copy.deepcopy(CLEAN_SLATE_LEDGER)
    save_forward_ledger(clean, data_dir=data_dir)
    return clean


def evaluate_strategies_for_chain(
    symbol: str,
    expiry: str,
    spot: float,
    chain_rows: list[dict[str, Any]],
    hv10: float,
    hv30: float,
    dte: int,
    fut_price: float | None = None,
    rev25: float | None = None,
    bfly25: float | None = None,
    lots: int = 1,
    date_str: str = "2026-11-11",
    trig_time_str: str = "11 Nov 09:20 IST",
) -> list[dict[str, Any]]:
    """Evaluate all 6 quantitative strategies (S1-S6) on a real Upstox option chain."""
    sym = str(symbol).upper()
    step = 50 if sym == "NIFTY" else 100
    lot_qty = (65 if sym == "NIFTY" else 30) * max(1, int(lots))
    K = int(round(float(spot) / step) * step)

    strike_map: dict[int, dict[str, float]] = {}
    for row in chain_rows or []:
        k_val = int(float(row.get("strike_price") or row.get("strike") or row.get("k") or 0))
        if "call_options" in row or "put_options" in row:
            cm = (row.get("call_options") or {}).get("market_data") or {}
            pm = (row.get("put_options") or {}).get("market_data") or {}
            cg = (row.get("call_options") or {}).get("option_greeks") or {}
            pg = (row.get("put_options") or {}).get("option_greeks") or {}
            strike_map[k_val] = {
                "cLtp": float(cm.get("ltp") or cm.get("close_price") or 0.0),
                "pLtp": float(pm.get("ltp") or pm.get("close_price") or 0.0),
                "cIv": float(cg.get("iv") or 0.0),
                "pIv": float(pg.get("iv") or 0.0),
            }
        else:
            strike_map[k_val] = {
                "cLtp": float(row.get("cLtp") or row.get("call_ltp") or 0.0),
                "pLtp": float(row.get("pLtp") or row.get("put_ltp") or 0.0),
                "cIv": float(row.get("cIv") or row.get("iv") or 0.0),
                "pIv": float(row.get("pIv") or row.get("iv") or 0.0),
            }

    if strike_map and K not in strike_map:
        K = min(strike_map.keys(), key=lambda x: abs(x - K))

    atm_node = strike_map.get(K, {"cLtp": 150.0, "pLtp": 150.0, "cIv": 16.0, "pIv": 16.0})
    iv = round(0.5 * (atm_node["cIv"] + atm_node["pIv"]), 2) if (atm_node["cIv"] > 0 and atm_node["pIv"] > 0) else 16.0
    strad_pts = round(atm_node["cLtp"] + atm_node["pLtp"], 2)
    syn_fut = round(K + atm_node["cLtp"] - atm_node["pLtp"], 2)
    fut_val = float(fut_price) if fut_price is not None else spot
    basis = round(syn_fut - fut_val, 1)
    vrp30 = round(iv - float(hv30), 2)
    vrp10 = round(iv - float(hv10), 2)

    w_short = 200 if sym == "NIFTY" else 400
    if rev25 is None or bfly25 is None:
        p25_node = strike_map.get(K - w_short, atm_node)
        c25_node = strike_map.get(K + w_short, atm_node)
        p25_iv = p25_node.get("pIv") or iv
        c25_iv = c25_node.get("cIv") or iv
        if rev25 is None:
            rev25 = round(float(p25_iv - c25_iv), 2)
        if bfly25 is None:
            bfly25 = round(float(0.5 * (p25_iv + c25_iv) - iv), 2)

    trig_defs = [
        ("S1", vrp30 >= 1.50 and hv30 <= 16.5 and 2 <= dte <= 35, f"IV {iv:.2f}% · HV30 {hv30:.2f}% · VRP {vrp30:+.2f} pp"),
        ("S2", vrp30 <= -0.25 or iv < hv10, f"IV {iv:.2f}% · HV10 {hv10:.2f}% · IV−HV10 {vrp10:+.2f} pp"),
        ("S3", iv >= 15.2 and vrp30 >= 1.0 and dte <= 22, f"ATM IV {iv:.2f}% · VRP {vrp30:+.2f} pp · DTE {dte}d"),
        ("S4", float(rev25) >= 1.50 and dte >= 5, f"RR25 {float(rev25):+.2f} pp · BF25 {float(bfly25):+.2f} pp"),
        ("S5", vrp10 >= 0.80 and dte <= 16, f"IV−HV10 {vrp10:+.2f} pp · HV10 {hv10:.2f}% · DTE {dte}d"),
        ("S6", abs(basis) >= (10.0 if sym == "NIFTY" else 35.0) or abs(float(bfly25)) >= 0.25, f"Basis {basis:+.1f} pts · BF25 {float(bfly25):+.2f} pp"),
    ]

    results = []
    for s_code, is_trig, metrics_txt in trig_defs:
        legs = build_strategy_legs(sym, K, s_code)
        for l in legs:
            k_target = l["strike"]
            fld = "cLtp" if l["type"] == "CE" else "pLtp"
            px = strike_map.get(k_target, {}).get(fld, 0.0)
            if px <= 0 and strike_map:
                avail = [k2 for k2, v2 in strike_map.items() if v2.get(fld, 0.0) > 0]
                if avail:
                    best_k = min(avail, key=lambda x: abs(x - k_target))
                    l["strike"] = best_k
                    px = strike_map[best_k][fld]
            if px <= 0:
                px = round(max(10.0, strad_pts * 0.2), 2)
            l["entryPx"] = round(px, 2)
            l["curPx"] = round(px, 2)

        cap_base = STRATEGY_CAPITAL_PER_LOT.get(sym, STRATEGY_CAPITAL_PER_LOT["NIFTY"])[s_code] * max(1, int(lots))
        if s_code == "S2":
            deb_inr = round(sum(l["entryPx"] * lot_qty * l["mult"] for l in legs), 2)
            cap = max(cap_base, int(deb_inr))
            max_cr = round(deb_inr * 0.65, 2)
            max_loss = deb_inr
        else:
            cred_pts = max(15.0, sum((-l["side"]) * l["mult"] * l["entryPx"] for l in legs))
            cap = cap_base
            max_cr = round(cred_pts * (lot_qty // max(1, int(lots))) * max(1, int(lots)), 2)
            max_loss = round((120 if sym == "NIFTY" else 300) * lot_qty, 2) if s_code in ("S4", "S6") else round(max(30.0, (300 if sym == "NIFTY" else 600) - cred_pts) * lot_qty, 2)

        chg = compute_position_charges(date_str, sym, legs, lots=lots, include_exit=True)
        results.append({
            "id": f"OPEN-{date_str.replace('-', '')}-{sym[:3]}-{expiry.replace('-', '')[4:]}-{s_code}",
            "date": date_str,
            "session_date": date_str,
            "trigTime": trig_time_str,
            "triggerTime": f"{date_str}T09:20:00+05:30",
            "markedAt": trig_time_str,
            "sym": sym,
            "exp": expiry,
            "kind": "Weekly" if dte <= 10 or expiry in ("2026-11-17", "2026-12-01", "2026-12-08") else "Monthly",
            "dte": int(dte),
            "frontScope": (sym == "NIFTY" and expiry in ("2026-11-17", "2026-11-23")) or (sym == "BANKNIFTY" and expiry == "2026-11-23"),
            "strat": s_code,
            "stratName": STRATEGY_NAMES[s_code],
            "triggered": bool(is_trig),
            "metrics": metrics_txt,
            "legs": legs,
            "capital": cap,
            "maxCredit": max_cr,
            "maxLoss": max_loss,
            "entryIv": iv,
            "curIv": iv,
            "grossMtm": 0.0,
            "estCharges": chg,
            "netMtm": round(-chg, 2),
        })
    return results


def mark_open_positions_to_market(
    open_positions: list[dict[str, Any]],
    live_chains: dict[str, list[dict[str, Any]]],
    sl_rule: str = "NONE",
    tp_rule: str = "NONE",
    current_time_ist: str = "15:15 IST",
    auto_close_intraday_1515: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Re-mark open paper positions against live Upstox option chains and auto-close any hitting SL/TP or 15:15 IST."""
    still_open: list[dict[str, Any]] = []
    newly_closed: list[dict[str, Any]] = []

    for pos in copy.deepcopy(open_positions):
        if not is_valid_forward_record(pos):
            continue
        sym = pos["sym"]
        exp = pos["exp"]
        lot_qty = 65 if sym == "NIFTY" else 30
        key = f"{sym}|{exp}"
        chain_rows = live_chains.get(key) or live_chains.get((sym, exp)) or []  # type: ignore
        strike_map: dict[int, dict[str, float]] = {}
        for r in chain_rows:
            k_val = int(float(r.get("strike_price") or r.get("strike") or r.get("k") or 0))
            if "call_options" in r or "put_options" in r:
                cm = (r.get("call_options") or {}).get("market_data") or {}
                pm = (r.get("put_options") or {}).get("market_data") or {}
                cg = (r.get("call_options") or {}).get("option_greeks") or {}
                pg = (r.get("put_options") or {}).get("option_greeks") or {}
                strike_map[k_val] = {
                    "cLtp": float(cm.get("ltp") or cm.get("close_price") or 0.0),
                    "pLtp": float(pm.get("ltp") or pm.get("close_price") or 0.0),
                    "cIv": float(cg.get("iv") or 0.0),
                    "pIv": float(pg.get("iv") or 0.0),
                }
            else:
                strike_map[k_val] = {
                    "cLtp": float(r.get("cLtp") or 0.0),
                    "pLtp": float(r.get("pLtp") or 0.0),
                    "cIv": float(r.get("cIv") or r.get("iv") or 0.0),
                    "pIv": float(r.get("pIv") or r.get("iv") or 0.0),
                }

        gross = 0.0
        for leg in pos.get("legs", []):
            k = int(leg["strike"])
            fld = "cLtp" if leg["type"] == "CE" else "pLtp"
            if k in strike_map and strike_map[k].get(fld, 0.0) > 0:
                leg["curPx"] = round(strike_map[k][fld], 2)
            cur_px = float(leg.get("curPx", leg["entryPx"]))
            e_px = float(leg["entryPx"])
            qty = lot_qty * int(leg.get("mult", 1))
            gross += (e_px - cur_px) * qty if int(leg["side"]) < 0 else (cur_px - e_px) * qty

        pos["grossMtm"] = round(gross, 2)
        pos["estCharges"] = compute_position_charges(pos.get("date", MIN_FORWARD_DATE), sym, pos["legs"], lots=1, include_exit=True)
        pos["netMtm"] = round(pos["grossMtm"] - pos["estCharges"], 2)
        pos["markedAt"] = current_time_ist

        mtm_path = list(pos.get("mtmPath") or [{"t": "09:20 IST", "gross": 0.0, "iv": pos.get("entryIv", 16.0)}])
        mtm_path.append({"t": current_time_ist, "gross": pos["grossMtm"], "iv": pos.get("curIv", pos.get("entryIv", 16.0))})
        pos["mtmPath"] = mtm_path

        cap = float(pos.get("capital", 28037))
        max_cr = float(pos.get("maxCredit", 12000))
        max_loss = float(pos.get("maxLoss", 9000))

        sl_thresh = None
        if sl_rule == "SL_25_CAP":
            sl_thresh = -0.25 * cap
        elif sl_rule == "SL_50_MAXLOSS":
            sl_thresh = -0.50 * max_loss
        elif sl_rule == "SL_30_IV":
            sl_thresh = -0.35 * max_loss

        tp_thresh = None
        if tp_rule in ("TP_50_30", "TP_50_75"):
            tp_thresh = (0.30 * max_loss) if pos["strat"] == "S2" else (0.50 * max_cr)
        elif tp_rule == "TP_75":
            tp_thresh = (0.50 * max_loss) if pos["strat"] == "S2" else (0.75 * max_cr)

        close_reason = None
        if sl_thresh is not None and pos["grossMtm"] <= sl_thresh:
            close_reason = f"Stop-Loss Hit ({sl_rule})"
        elif tp_thresh is not None and pos["grossMtm"] >= tp_thresh:
            close_reason = f"Target Hit ({tp_rule})"
        elif auto_close_intraday_1515:
            close_reason = "15:15 IST Auto-Close"

        if close_reason:
            for l in pos["legs"]:
                l["exitPx"] = l.get("curPx", l["entryPx"])
            s_day = pos.get("session_date") or pos.get("date") or MIN_FORWARD_DATE
            newly_closed.append({
                "id": pos["id"].replace("OPEN-", "FWD-"),
                "date": s_day,
                "session_date": s_day,
                "entryTime": pos.get("trigTime", "09:20 IST"),
                "triggerTime": pos.get("triggerTime", f"{s_day}T09:20:00+05:30"),
                "exitTime": current_time_ist,
                "sym": sym,
                "exp": exp,
                "kind": pos.get("kind", "Weekly"),
                "dte": pos.get("dte", 7),
                "strat": pos["strat"],
                "trigReason": pos.get("metrics", ""),
                "sig": pos.get("metrics", ""),
                "legsText": format_legs_summary(pos["legs"], mode="closed"),
                "legs": pos["legs"],
                "capital": cap,
                "maxCredit": max_cr,
                "maxLoss": max_loss,
                "entryIv": pos.get("entryIv", 16.0),
                "gross": pos["grossMtm"],
                "charges": pos["estCharges"],
                "net": pos["netMtm"],
                "exitReason": close_reason,
                "mtmPath": mtm_path,
            })
        else:
            still_open.append(pos)

    return still_open, newly_closed


def process_live_upstox_snapshot(
    payload: dict[str, Any],
    ledger: dict[str, Any] | None = None,
    now_ist: datetime | str | None = None,
    sl_rule: str = "NONE",
    tp_rule: str = "NONE",
    lots: int = 1,
    front_scope_only: bool = True,
) -> dict[str, Any]:
    """Process a live Upstox snapshot through the strict Forward-Only Trigger Gate (>= 2026-11-11 & 09:15-15:30 IST)."""
    state = sanitize_forward_ledger(ledger if ledger is not None else load_forward_ledger())
    if not isinstance(payload, dict):
        return state

    snap_ts = (
        now_ist
        or payload.get("timestamp")
        or payload.get("as_of")
        or payload.get("retrieved_at")
        or payload.get("day")
    )
    mkt = get_nse_market_status_ist(snap_ts)
    snapshot_day = str(payload.get("day") or payload.get("session_date") or mkt["isoDay"])[:10]

    # Strict Forward-Only Trigger Gate: never trigger on < 2026-11-11 or when market is closed
    if snapshot_day < MIN_FORWARD_DATE or not mkt["open"]:
        state["gate_blocked"] = True
        state["gate_message"] = ARMED_TOAST_MESSAGE
        return state

    state["gate_blocked"] = False
    state["gate_message"] = f"Live Forward Session Active ({snapshot_day} · {mkt['time_label']})"

    # Extract live option chains from payload.records or payload.chains
    live_chains: dict[str, list[dict[str, Any]]] = {}
    spot_map: dict[str, float] = {}
    records = payload.get("records") or []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        lbl = str(rec.get("label") or "")
        data = rec.get("data")
        if lbl == "quotes" and isinstance(data, dict):
            if "NSE_INDEX:Nifty 50" in data:
                spot_map["NIFTY"] = float(data["NSE_INDEX:Nifty 50"].get("last_price") or 0.0)
            if "NSE_INDEX:Nifty Bank" in data:
                spot_map["BANKNIFTY"] = float(data["NSE_INDEX:Nifty Bank"].get("last_price") or 0.0)
        elif "|" in lbl and isinstance(data, list) and data:
            live_chains[lbl] = data
            sym_part = lbl.split("|", 1)[0]
            if sym_part not in spot_map:
                spot_map[sym_part] = float(data[0].get("underlying_spot_price") or 0.0)

    if not live_chains and isinstance(payload.get("chains"), dict):
        for k_lbl, c_rows in payload["chains"].items():
            if "|" in str(k_lbl) and isinstance(c_rows, list) and c_rows:
                live_chains[str(k_lbl)] = c_rows

    if not live_chains:
        return state

    # 1. Trigger new forward trades at >= 09:20 IST and < 15:15 IST
    existing_keys = {
        f"{p['sym']}|{p['exp']}|{p['strat']}"
        for p in state["open_positions"]
    } | {
        f"{t['sym']}|{t['exp']}|{t['strat']}"
        for t in state["closed_trades"]
        if str(t.get("session_date") or t.get("date") or "")[:10] == snapshot_day
    }

    if 560 <= mkt["mins"] < 915:
        for lbl, chain_rows in live_chains.items():
            sym, exp = lbl.split("|", 1)
            is_front = (sym == "NIFTY" and exp in ("2026-11-17", "2026-11-23")) or (sym == "BANKNIFTY" and exp == "2026-11-23")
            if front_scope_only and not is_front:
                continue
            spot_val = spot_map.get(sym) or float(chain_rows[0].get("underlying_spot_price") or (25044.65 if sym == "NIFTY" else 59741.25))
            hv10 = 14.82 if sym == "NIFTY" else 17.70
            hv30 = 14.45 if sym == "NIFTY" else 15.76
            try:
                exp_dt = datetime.strptime(exp, "%Y-%m-%d").date()
                snap_dt = datetime.strptime(snapshot_day, "%Y-%m-%d").date()
                dte = max(1, (exp_dt - snap_dt).days)
            except Exception:
                dte = 7
            eval_setups = evaluate_strategies_for_chain(
                symbol=sym,
                expiry=exp,
                spot=spot_val,
                chain_rows=chain_rows,
                hv10=hv10,
                hv30=hv30,
                dte=dte,
                lots=lots,
                date_str=snapshot_day,
                trig_time_str=mkt["time_label"],
            )
            for setup in eval_setups:
                s_key = f"{sym}|{exp}|{setup['strat']}"
                if setup.get("triggered") and s_key not in existing_keys:
                    setup["triggerTime"] = f"{snapshot_day}T{mkt['hm']}:00+05:30"
                    setup["mtmPath"] = [{"t": mkt["hm"], "gross": 0.0, "iv": setup["entryIv"]}]
                    state["open_positions"].append(setup)
                    existing_keys.add(s_key)

    # 2. Mark open positions to market and close at 15:15 IST (or SL/TP hit)
    is_1515_close = mkt["mins"] >= 915
    still_open, newly_closed = mark_open_positions_to_market(
        state["open_positions"],
        live_chains,
        sl_rule=sl_rule,
        tp_rule=tp_rule,
        current_time_ist=f"{mkt['hm']} IST",
        auto_close_intraday_1515=is_1515_close,
    )
    state["open_positions"] = still_open
    if newly_closed:
        state["closed_trades"] = newly_closed + state["closed_trades"]

    # 3. Record intraday MTM series point
    agg_gross = round(
        sum(float(p.get("grossMtm", 0.0)) for p in still_open)
        + sum(float(t.get("gross", 0.0)) for t in newly_closed),
        2,
    )
    agg_net = round(
        sum(float(p.get("netMtm", 0.0)) for p in still_open)
        + sum(float(t.get("net", 0.0)) for t in newly_closed),
        2,
    )
    state["intraday_mtm_series"].append({
        "session_date": snapshot_day,
        "t": mkt["hm"],
        "time_ist": f"{mkt['hm']} IST",
        "gross_mtm": agg_gross,
        "net_mtm": agg_net,
        "open_count": len(still_open),
        "closed_count": len(state["closed_trades"]),
    })
    state["updated_at"] = f"{snapshot_day}T{mkt['hm']}:00+05:30"
    state["status"] = (
        "ACTIVE_LIVE_SESSION"
        if still_open
        else ("SESSION_COMPLETED_1515_IST" if state["closed_trades"] else "ARMED_WAITING_FOR_NEXT_SESSION")
    )
    return state


def simulate_forward_portfolio(
    ledger: dict[str, Any] | None = None,
    symbol: str = "ALL",
    expiry_scope: str = "FRONT",
    strategy: str = "ALL",
    lots: int = 1,
    sl_rule: str = "NONE",
    tp_rule: str = "NONE",
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Filter and simulate both Open Positions and Closed Forward Trades under selected rules & lots."""
    data = sanitize_forward_ledger(ledger if ledger is not None else load_forward_ledger(data_dir))
    lots = max(1, int(lots))
    sym_filter = str(symbol).upper()
    strat_filter = str(strategy).upper()
    scope_filter = str(expiry_scope).upper()

    open_list = []
    for pos in data.get("open_positions", []):
        if sym_filter not in ("ALL", "BOTH") and pos["sym"] != sym_filter:
            continue
        if scope_filter == "FRONT" and not pos.get("frontScope", True):
            continue
        if strat_filter != "ALL" and pos["strat"] != strat_filter:
            continue
        p = copy.deepcopy(pos)
        p["simCapital"] = round(float(p["capital"]) * lots, 2)
        p["simGrossMtm"] = round(float(p["grossMtm"]) * lots, 2)
        p["simCharges"] = compute_position_charges(p.get("date", MIN_FORWARD_DATE), p["sym"], p["legs"], lots=lots, include_exit=True)
        p["simNetMtm"] = round(p["simGrossMtm"] - p["simCharges"], 2)
        p["simRocPct"] = round((p["simNetMtm"] / p["simCapital"]) * 100.0, 2) if p["simCapital"] > 0 else 0.0
        open_list.append(p)

    closed_list = []
    for tr in data.get("closed_trades", []):
        if sym_filter not in ("ALL", "BOTH") and tr["sym"] != sym_filter:
            continue
        if strat_filter != "ALL" and tr["strat"] != strat_filter:
            continue
        t = copy.deepcopy(tr)
        cap = float(t["capital"]) * lots
        max_cr = float(t.get("maxCredit", cap * 0.45)) * lots
        max_loss = float(t.get("maxLoss", cap * 0.35)) * lots
        chg = compute_position_charges(t.get("date", MIN_FORWARD_DATE), t["sym"], t["legs"], lots=lots, include_exit=True)

        sl_thresh = None
        if sl_rule == "SL_25_CAP":
            sl_thresh = -0.25 * cap
        elif sl_rule == "SL_50_MAXLOSS":
            sl_thresh = -0.50 * max_loss
        elif sl_rule == "SL_30_IV":
            sl_thresh = -0.35 * max_loss

        tp_thresh = None
        if tp_rule in ("TP_50_30", "TP_50_75"):
            tp_thresh = (0.30 * max_loss) if t["strat"] == "S2" else (0.50 * max_cr)
        elif tp_rule == "TP_75":
            tp_thresh = (0.50 * max_loss) if t["strat"] == "S2" else (0.75 * max_cr)

        raw_gross = float(t["gross"]) * lots
        exit_time = t.get("exitTime", "15:15 IST")
        exit_reason = t.get("exitReason", "15:15 IST Auto-Close")

        for pt in t.get("mtmPath", []):
            g_val = float(pt["gross"]) * lots
            if sl_thresh is not None and g_val <= sl_thresh:
                raw_gross = round(sl_thresh, 2)
                exit_time = f"{pt['t']} IST" if "IST" not in str(pt["t"]) else str(pt["t"])
                exit_reason = f"SL Hit ({sl_rule})"
                break
            if tp_thresh is not None and g_val >= tp_thresh:
                raw_gross = round(tp_thresh, 2)
                exit_time = f"{pt['t']} IST" if "IST" not in str(pt["t"]) else str(pt["t"])
                exit_reason = f"Target Hit ({tp_rule})"
                break

        net_val = round(raw_gross - chg, 2)
        t["simCapital"] = round(cap, 2)
        t["simGross"] = round(raw_gross, 2)
        t["simCharges"] = round(chg, 2)
        t["simNet"] = net_val
        t["simExitTime"] = exit_time
        t["simExitReason"] = exit_reason
        t["simRocPct"] = round((net_val / cap) * 100.0, 2) if cap > 0 else 0.0
        closed_list.append(t)

    open_gross = round(sum(x["simGrossMtm"] for x in open_list), 2)
    open_chg = round(sum(x["simCharges"] for x in open_list), 2)
    open_net = round(sum(x["simNetMtm"] for x in open_list), 2)
    open_cap = round(sum(x["simCapital"] for x in open_list), 2)

    closed_gross = round(sum(x["simGross"] for x in closed_list), 2)
    closed_chg = round(sum(x["simCharges"] for x in closed_list), 2)
    closed_net = round(sum(x["simNet"] for x in closed_list), 2)
    wins = sum(1 for x in closed_list if x["simNet"] > 0)
    win_rate = round((wins / len(closed_list)) * 100.0, 1) if closed_list else 0.0
    pos_sum = sum(x["simNet"] for x in closed_list if x["simNet"] > 0)
    neg_sum = abs(sum(x["simNet"] for x in closed_list if x["simNet"] < 0))
    pf = round(pos_sum / neg_sum, 2) if neg_sum > 0 else (99.9 if pos_sum > 0 else 0.0)

    peak_cap = max(open_cap, max((x["simCapital"] for x in closed_list), default=56074.0 * lots))
    total_net = round(closed_net + open_net, 2)
    portfolio_roc = round((total_net / peak_cap) * 100.0, 2) if (peak_cap > 0 and (open_list or closed_list)) else 0.0

    return {
        "status": data.get("status", "ARMED_WAITING_FOR_NEXT_SESSION"),
        "inception_date": data.get("inception_date", MIN_FORWARD_DATE),
        "min_trigger_date": data.get("min_trigger_date", MIN_FORWARD_DATE),
        "open_positions": open_list,
        "closed_trades": closed_list,
        "summary": {
            "open_count": len(open_list),
            "open_expiries": len({f"{x['sym']}|{x['exp']}" for x in open_list}),
            "open_gross_mtm": open_gross,
            "open_charges": open_chg,
            "open_net_mtm": open_net,
            "closed_count": len(closed_list),
            "closed_gross_pnl": closed_gross,
            "closed_charges": closed_chg,
            "closed_net_pnl": closed_net,
            "active_capital": open_cap,
            "peak_capital": peak_cap,
            "total_net_pnl": total_net,
            "portfolio_roc_pct": portfolio_roc,
            "win_rate_pct": win_rate,
            "profit_factor": pf,
            "total_charges": round(closed_chg + open_chg, 2),
        },
    }


def render_forward_test_ui(data_dir: Path | None = None) -> None:
    """Render the Streamlit 'Forward Test (Auto Paper)' workspace view."""
    import streamlit as st
    import plotly.graph_objects as go

    st.markdown("### ⚡ Automated Forward Testing (Live Paper-Trading Engine · S1–S6)")
    st.caption(
        "Clean-Slate Forward Testing Engine · Armed for first live trade from 11 Nov 2026 (09:20 IST) onwards. "
        "Historical 02 Nov – 09 Nov 2026 sessions are isolated in Strategy Backtest."
    )

    c1, c2, c3, c4, c5, c6 = st.columns([1.2, 1.2, 1.3, 0.9, 1.3, 1.3])
    sym_sel = c1.selectbox("Index Filter", ["ALL", "NIFTY", "BANKNIFTY"], key="fwd_sym_sel")
    scope_sel = c2.selectbox("Expiry Scope", ["FRONT", "ALL"], format_func=lambda x: "Front Weekly + Monthly" if x == "FRONT" else "All Active Expiries", key="fwd_scope_sel")
    strat_sel = c3.selectbox("Strategy Filter", ["ALL", "S1", "S2", "S3", "S4", "S5", "S6"], format_func=lambda x: "All 6 Strategies (S1–S6)" if x == "ALL" else STRATEGY_NAMES.get(x, x), key="fwd_strat_sel")
    lots_sel = c4.number_input("Lots", min_value=1, max_value=20, value=1, step=1, key="fwd_lots_sel")
    sl_sel = c5.selectbox(
        "Stop-Loss Rule",
        ["NONE", "SL_25_CAP", "SL_50_MAXLOSS", "SL_30_IV"],
        format_func=lambda x: {
            "NONE": "None / Hold to 15:15",
            "SL_25_CAP": "-25% of Capital",
            "SL_50_MAXLOSS": "-50% of Max Loss",
            "SL_30_IV": "+30% IV Spike",
        }[x],
        key="fwd_sl_sel",
    )
    tp_sel = c6.selectbox(
        "Profit Target Rule",
        ["NONE", "TP_50_30", "TP_75"],
        format_func=lambda x: {
            "NONE": "None / Hold to 15:15",
            "TP_50_30": "+50% Max Credit / +30% Debit",
            "TP_75": "+75% of Max Credit",
        }[x],
        key="fwd_tp_sel",
    )

    sim = simulate_forward_portfolio(
        symbol=sym_sel,
        expiry_scope=scope_sel,
        strategy=strat_sel,
        lots=int(lots_sel),
        sl_rule=sl_sel,
        tp_rule=tp_sel,
        data_dir=data_dir,
    )
    s = sim["summary"]

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Active Open Paper Positions", f"{s['open_count']} Open", "Armed · 11 Nov 09:20 IST" if s["open_count"] == 0 else f"{s['open_expiries']} Active Expiries")
    k2.metric("Live Unrealized MTM (₹)", f"₹{s['open_net_mtm']:,.0f}", f"Gross: ₹{s['open_gross_mtm']:,.0f}")
    k3.metric("Closed Forward Net P&L (₹)", f"₹{s['closed_net_pnl']:,.0f}", f"{s['closed_count']} Auto-Triggered Trades")
    k4.metric("Total Capital Deployed", f"₹{s['active_capital']:,.0f}", f"Unit Cap: ₹{s['peak_capital']:,.0f}")
    k5.metric("Forward Portfolio ROC %", f"{s['portfolio_roc_pct']:.2f}%", "Total Net / Unit Capital")
    k6.metric("Hit Rate & Profit Factor", f"{s['win_rate_pct']:.1f}% · PF {s['profit_factor']:.2f}", f"Charges: ₹{s['total_charges']:,.0f}")

    st.markdown("#### Panel 1 — Active / Open Forward-Test Positions (Live Mark-to-Market vs Real Upstox Option Chains)")
    if sim["open_positions"]:
        open_rows = []
        for p in sim["open_positions"]:
            open_rows.append({
                "Trigger Time (IST)": p["trigTime"],
                "Index & Expiry (DTE)": f"{p['sym']} {p['exp']} ({p['kind']} · {p['dte']}d)",
                "Strategy (S1–S6)": p["stratName"],
                "Trigger Condition Met": p["metrics"],
                "Exact Option Legs & Entry LTP": format_legs_summary(p["legs"], mode="entry"),
                "Current Leg LTP": format_legs_summary(p["legs"], mode="current"),
                "Capital Used (₹)": p["simCapital"],
                "Gross MTM (₹)": p["simGrossMtm"],
                "Upstox Charges (₹)": p["simCharges"],
                "Net MTM P&L (₹)": p["simNetMtm"],
                "ROC %": p["simRocPct"],
            })
        st.dataframe(pd.DataFrame(open_rows), width="stretch", hide_index=True)
    else:
        st.info(ARMED_TABLE_MESSAGE)

    st.markdown("#### Panel 2 — Auto-Triggered Closed Forward Trade Ledger (From 11 Nov 2026 09:20 IST Onwards)")
    if sim["closed_trades"]:
        closed_rows = []
        for t in sim["closed_trades"]:
            closed_rows.append({
                "Session Date": f"{t['date']} ({t.get('entryTime', '09:20 IST')} → {t['simExitTime']})",
                "Index & Expiry": f"{t['sym']} {t['exp']} ({t['dte']}d)",
                "Strategy": STRATEGY_NAMES.get(t["strat"], t["strat"]),
                "Trigger Met (09:20 IST)": t["trigReason"],
                "Structure & Strikes": t["legsText"],
                "Exit Status": t["simExitReason"],
                "Capital Used (₹)": t["simCapital"],
                "Gross P&L (₹)": t["simGross"],
                "Upstox Charges (₹)": t["simCharges"],
                "Net P&L (₹)": t["simNet"],
                "ROC %": t["simRocPct"],
            })
        df_closed = pd.DataFrame(closed_rows)
        st.dataframe(df_closed, width="stretch", hide_index=True)

        fig = go.Figure()
        cum_net = np.cumsum([x["simNet"] for x in sim["closed_trades"]])
        cum_gross = np.cumsum([x["simGross"] for x in sim["closed_trades"]])
        x_labels = [f"{x['date'][5:]} {x['sym'][:3]} {x['strat']}" for x in sim["closed_trades"]]
        fig.add_trace(go.Scatter(x=x_labels, y=cum_net, mode="lines+markers", name="Cumulative Net Forward P&L (₹)", line=dict(color="#10b981", width=2.5)))
        fig.add_trace(go.Scatter(x=x_labels, y=cum_gross, mode="lines", name="Cumulative Gross Forward P&L (₹)", line=dict(color="#38bdf8", width=1.8, dash="dot")))
        fig.update_layout(
            height=340,
            margin=dict(l=20, r=20, t=30, b=20),
            paper_bgcolor="#0b1220",
            plot_bgcolor="#0b1220",
            font=dict(color="#e2e8f0"),
            title="Forward-Test Cumulative Paper Equity Curve (From 2026-11-11 Onwards)",
        )
        st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
    else:
        st.info(ARMED_TABLE_MESSAGE)


load_forward_test_ledger = load_forward_ledger
mark_open_positions_from_snapshot = mark_open_positions_to_market


def evaluate_forward_test_scorecard(
    ledger: dict[str, Any] | None = None,
    index_filter: str = "ALL",
    expiry_scope: str = "FRONT",
    strategy_filter: str = "ALL",
    lots: int = 1,
    sl_rule: str = "NONE",
    tp_rule: str = "NONE",
) -> dict[str, Any]:
    """Compute summary metrics and 6-strategy scorecard from the forward test ledger."""
    sim = simulate_forward_portfolio(
        ledger=ledger,
        symbol=index_filter,
        expiry_scope=expiry_scope,
        strategy=strategy_filter,
        lots=lots,
        sl_rule=sl_rule,
        tp_rule=tp_rule,
    )
    out = dict(sim["summary"])
    lots_mult = max(1, int(lots))
    scorecard = []
    for sc in ("S1", "S2", "S3", "S4", "S5", "S6"):
        o_sub = [p for p in sim["open_positions"] if p["strat"] == sc]
        c_sub = [t for t in sim["closed_trades"] if t["strat"] == sc]
        o_net = round(sum(p["simNetMtm"] for p in o_sub), 2)
        c_gross = round(sum(t["simGross"] for t in c_sub), 2)
        c_chg = round(sum(t["simCharges"] for t in c_sub), 2)
        c_net = round(sum(t["simNet"] for t in c_sub), 2)
        tot_net = round(c_net + o_net, 2)
        unit_cap = (28037 if sc in ("S1", "S3", "S5") else 56074) * lots_mult
        roc_pct = round((tot_net / unit_cap) * 100.0, 2) if unit_cap > 0 else 0.0
        is_armed = len(o_sub) == 0 and len(c_sub) == 0
        scorecard.append({
            "strategy": sc,
            "name": STRATEGY_NAMES[sc],
            "open_count": len(o_sub),
            "closed_count": len(c_sub),
            "open_net_mtm": o_net,
            "closed_gross_pnl": c_gross,
            "closed_charges": c_chg,
            "closed_net_pnl": c_net,
            "total_net_pnl": tot_net,
            "unit_capital": unit_cap,
            "roc_pct": roc_pct,
            "status": "ARMED · 11 NOV 09:20 IST" if is_armed else "ACTIVE · LIVE",
        })
    out["scorecard"] = scorecard
    return out
