"""Independently tie newly recorded paper prices to the archived option books."""

from math import isfinite

from volatility_lab.backtest.forward_test import (
    LOT_SIZES, _parse_aware_timestamp, _quote_timestamp_is_fresh, compute_position_charges,
)


class ExecutionEvidenceError(RuntimeError):
    """Publication must stop if a paper execution cannot be reproduced from the archive."""


def _number(value):
    if isinstance(value, bool):
        raise ValueError("Invalid book number")
    parsed = float(value)
    if not isfinite(parsed):
        raise ValueError("Invalid book number")
    return parsed


def _book(row, kind):
    if kind not in ("CE", "PE"):
        raise ExecutionEvidenceError("Unknown option type in paper execution")
    if "call_options" in row or "put_options" in row:
        market = row["call_options" if kind == "CE" else "put_options"]["market_data"]
        fields = ("bid_price", "ask_price", "bid_qty", "ask_qty")
        stamps = ("quote_timestamp", "feed_timestamp", "currentTs", "ts")
    else:
        prefix = "c" if kind == "CE" else "p"
        market = row
        fields = tuple(prefix + suffix for suffix in ("Bid", "Ask", "BidQty", "AskQty"))
        stamps = (prefix + "QuoteTimestamp", prefix + "_quote_timestamp")
    quote_at = next((market[key] for key in stamps if market.get(key) is not None), None)
    return *(_number(market[key]) for key in fields), _parse_aware_timestamp(quote_at)


def verify_execution_evidence(report, trade, endpoint):
    """Verify a new entry or exit directly against its underlying, expiry, strike and option side."""
    if endpoint not in ("entry", "exit"):
        raise ValueError("Unknown execution endpoint")
    try:
        available = _parse_aware_timestamp(report.get("available_at") or report["retrieved_at"])
        observed = _parse_aware_timestamp(trade["triggerTime" if endpoint == "entry" else "exit_at"])
        symbol, expiry = trade["sym"], trade["exp"]
        records = [r for r in report["records"] if r["label"] == f"{symbol}|{expiry}"]
        if available is None or observed != available or len(records) != 1:
            raise ExecutionEvidenceError("Paper execution does not match the captured contract and time")
        chain = records[0]
        chain_at = _parse_aware_timestamp(chain["retrieved_at"])
        if not _quote_timestamp_is_fresh(chain_at, observed):
            raise ExecutionEvidenceError("Paper execution chain was unavailable or stale at the recorded endpoint")
        lots = _number(trade["lots"])
        if not lots.is_integer() or lots <= 0 or not trade["legs"]:
            raise ExecutionEvidenceError("Paper execution lacks a valid lot count or legs")
        gross = 0.0
        for leg in trade["legs"]:
            matches = [row for row in chain["data"] if row.get("expiry") == expiry
                       and _number(row["strike_price"]) == _number(leg["strike"])]
            if len(matches) != 1:
                raise ExecutionEvidenceError("Paper execution has no unique matching option strike")
            bid, ask, bid_qty, ask_qty, stamp = _book(matches[0], leg["type"])
            side, mult = _number(leg["side"]), _number(leg.get("mult", 1))
            if side not in (-1, 1) or mult <= 0 or not mult.is_integer():
                raise ExecutionEvidenceError("Paper execution has invalid contract quantities")
            if bid <= 0 or ask < bid or min(bid_qty, ask_qty) <= 0:
                raise ExecutionEvidenceError("Paper execution lacks an uncrossed two-sided archive book")
            price, size = (ask, ask_qty) if (side == 1) == (endpoint == "entry") else (bid, bid_qty)
            quantity = int(lots * LOT_SIZES[symbol] * mult)
            if size < quantity:
                raise ExecutionEvidenceError("Insufficient displayed quantity for recorded paper execution")
            if round(price, 2) != _number(leg[endpoint + "Px"]):
                raise ExecutionEvidenceError("Paper execution price differs from the archived side of book")
            if (not _quote_timestamp_is_fresh(stamp, chain_at)
                    or _parse_aware_timestamp(leg[endpoint + "QuoteAt"]) != stamp):
                raise ExecutionEvidenceError("Paper execution quote time differs from its fresh archive book")
            if endpoint == "exit":
                gross += side * quantity * (round(price, 2) - _number(leg["entryPx"]))
        if endpoint == "exit":
            entry = _parse_aware_timestamp(trade["triggerTime"])
            if (entry is None or entry > observed or trade["session_date"] != entry.date().isoformat()
                    or trade["exit_session_date"] != observed.date().isoformat()):
                raise ExecutionEvidenceError("Paper close has inconsistent entry or exit dates")
            fees = compute_position_charges(trade["session_date"], symbol, trade["legs"], lots=int(lots),
                                            include_exit=True, exit_day=trade["exit_session_date"])
            gross = round(gross, 2)
            if any(_number(trade[key]) != value for key, value in (
                ("gross", gross), ("charges", fees), ("net", round(gross - fees, 2))
            )):
                raise ExecutionEvidenceError("Paper close amounts differ from book prices, quantities or dated fees")
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
        raise ExecutionEvidenceError("Incomplete archive evidence for paper execution") from exc
