"""Shared helpers, contract blacklists, and dated NSE lot size schedule for Options and Futures backtests."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
BLACKLIST_PATH = ROOT / "data" / "audit" / "contract_blacklist.json"

BLACKLISTED_OPTIONS = {"NSE_FO|58561|03-10-2024", "NSE_FO|61471|23-11-2026"}
BLACKLISTED_FUTURES = {("BANKNIFTY", "2024-11-27"), ("NIFTY", "2026-11-23")}

if BLACKLIST_PATH.exists():
    try:
        _data = json.loads(BLACKLIST_PATH.read_text(encoding="utf-8"))
        BLACKLISTED_OPTIONS.update(_data.get("blacklisted_keys", []))
        for _item in _data.get("blacklisted_futures", []):
            BLACKLISTED_FUTURES.add((_item["symbol"], _item["expiry"]))
    except Exception:
        pass


def record_skip(skips, day, reason):
    skips.append({"Date": str(day), "Reason": str(reason)})


def is_blacklisted_option(symbol_or_key: str, expiry: str = "", strike: float = 0.0, option_type: str = "") -> bool:
    if str(symbol_or_key) in BLACKLISTED_OPTIONS:
        return True
    return (str(symbol_or_key), str(expiry), float(strike or 0.0), str(option_type)) in BLACKLISTED_OPTIONS


def is_blacklisted_future(symbol: str, expiry: str = "", key: str = "") -> bool:
    if str(key) in BLACKLISTED_FUTURES:
        return True
    exp_str = str(expiry)[:10] if expiry else ""
    return (str(symbol), exp_str) in BLACKLISTED_FUTURES


def dated_lot_size(symbol: str, day_or_expiry: str = "") -> int:
    """Return historical NSE index derivative lot size for NIFTY and BANKNIFTY."""
    d = str(day_or_expiry or "")[:10]
    sym = str(symbol or "NIFTY").upper()
    if sym == "BANKNIFTY":
        if d and d < "2023-07-01":
            return 25
        if d and d < "2024-11-20":
            return 15
        return 30
    if d and d < "2024-04-26":
        return 50
    if d and d < "2024-11-20":
        return 25
    return 75
