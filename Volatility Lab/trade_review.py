"""Read-only short-volatility diagnostics. No order routing or trade recommendations."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def relative_spread_history(data: pd.DataFrame, session_dates: list[str]) -> pd.DataFrame:
    if data.empty:
        return pd.DataFrame(columns=["Date", "RelativeSpreadPct"])
    sub = data[data["Date"].isin(session_dates)].copy()
    sub["RelativeSpreadPct"] = (sub["Ask"] - sub["Bid"]) / sub["Mid"].replace(0, np.nan) * 100.0
    return sub[["Date", "RelativeSpreadPct"]].dropna()


def archive_quotes(root: Path, symbol: str, expiry: str, payload: dict[str, Any]) -> Path:
    target_dir = Path(root) / "quotes_archive" / symbol
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{expiry}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def option_book(
    payload: dict[str, Any],
    expiry: str,
    rate: float = 0.065,
    dividend: float = 0.0,
    max_spread: float = 3.0,
) -> dict[str, Any]:
    """Validate Upstox option chain payload and populate Supplied IV without NaN Bid/Ask IV stubs."""
    if payload.get("provider") != "Upstox":
        raise ValueError("Only Upstox provider payloads are supported.")
    spot = float(payload.get("spot", 0.0))
    if not math.isfinite(spot) or spot <= 0:
        raise ValueError("Spot price must be positive and finite.")
    tte = float(payload.get("tte_days", 1.0))
    if not math.isfinite(tte) or tte <= 0:
        raise ValueError("Time to expiry must be positive.")

    rows = []
    seen: set[tuple[float, str]] = set()
    for item in payload.get("contracts", []):
        if item.get("expiry") != expiry:
            raise ValueError(f"Mixed expiry detected: {item.get('expiry')} != {expiry}")
        strike = float(item["strike"])
        kind = str(item["kind"]).upper()
        if (strike, kind) in seen:
            raise ValueError(f"Duplicate contract for strike {strike} {kind}")
        seen.add((strike, kind))
        bid = float(item["bid"])
        ask = float(item["ask"])
        mid = 0.5 * (bid + ask)
        supplied_iv = float(item["iv"]) / 100.0 if item.get("iv") is not None else np.nan
        rel_spread = ((ask - bid) / mid * 100.0) if mid > 0 else np.inf
        rows.append({
            "Strike": strike,
            "Type": kind,
            "Bid": bid,
            "Mid": mid,
            "Ask": ask,
            "BidQty": int(item.get("bid_qty", 0)),
            "AskQty": int(item.get("ask_qty", 0)),
            "Supplied IV": supplied_iv,
            "Mid IV": supplied_iv,
            "Delta": float(item.get("delta", 0.0)),
            "Gamma": float(item.get("gamma", 0.0)),
            "Theta": float(item.get("theta", 0.0)),
            "Vega": float(item.get("vega", 0.0)),
            "SpreadPct": rel_spread,
            "Liquid": rel_spread <= max_spread,
        })

    df = pd.DataFrame(rows).sort_values(["Strike", "Type"]).reset_index(drop=True)
    return {
        "spot": spot,
        "expiry": expiry,
        "tte_days": tte,
        "rate": rate,
        "dividend": dividend,
        "lot_size": int(payload.get("lot_size", 1)),
        "chain": df,
    }


def atm_pair(context: dict[str, Any]) -> tuple[pd.Series, pd.Series]:
    df = context["chain"]
    spot = context["spot"]
    strikes = sorted(df["Strike"].unique())
    atm_strike = min(strikes, key=lambda s: abs(s - spot))
    ce = df[(df["Strike"] == atm_strike) & (df["Type"] == "CE")].iloc[0]
    pe = df[(df["Strike"] == atm_strike) & (df["Type"] == "PE")].iloc[0]
    return ce, pe


def freshness(context: dict[str, Any], now: datetime, spot_quote: datetime | None = None) -> dict[str, Any]:
    asof = context.get("asof", now)
    age_sec = max(0.0, (now - asof).total_seconds())
    spot_age = max(0.0, (now - spot_quote).total_seconds()) if spot_quote else 0.0
    return {"chain_age_seconds": age_sec, "spot_age_seconds": spot_age, "fresh": age_sec <= 60.0 and spot_age <= 60.0}


def historical_volatility_context(
    daily_moves: pd.Series,
    intraday_moves: pd.Series,
    gap_moves: pd.Series,
    window: int = 30,
) -> dict[str, float]:
    return {
        "rv_daily": float(daily_moves.tail(window).std(ddof=1) * np.sqrt(252)),
        "rv_intraday": float(intraday_moves.tail(window).std(ddof=1) * np.sqrt(252)),
        "rv_gap": float(gap_moves.tail(window).std(ddof=1) * np.sqrt(252)),
    }


def make_position(
    context: dict[str, Any],
    put_wing: float | None = None,
    call_wing: float | None = None,
) -> pd.DataFrame:
    """Construct short ATM straddle with optional symmetric or asymmetric protective wings."""
    ce, pe = atm_pair(context)
    df = context["chain"]
    legs = [
        {**ce.to_dict(), "Position": -1, "ExecPrice": float(ce["Bid"])},
        {**pe.to_dict(), "Position": -1, "ExecPrice": float(pe["Bid"])},
    ]
    if put_wing is not None:
        pw_rows = df[(df["Strike"] == float(put_wing)) & (df["Type"] == "PE")]
        if pw_rows.empty:
            raise ValueError(f"Put wing strike {put_wing} not found in option chain.")
        pw = pw_rows.iloc[0]
        legs.append({**pw.to_dict(), "Position": 1, "ExecPrice": float(pw["Ask"])})
    if call_wing is not None:
        cw_rows = df[(df["Strike"] == float(call_wing)) & (df["Type"] == "CE")]
        if cw_rows.empty:
            raise ValueError(f"Call wing strike {call_wing} not found in option chain.")
        cw = cw_rows.iloc[0]
        legs.append({**cw.to_dict(), "Position": 1, "ExecPrice": float(cw["Ask"])})
    return pd.DataFrame(legs)


def position_greeks(context: dict[str, Any], position: pd.DataFrame) -> dict[str, float]:
    lot = float(context.get("lot_size", 1))
    return {
        greek: float((position["Position"] * position[greek]).sum() * lot)
        for greek in ("Delta", "Gamma", "Theta", "Vega")
    }


def stress_position(
    context: dict[str, Any],
    position: pd.DataFrame,
    elapsed_days: float = 1.0,
    costs: float = 0.0,
) -> dict[str, Any]:
    """Compute spot/IV shock grid and aggregate multi-wing payoff across all long/short legs."""
    spot = float(context["spot"])
    lot = float(context.get("lot_size", 1))
    spot_shocks = (-0.05, -0.025, 0.0, 0.025, 0.05)
    iv_shocks = (0.0, 0.05, 0.10)
    grid = []
    for ds in spot_shocks:
        d_spot = spot * ds
        for dv in iv_shocks:
            pnl = 0.0
            for _, row in position.iterrows():
                pos = float(row["Position"]) * lot
                leg_pnl = (
                    row["Delta"] * d_spot
                    + 0.5 * row["Gamma"] * (d_spot ** 2)
                    + row["Vega"] * (dv * 100.0)
                    + row["Theta"] * elapsed_days
                )
                pnl += pos * leg_pnl
            grid.append({
                "SpotShockPct": ds * 100.0,
                "IVShockPts": dv * 100.0,
                "UnhedgedPnL": round(pnl - costs, 2),
            })

    # Exact expiration payoff scan across strikes and tail bounds to support asymmetric or multi-wing structures
    candidate_spots = [0.5 * spot, 1.5 * spot] + [ float(k) for k in position["Strike"].unique() ]
    worst_expiry_pnl = math.inf
    for s_exp in candidate_spots:
        exp_pnl = 0.0
        for _, row in position.iterrows():
            intrinsic = max(s_exp - row["Strike"], 0.0) if row["Type"] == "CE" else max(row["Strike"] - s_exp, 0.0)
            exp_pnl += float(row["Position"]) * (intrinsic - float(row["ExecPrice"])) * lot
        worst_expiry_pnl = min(worst_expiry_pnl, exp_pnl - costs)

    net_calls = (position.loc[position["Type"] == "CE", "Position"]).sum()
    net_puts = (position.loc[position["Type"] == "PE", "Position"]).sum()
    max_loss = round(max(0.0, -worst_expiry_pnl), 2) if (net_calls >= 0 and net_puts >= 0) else None
    return {"grid": pd.DataFrame(grid), "max_defined_loss": max_loss}


def parity_forward(context: dict[str, Any]) -> dict[str, Any] | None:
    """Near-ATM put/call parity consistency, using synchronized quote pairs."""
    book = context.get('book')
    spot = context.get('spot')
    if book is None or spot is None or getattr(book, 'empty', True):
        return None
    values = []
    for strike, group in book.groupby('Strike'):
        if abs(strike / spot - 1.0) > 0.03 or len(group) != 2 or set(group['Kind']) != {'CE', 'PE'}:
            continue
        ages = group['Quote age · sec'].to_numpy(float) if 'Quote age · sec' in group.columns else np.array([0.0, 0.0])
        if not np.isfinite(ages).all() or (np.max(ages) - np.min(ages)) > 30.0:
            continue
        sides = group.set_index('Kind')
        values.append(strike + np.exp(context['rate'] * context['years']) * (sides.loc['CE', 'Mid'] - sides.loc['PE', 'Mid']))
    if len(values) < 3:
        return None
    forward = float(np.median(values))
    assumed = spot * np.exp((context['rate'] - context['dividend']) * context['years'])
    return {
        'Paired strikes': len(values),
        'Parity forward': forward,
        'Assumed-carry forward': assumed,
        'Difference · points': forward - assumed,
        'Pair range · points': float(np.ptp(values)),
    }


def gap_summary(frame: pd.DataFrame, as_of: Any) -> pd.DataFrame:
    from vol_lib import validate_ohlc
    from quote_quality import timestamp
    stamp = timestamp(as_of)
    data = validate_ohlc(frame)
    cutoff = stamp.normalize().tz_localize(None)
    data = data[data.index < cutoff]
    start = cutoff - pd.DateOffset(years=1)
    gap = (data['Open'] / data['Close'].shift() - 1.0) * 100.0
    daily = (data['Close'] / data['Close'].shift() - 1.0) * 100.0
    intraday = (data['High'] - data['Low']) / data['Open'] * 100.0
    records = []
    for label, series in [('Overnight gap', gap), ('Close-to-close move', daily), ('Intraday range', intraday)]:
        series = series[series.index >= start].dropna()
        if len(series) < 30:
            raise ValueError('At least 30 completed observations are required for gap review.')
        records.append({
            'Measure': label,
            'Observations': len(series),
            'Median absolute %': series.abs().median(),
            '95th absolute %': series.abs().quantile(0.95),
            'Worst absolute %': series.abs().max(),
            'Worst date': str(series.abs().idxmax().date()),
            'Through': str(series.index.max().date()),
        })
    return pd.DataFrame(records)
