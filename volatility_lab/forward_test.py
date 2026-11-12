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
   to market against timestamped, two-sided option books, and auto-closes them at 15:15 IST or on SL/TP hit.
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

try:
    from market_calendar import HOLIDAYS as CALENDAR_HOLIDAYS
except ImportError:
    from .market_calendar import HOLIDAYS as CALENDAR_HOLIDAYS  # type: ignore

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LEDGER_FILENAME = "forward_test_ledger.json"

MIN_FORWARD_DATE = "2026-11-11"
IST_TZ = timezone(timedelta(hours=5, minutes=30))
MAX_FORWARD_QUOTE_AGE_SECONDS = 120


def _parse_aware_timestamp(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and np.isfinite(value):
        try:
            epoch = float(value)
            parsed = datetime.fromtimestamp(epoch / 1000.0 if epoch > 1e11 else epoch, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str) and value.strip():
        try:
            normalized = value.strip()
            if normalized.isdigit() and len(normalized) in (10, 13):
                epoch = int(normalized)
                parsed = datetime.fromtimestamp(epoch / 1000.0 if len(normalized) == 13 else epoch, tz=timezone.utc)
            else:
                parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(IST_TZ)


def _quote_timestamp_is_fresh(observed: datetime | str | None, reference: datetime | str | None) -> bool:
    observed_dt = _parse_aware_timestamp(observed)
    reference_dt = _parse_aware_timestamp(reference)
    if observed_dt is None or reference_dt is None:
        return False
    age_seconds = (reference_dt - observed_dt).total_seconds()
    return -30.0 <= age_seconds <= MAX_FORWARD_QUOTE_AGE_SECONDS


def _quote_event_timestamp(market_data: dict[str, Any]) -> datetime | str | None:
    """Read an instrument/feed event time; retrieval time is deliberately excluded."""
    for key in ("quote_timestamp", "feed_timestamp", "currentTs", "ts"):
        if market_data.get(key) is not None:
            return market_data[key]
    return None


def _iv_percent(value: Any) -> float | None:
    """Normalize decimal or percent IV and reject unit-ambiguous/out-of-range values."""
    try:
        iv = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not np.isfinite(iv) or iv <= 0.0 or iv > 500.0:
        return None
    if iv <= 1.0:
        iv *= 100.0
    elif iv < 5.0:
        return None
    return iv


def _risk_vol_metrics(payload: dict[str, Any], symbol: str, session_day: str) -> tuple[float | None, float | None]:
    metrics = payload.get("risk_metrics")
    item = metrics.get(symbol) if isinstance(metrics, dict) else None
    if not isinstance(item, dict):
        return None, None
    as_of = str(item.get("as_of") or "")[:10]
    try:
        age_days = (datetime.strptime(session_day, "%Y-%m-%d").date()
                    - datetime.strptime(as_of, "%Y-%m-%d").date()).days
    except ValueError:
        return None, None
    if age_days < 0 or age_days > 5:
        return None, None
    return _iv_percent(item.get("hv10")), _iv_percent(item.get("hv30"))


def _build_strike_quote_map(chain_rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    strike_map: dict[int, dict[str, Any]] = {}
    for row in chain_rows or []:
        if not isinstance(row, dict):
            continue
        try:
            k_val = int(float(row.get("strike_price") or row.get("strike") or row.get("k") or 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if k_val <= 0:
            continue
        if "call_options" in row or "put_options" in row:
            call = row.get("call_options") or {}
            put = row.get("put_options") or {}
            cm = call.get("market_data") or {} if isinstance(call, dict) else {}
            pm = put.get("market_data") or {} if isinstance(put, dict) else {}
            cg = call.get("option_greeks") or {} if isinstance(call, dict) else {}
            pg = put.get("option_greeks") or {} if isinstance(put, dict) else {}
            cm = cm if isinstance(cm, dict) else {}
            pm = pm if isinstance(pm, dict) else {}
            cg = cg if isinstance(cg, dict) else {}
            pg = pg if isinstance(pg, dict) else {}
            values = {
                "cLtp": cm.get("ltp"), "pLtp": pm.get("ltp"),
                "cBid": cm.get("bid_price"), "cAsk": cm.get("ask_price"),
                "cBidQty": cm.get("bid_qty"), "cAskQty": cm.get("ask_qty"),
                "pBid": pm.get("bid_price"), "pAsk": pm.get("ask_price"),
                "pBidQty": pm.get("bid_qty"), "pAskQty": pm.get("ask_qty"),
                "cIv": cg.get("iv"), "pIv": pg.get("iv"),
            }
            quote_timestamps = {
                "cQuoteTimestamp": _quote_event_timestamp(cm),
                "pQuoteTimestamp": _quote_event_timestamp(pm),
            }
        else:
            values = {
                "cLtp": row.get("cLtp") or row.get("call_ltp"),
                "pLtp": row.get("pLtp") or row.get("put_ltp"),
                "cBid": row.get("cBid"), "cAsk": row.get("cAsk"),
                "cBidQty": row.get("cBidQty"), "cAskQty": row.get("cAskQty"),
                "pBid": row.get("pBid"), "pAsk": row.get("pAsk"),
                "pBidQty": row.get("pBidQty"), "pAskQty": row.get("pAskQty"),
                "cIv": row.get("cIv") or row.get("iv"),
                "pIv": row.get("pIv") or row.get("iv"),
            }
            quote_timestamps = {
                "cQuoteTimestamp": row.get("cQuoteTimestamp") or row.get("c_quote_timestamp"),
                "pQuoteTimestamp": row.get("pQuoteTimestamp") or row.get("p_quote_timestamp"),
            }
        normalized: dict[str, Any] = {}
        for name, value in values.items():
            try:
                parsed = float(value or 0.0)
            except (TypeError, ValueError, OverflowError):
                parsed = 0.0
            normalized[name] = parsed if np.isfinite(parsed) else 0.0
        normalized.update(quote_timestamps)
        strike_map[k_val] = normalized
    return strike_map


def _executable_quote(
    node: dict[str, Any] | None,
    kind: str,
    side: int,
    quantity: int,
    is_exit: bool,
    *,
    reference_timestamp: datetime | str | None = None,
    require_event_timestamp: bool = False,
) -> float:
    if not node:
        return 0.0
    prefix = "c" if kind == "CE" else "p"
    if require_event_timestamp and not _quote_timestamp_is_fresh(
        node.get(prefix + "QuoteTimestamp"), reference_timestamp
    ):
        return 0.0
    bid, ask = float(node.get(prefix + "Bid", 0.0)), float(node.get(prefix + "Ask", 0.0))
    bid_qty = float(node.get(prefix + "BidQty", 0.0))
    ask_qty = float(node.get(prefix + "AskQty", 0.0))
    if not all(np.isfinite(value) for value in (bid, ask, bid_qty, ask_qty)):
        return 0.0
    if not (bid > 0.0 and ask >= bid and ask > 0.0):
        return 0.0
    is_buy = (side < 0) if is_exit else (side > 0)
    depth = ask_qty if is_buy else bid_qty
    return ask if is_buy and depth >= quantity else bid if not is_buy and depth >= quantity else 0.0

# Keep session gates aligned with the maintained NSE calendar rather than a
# partial second copy that can silently open an exchange holiday.
NSE_HOLIDAYS: set[str] = {day.isoformat() for day in CALENDAR_HOLIDAYS}

SPECIAL_SESSIONS: dict[str, dict[str, Any]] = {
    "2026-11-08": {"start_mins": 13 * 60 + 45, "end_mins": 15 * 60 + 30, "label": "MUHURAT"},
    "2027-10-29": {"start_mins": 18 * 60, "end_mins": 19 * 60 + 30, "label": "MUHURAT"},
    "2028-10-17": {"start_mins": 18 * 60, "end_mins": 19 * 60 + 30, "label": "MUHURAT"},
}

LOT_SIZES: dict[str, int] = {"NIFTY": 65, "BANKNIFTY": 30}

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
    "No eligible positions in the current ledger · Entries require validated, timestamped option books "
    "and current risk metrics after 09:20 IST."
)

ARMED_TOAST_MESSAGE = (
    "Forward-test gate is armed; entries require a valid session timestamp and fresh exchange quote timestamps."
)

CLEAN_SLATE_LEDGER: dict[str, Any] = {
    "schema": 2,
    "version": "forward-test-v20.1-event-time-gated",
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


def _forward_record_session_date(item: dict[str, Any]) -> str | None:
    """Return a forward-ledger session date only when it is parseable and in scope."""
    if not isinstance(item, dict):
        return None
    s_date = str(item.get("session_date") or item.get("date") or "").strip()[:10]
    try:
        datetime.strptime(s_date, "%Y-%m-%d")
    except ValueError:
        return None
    return s_date if s_date >= MIN_FORWARD_DATE else None


def is_valid_forward_record(item: dict[str, Any]) -> bool:
    """Accept only correctly dated, in-session forward entries from 09:20 through 15:14 IST."""
    s_date = _forward_record_session_date(item)
    if s_date is None:
        return False
    session_day = datetime.strptime(s_date, "%Y-%m-%d").date()
    trig = str(item.get("triggerTime") or item.get("trigTime") or item.get("entryTime") or "").strip()
    parsed = _parse_aware_timestamp(trig)
    if parsed is not None:
        if parsed.date() != session_day:
            return False
        trigger_minutes = parsed.hour * 60 + parsed.minute
    else:
        if re.match(r"^\d{4}-\d{2}-\d{2}T", trig):
            return False  # ISO trigger timestamps must carry an explicit timezone.
        hm = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?:\s*IST)?\b", trig, flags=re.IGNORECASE)
        if not hm:
            return False
        trigger_minutes = int(hm.group(1)) * 60 + int(hm.group(2))
        month_day = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3})\b", trig)
        if month_day:
            try:
                parsed_day = datetime.strptime(
                    f"{month_day.group(1)} {month_day.group(2)} {session_day.year}", "%d %b %Y"
                ).date()
            except ValueError:
                return False
            if parsed_day != session_day:
                return False
    if trigger_minutes < 560 or trigger_minutes >= 915:
        return False
    return True


def _retain_forward_records(records: Any, *, open_position: bool) -> list[dict[str, Any]]:
    """Retain in-scope records and make malformed provenance visible, never silent."""
    retained: list[dict[str, Any]] = []
    for item in records or []:
        if not isinstance(item, dict) or _forward_record_session_date(item) is None:
            continue
        saved = copy.deepcopy(item)
        if not is_valid_forward_record(saved):
            saved["integrity_status"] = "UNVERIFIED_RECORD"
            saved["freshness"] = "STALE"
            saved["staleReason"] = (
                "Forward record lacks a verifiable in-session trigger timestamp; "
                "automatic marking and exit are withheld."
            )
            if open_position:
                saved["manual_reconciliation_required"] = True
        retained.append(saved)
    return retained


def sanitize_forward_ledger(raw_ledger: dict[str, Any] | None) -> dict[str, Any]:
    """Preserve in-scope records while flagging, rather than dropping, bad provenance."""
    base = copy.deepcopy(CLEAN_SLATE_LEDGER)
    if not isinstance(raw_ledger, dict):
        return base
    open_pos = _retain_forward_records(raw_ledger.get("open_positions"), open_position=True)
    closed_tr = _retain_forward_records(raw_ledger.get("closed_trades"), open_position=False)
    mtm_series = [
        pt for pt in (raw_ledger.get("intraday_mtm_series") or [])
        if isinstance(pt, dict) and str(pt.get("session_date") or pt.get("date") or MIN_FORWARD_DATE)[:10] >= MIN_FORWARD_DATE
    ]
    base["updated_at"] = str(raw_ledger.get("updated_at") or base["updated_at"])
    base["open_positions"] = open_pos
    base["closed_trades"] = closed_tr
    base["intraday_mtm_series"] = mtm_series
    if any(p.get("manual_reconciliation_required") for p in open_pos):
        base["status"] = "INTEGRITY_REVIEW_REQUIRED"
    elif open_pos or closed_tr:
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
    hv10: float | None,
    hv30: float | None,
    dte: int,
    fut_price: float | None = None,
    rev25: float | None = None,
    bfly25: float | None = None,
    lots: int = 1,
    date_str: str = "2026-11-11",
    trig_time_str: str = "11 Nov 09:20 IST",
    forward_basis: float | None = None,
    quote_reference: datetime | str | None = None,
    require_event_timestamps: bool = False,
) -> list[dict[str, Any]]:
    """Evaluate all 6 quantitative strategies (S1-S6) on a real Upstox option chain."""
    sym = str(symbol).upper()
    step = 50 if sym == "NIFTY" else 100
    lot_qty = (65 if sym == "NIFTY" else 30) * max(1, int(lots))
    K = int(round(float(spot) / step) * step)

    strike_map = _build_strike_quote_map(chain_rows)

    if not np.isfinite(float(spot)) or float(spot) <= 0 or not strike_map or K not in strike_map:
        # Data integrity gate: cannot evaluate setups without valid ATM strikes
        return []

    atm_node = strike_map[K]
    c_iv = _iv_percent(atm_node.get("cIv"))
    p_iv = _iv_percent(atm_node.get("pIv"))
    if c_iv is None or p_iv is None:
        return []
    if require_event_timestamps and not all(
        _quote_timestamp_is_fresh(atm_node.get(prefix + "QuoteTimestamp"), quote_reference)
        for prefix in ("c", "p")
    ):
        return []

    iv = round(0.5 * (c_iv + p_iv), 2)
    try:
        hv10_value = float(hv10) if hv10 is not None else np.nan
    except (TypeError, ValueError, OverflowError):
        hv10_value = np.nan
    try:
        hv30_value = float(hv30) if hv30 is not None else np.nan
    except (TypeError, ValueError, OverflowError):
        hv30_value = np.nan
    hv10_valid = np.isfinite(hv10_value) and 0.0 < hv10_value <= 500.0
    hv30_valid = np.isfinite(hv30_value) and 0.0 < hv30_value <= 500.0
    vrp30 = round(iv - hv30_value, 2) if hv30_valid else np.nan
    vrp10 = round(iv - hv10_value, 2) if hv10_valid else np.nan
    try:
        basis = float(forward_basis) if forward_basis is not None else np.nan
    except (TypeError, ValueError, OverflowError):
        basis = np.nan
    if not np.isfinite(basis):
        basis = np.nan

    w_short = 200 if sym == "NIFTY" else 400
    p25_node = strike_map.get(K - w_short)
    c25_node = strike_map.get(K + w_short)
    p25_iv = _iv_percent(p25_node.get("pIv")) if p25_node else None
    c25_iv = _iv_percent(c25_node.get("cIv")) if c25_node else None
    wings_valid = p25_iv is not None and c25_iv is not None
    if require_event_timestamps:
        wings_valid = wings_valid and bool(p25_node and c25_node) and all(
            _quote_timestamp_is_fresh(node.get(prefix + "QuoteTimestamp"), quote_reference)
            for node, prefix in ((p25_node, "p"), (c25_node, "c"))
        )
    if rev25 is None:
        rev25 = round(p25_iv - c25_iv, 2) if wings_valid else np.nan
    if bfly25 is None:
        bfly25 = round(0.5 * (p25_iv + c25_iv) - iv, 2) if wings_valid else np.nan
    try:
        rev25_value = float(rev25)
    except (TypeError, ValueError, OverflowError):
        rev25_value = np.nan
    try:
        bfly25_value = float(bfly25)
    except (TypeError, ValueError, OverflowError):
        bfly25_value = np.nan

    trig_defs = [
        ("S1", hv30_valid and vrp30 >= 1.50 and hv30_value <= 16.5 and 2 <= dte <= 35,
         f"IV {iv:.2f}% · HV30 {hv30_value:.2f}% · VRP {vrp30:+.2f} pp" if hv30_valid else f"IV {iv:.2f}% · HV30 unavailable"),
        ("S2", hv10_valid and hv30_valid and (vrp30 <= -0.25 or iv < hv10_value),
         f"IV {iv:.2f}% · HV10 {hv10_value:.2f}% · IV−HV10 {vrp10:+.2f} pp" if hv10_valid and hv30_valid else "IV/HV inputs unavailable"),
        ("S3", hv30_valid and iv >= 15.2 and vrp30 >= 1.0 and dte <= 22,
         f"ATM IV {iv:.2f}% · VRP {vrp30:+.2f} pp · DTE {dte}d" if hv30_valid else f"ATM IV {iv:.2f}% · HV30 unavailable"),
        ("S4", wings_valid and np.isfinite(rev25_value) and rev25_value >= 1.50 and dte >= 5,
         f"RR25 {rev25_value:+.2f} pp · BF25 {bfly25_value:+.2f} pp" if wings_valid else "25-delta wing IV unavailable"),
        ("S5", hv10_valid and vrp10 >= 0.80 and dte <= 16,
         f"IV−HV10 {vrp10:+.2f} pp · HV10 {hv10_value:.2f}% · DTE {dte}d" if hv10_valid else "HV10 unavailable"),
        ("S6", (np.isfinite(basis) and abs(basis) >= (10.0 if sym == "NIFTY" else 35.0))
         or (wings_valid and np.isfinite(bfly25_value) and abs(bfly25_value) >= 0.25),
         f"Forward basis {basis:+.1f} pts · BF25 {bfly25_value:+.2f} pp" if np.isfinite(basis) and np.isfinite(bfly25_value)
         else (f"BF25 {bfly25_value:+.2f} pp" if wings_valid and np.isfinite(bfly25_value) else "Validated forward basis and 25-delta wing IV unavailable")),
    ]

    results = []
    for s_code, is_trig, metrics_txt in trig_defs:
        legs = build_strategy_legs(sym, K, s_code)
        has_missing_quote = False
        for l in legs:
            k_target = l["strike"]
            node = strike_map.get(k_target)
            px = _executable_quote(
                node, l["type"], int(l["side"]), lot_qty * int(l.get("mult", 1)), False,
                reference_timestamp=quote_reference,
                require_event_timestamp=require_event_timestamps,
            )
            if not px:
                has_missing_quote = True
                break
            l["entryPx"] = round(px, 2)
            l["curPx"] = round(px, 2)

        if has_missing_quote:
            # Data Integrity: NEVER substitute estimated or fabricated prices.
            # Skip setup entry entirely when real quote is unavailable.
            continue

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
    live_chain_timestamps: dict[str, datetime | str | None] | None = None,
    reference_timestamp: datetime | str | None = None,
    require_event_timestamps: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Mark positions from fresh executable books; defer automated exits when any leg is unpriced."""
    still_open: list[dict[str, Any]] = []
    newly_closed: list[dict[str, Any]] = []

    for pos in copy.deepcopy(open_positions):
        if not is_valid_forward_record(pos):
            if _forward_record_session_date(pos) is not None:
                pos["freshness"] = "STALE"
                pos["integrity_status"] = "UNVERIFIED_RECORD"
                pos["manual_reconciliation_required"] = True
                pos["staleReason"] = (
                    "Forward record lacks a verifiable in-session trigger timestamp; "
                    "automatic marking and exit are withheld."
                )
                still_open.append(pos)
            continue
        sym = str(pos.get("sym") or "").upper()
        exp = str(pos.get("exp") or "")
        if not sym or not exp:
            pos["freshness"] = "STALE"
            pos["integrity_status"] = "UNVERIFIED_RECORD"
            pos["manual_reconciliation_required"] = True
            pos["staleReason"] = "Forward record lacks a symbol or expiry; automatic marking and exit are withheld."
            still_open.append(pos)
            continue
        exit_was_pending = bool(pos.get("exit_pending"))
        lot_qty = 65 if sym == "NIFTY" else 30
        key = f"{sym}|{exp}"
        chain_rows = live_chains.get(key) or live_chains.get((sym, exp)) or []  # type: ignore
        strike_map = _build_strike_quote_map(chain_rows)
        timestamp = (live_chain_timestamps or {}).get(key)
        chain_fresh = bool(strike_map) and _quote_timestamp_is_fresh(timestamp, reference_timestamp)
        quote_reference = timestamp or reference_timestamp
        legs = pos.get("legs", [])
        all_legs_fresh = chain_fresh and bool(legs)
        exit_prices: list[float] = []
        if all_legs_fresh:
            for leg in legs:
                node = strike_map.get(int(leg["strike"]))
                qty = lot_qty * int(leg.get("mult", 1))
                px = _executable_quote(
                    node, leg["type"], int(leg["side"]), qty, True,
                    reference_timestamp=quote_reference,
                    require_event_timestamp=require_event_timestamps,
                )
                if not px:
                    all_legs_fresh = False
                    break
                exit_prices.append(px)
        if all_legs_fresh:
            for leg, px in zip(legs, exit_prices):
                leg["curPx"] = round(px, 2)

        pos["freshness"] = "FRESH" if all_legs_fresh else "STALE"
        if not all_legs_fresh:
            pos["staleReason"] = f"Missing live quote, stale quote, or non-executable book in snapshot for {key}"
            if auto_close_intraday_1515 or pos.get("exit_pending"):
                pos["exit_pending"] = True
        else:
            pos.pop("staleReason", None)
            pos["integrity_status"] = "VALIDATED_FRESH_EXECUTABLE_MARK"

        if all_legs_fresh:
            gross = 0.0
            for leg in pos.get("legs", []):
                cur_px = float(leg["curPx"])
                e_px = float(leg["entryPx"])
                qty = lot_qty * int(leg.get("mult", 1))
                gross += (e_px - cur_px) * qty if int(leg["side"]) < 0 else (cur_px - e_px) * qty
            pos["grossMtm"] = round(gross, 2)
            pos["estCharges"] = compute_position_charges(pos.get("date", MIN_FORWARD_DATE), sym, pos["legs"], lots=1, include_exit=True)
            pos["netMtm"] = round(pos["grossMtm"] - pos["estCharges"], 2)
            pos["markedAt"] = current_time_ist

        mtm_path = list(pos.get("mtmPath") or [{"t": "09:20 IST", "gross": 0.0, "iv": pos.get("entryIv", 16.0), "fresh": True}])
        mtm_path.append({"t": current_time_ist, "gross": pos.get("grossMtm", 0.0), "iv": pos.get("curIv", pos.get("entryIv", 16.0)), "fresh": all_legs_fresh})
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
        # OPERATIONAL SAFETY: Stale marks CANNOT drive automated SL/TP exits!
        if all_legs_fresh and sl_thresh is not None and pos["grossMtm"] <= sl_thresh:
            close_reason = f"Stop-Loss Hit ({sl_rule})"
        elif all_legs_fresh and tp_thresh is not None and pos["grossMtm"] >= tp_thresh:
            close_reason = f"Target Hit ({tp_rule})"
        elif all_legs_fresh and (auto_close_intraday_1515 or exit_was_pending):
            close_reason = "15:15 IST Auto-Close (delayed until fresh quote)" if exit_was_pending else "15:15 IST Auto-Close"

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
    if payload.get("status") != "complete":
        state["gate_blocked"] = True
        state["gate_message"] = "Snapshot is incomplete; no trigger or mark was processed."
        return state

    snap_ts = now_ist or payload.get("timestamp") or payload.get("as_of") or payload.get("retrieved_at")
    snap_dt = _parse_aware_timestamp(snap_ts)
    if snap_dt is None and isinstance(now_ist, datetime) and now_ist.tzinfo is None:
        snap_dt = now_ist.replace(tzinfo=IST_TZ)
    if snap_dt is None:
        state["gate_blocked"] = True
        state["gate_message"] = "Missing timezone-aware snapshot timestamp; no trigger or mark was processed."
        return state
    mkt = get_nse_market_status_ist(snap_dt)
    snapshot_day = str(payload.get("day") or payload.get("session_date") or mkt["isoDay"])[:10]

    # Strict Forward-Only Trigger Gate: never trigger on < 2026-11-11 or when market is closed
    if snapshot_day < MIN_FORWARD_DATE or snapshot_day != mkt["isoDay"] or not mkt["open"]:
        state["gate_blocked"] = True
        state["gate_message"] = (
            "Snapshot session date does not match its timestamp; no trigger or mark was processed."
            if snapshot_day != mkt["isoDay"] else ARMED_TOAST_MESSAGE
        )
        return state
    if payload.get("nfo_session_validated") is not True:
        state["gate_blocked"] = True
        state["gate_message"] = "NFO holiday/session status was not validated by the market-data collector."
        return state

    state["gate_blocked"] = False
    state["gate_message"] = f"Live Forward Session Active ({snapshot_day} · {mkt['time_label']})"

    # Extract live option chains from payload.records or payload.chains
    live_chains: dict[str, list[dict[str, Any]]] = {}
    live_chain_timestamps: dict[str, datetime | str | None] = {}
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
            live_chain_timestamps[lbl] = rec.get("retrieved_at") or rec.get("as_of") or rec.get("timestamp")
            sym_part = lbl.split("|", 1)[0]
            if sym_part not in spot_map:
                spot_map[sym_part] = float(data[0].get("underlying_spot_price") or 0.0)

    if not live_chains and isinstance(payload.get("chains"), dict):
        for k_lbl, c_rows in payload["chains"].items():
            if "|" in str(k_lbl) and isinstance(c_rows, list) and c_rows:
                live_chains[str(k_lbl)] = c_rows
                live_chain_timestamps[str(k_lbl)] = (payload.get("chain_timestamps") or {}).get(str(k_lbl))

    if not live_chains:
        if snapshot_day >= MIN_FORWARD_DATE and mkt["mins"] >= 915:
            for position in state["open_positions"]:
                position["freshness"] = "STALE"
                position["staleReason"] = "No live option-chain record at scheduled close"
                position["exit_pending"] = True
            if state["open_positions"]:
                state["status"] = "EXIT_PENDING_MISSING_QUOTES"
        state["intraday_mtm_series"] = [
            point for point in state.get("intraday_mtm_series", [])
            if not (
                str(point.get("session_date") or point.get("date") or "")[:10] == snapshot_day
                and str(point.get("t") or "") == mkt["hm"]
            )
        ]
        state["intraday_mtm_series"].append({
            "session_date": snapshot_day,
            "t": mkt["hm"],
            "time_ist": f"{mkt['hm']} IST",
            "gross_mtm": None,
            "net_mtm": None,
            "open_count": len(state["open_positions"]),
            "closed_count": len([
                trade for trade in state["closed_trades"]
                if str(trade.get("session_date") or trade.get("date") or "")[:10] == snapshot_day
            ]),
            "integrity_status": "WITHHELD_NO_LIVE_OPTION_CHAIN",
        })
        state["updated_at"] = f"{snapshot_day}T{mkt['hm']}:00+05:30"
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

    reference_time = snap_dt
    if 560 <= mkt["mins"] < 915:
        eligible_expiries: dict[str, list[str]] = {"NIFTY": [], "BANKNIFTY": []}
        for label in live_chains:
            sym_part, exp_part = label.split("|", 1)
            if sym_part in eligible_expiries and exp_part >= snapshot_day:
                eligible_expiries[sym_part].append(exp_part)
        front_expiries = {
            "NIFTY": set(sorted(set(eligible_expiries["NIFTY"]))[:2]),
            "BANKNIFTY": set(sorted(set(eligible_expiries["BANKNIFTY"]))[:1]),
        }
        for lbl, chain_rows in live_chains.items():
            sym, exp = lbl.split("|", 1)
            chain_ts = live_chain_timestamps.get(lbl)
            chain_dt = _parse_aware_timestamp(chain_ts)
            if chain_dt is None or not _quote_timestamp_is_fresh(chain_ts, reference_time):
                continue
            chain_mkt = get_nse_market_status_ist(chain_dt)
            if chain_mkt["isoDay"] != snapshot_day or not chain_mkt["open"] or not (560 <= chain_mkt["mins"] < 915):
                continue
            is_front = exp in front_expiries.get(sym, set())
            if front_scope_only and not is_front:
                continue
            spot_val = spot_map.get(sym) or float(chain_rows[0].get("underlying_spot_price") or 0.0)
            if not np.isfinite(spot_val) or spot_val <= 0:
                continue
            metrics = payload.get("risk_metrics")
            symbol_metrics = metrics.get(sym, {}) if isinstance(metrics, dict) else {}
            if not isinstance(symbol_metrics, dict):
                symbol_metrics = {}
            hv10, hv30 = _risk_vol_metrics(payload, sym, snapshot_day)
            basis_timestamp = symbol_metrics.get("forward_basis_timestamp")
            basis_expiry = str(symbol_metrics.get("forward_basis_expiry") or "")[:10]
            basis_is_fresh = (
                basis_expiry == exp
                and _quote_timestamp_is_fresh(basis_timestamp, chain_ts)
            )
            forward_basis = symbol_metrics.get("forward_basis") if basis_is_fresh else None
            try:
                expiry_dt = datetime.fromisoformat(exp + "T15:30:00+05:30")
                seconds_to_expiry = (expiry_dt - chain_dt).total_seconds()
                if seconds_to_expiry <= 0:
                    continue
                dte = int(np.ceil(seconds_to_expiry / 86400.0))
            except Exception:
                continue
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
                trig_time_str=chain_mkt["time_label"],
                forward_basis=forward_basis,
                quote_reference=chain_dt,
                require_event_timestamps=True,
            )
            for setup in eval_setups:
                s_key = f"{sym}|{exp}|{setup['strat']}"
                if setup.get("triggered") and s_key not in existing_keys:
                    setup["triggerTime"] = f"{snapshot_day}T{chain_mkt['hm']}:00+05:30"
                    setup["mtmPath"] = [{"t": chain_mkt["hm"], "gross": 0.0, "iv": setup["entryIv"]}]
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
        live_chain_timestamps=live_chain_timestamps,
        reference_timestamp=snap_dt,
        require_event_timestamps=True,
    )
    state["open_positions"] = still_open
    if newly_closed:
        state["closed_trades"] = newly_closed + state["closed_trades"]

    # 3. Record an aggregate MTM point only when every open position has a
    # fresh, executable mark.  Carrying a prior total forward would turn a
    # missing quote into a deceptively current P&L value.
    session_closed = [
        trade for trade in state["closed_trades"]
        if str(trade.get("session_date") or trade.get("date") or "")[:10] == snapshot_day
    ]
    marks_withheld = any(
        str(position.get("freshness") or "").upper() != "FRESH"
        or str(position.get("integrity_status") or "") == "UNVERIFIED_RECORD"
        or bool(position.get("manual_reconciliation_required"))
        for position in still_open
    )
    mtm_point: dict[str, Any] = {
        "session_date": snapshot_day,
        "t": mkt["hm"],
        "time_ist": f"{mkt['hm']} IST",
        "open_count": len(still_open),
        "closed_count": len(session_closed),
    }
    if marks_withheld:
        mtm_point.update({
            "gross_mtm": None,
            "net_mtm": None,
            "integrity_status": "WITHHELD_STALE_OR_UNVERIFIED_POSITION",
        })
    else:
        agg_gross = round(
            sum(float(p.get("grossMtm", 0.0)) for p in still_open)
            + sum(float(t.get("gross", 0.0)) for t in session_closed),
            2,
        )
        agg_net = round(
            sum(float(p.get("netMtm", 0.0)) for p in still_open)
            + sum(float(t.get("net", 0.0)) for t in session_closed),
            2,
        )
        mtm_point.update({
            "gross_mtm": agg_gross,
            "net_mtm": agg_net,
            "integrity_status": "VALIDATED_FRESH_EXECUTABLE_MARKS",
        })
    state["intraday_mtm_series"] = [
        point for point in state.get("intraday_mtm_series", [])
        if not (
            str(point.get("session_date") or point.get("date") or "")[:10] == snapshot_day
            and str(point.get("t") or "") == mkt["hm"]
        )
    ]
    state["intraday_mtm_series"].append(mtm_point)
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
