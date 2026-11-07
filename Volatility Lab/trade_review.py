"""Read-only short-volatility diagnostics. No order routing or trade recommendations."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def relative_spread_history(data: pd.DataFrame, session_dates: list[str] | None = None) -> pd.DataFrame:
    if data is None or data.empty:
        return pd.DataFrame(columns=["Date", "RelativeSpreadPct"])
    sub = data[data["Date"].isin(session_dates)].copy() if session_dates is not None and "Date" in data.columns else data.copy()
    if "Ask" in sub.columns and "Bid" in sub.columns:
        mid = sub["Mid"] if "Mid" in sub.columns else 0.5 * (sub["Bid"] + sub["Ask"])
        sub["RelativeSpreadPct"] = (sub["Ask"] - sub["Bid"]) / mid.replace(0, np.nan) * 100.0
    elif "RelativeSpreadPct" not in sub.columns:
        sub["RelativeSpreadPct"] = np.nan
    return sub[["Date", "RelativeSpreadPct"]].dropna() if "Date" in sub.columns else sub


def archive_quotes(root: Path, symbol: str, expiry: str, payload: dict[str, Any], config: str | None = None) -> Path:
    """Persist option quote book snapshot in both root/<symbol>_option_quotes_<expiry>.json and quotes_archive/."""
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    out_obj = dict(payload) if isinstance(payload, dict) else {"records": payload}
    if config is not None and "config" not in out_obj:
        out_obj["config"] = config
    direct_path = root_path / f"{str(symbol).lower()}_option_quotes_{expiry}.json"
    txt = json.dumps(out_obj, indent=2, sort_keys=True, default=str)
    direct_path.write_text(txt, encoding="utf-8")
    target_dir = root_path / "quotes_archive" / str(symbol)
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / f"{expiry}.json").write_text(txt, encoding="utf-8")
    return direct_path


def option_book(
    payload: dict[str, Any],
    expiry: str,
    rate: float = 0.065,
    dividend: float = 0.0,
    max_spread: float = 3.0,
) -> dict[str, Any]:
    """Validate Upstox option chain payload and populate Supplied IV with full legacy ('book'/'Kind') & modern ('chain'/'Type') keys."""
    if payload.get("provider", "Upstox") != "Upstox":
        raise ValueError("Only Upstox provider payloads are supported.")
    spot = float(payload.get("spot") or payload.get("underlying_spot") or 0.0)
    if not math.isfinite(spot) or spot <= 0:
        raise ValueError("Spot price must be positive and finite.")
    tte = float(payload.get("tte_days") or payload.get("dte") or 1.0)
    if not math.isfinite(tte) or tte <= 0:
        tte = 1.0

    rows = []
    seen: set[tuple[float, str]] = set()
    contracts = payload.get("contracts") or payload.get("quotes") or []
    for item in contracts:
        item_exp = str(item.get("expiry") or expiry)[:10]
        if item_exp != str(expiry)[:10]:
            raise ValueError(f"Mixed expiry detected: {item_exp} != {expiry}")
        strike = float(item.get("strike") or item.get("Strike") or 0.0)
        kind = str(item.get("kind") or item.get("Type") or item.get("Kind") or "CE").upper()
        if (strike, kind) in seen:
            raise ValueError(f"Duplicate contract for strike {strike} {kind}")
        seen.add((strike, kind))
        bid = float(item.get("bid") if item.get("bid") is not None else item.get("Bid", 0.0))
        ask = float(item.get("ask") if item.get("ask") is not None else item.get("Ask", 0.0))
        mid = 0.5 * (bid + ask)
        raw_iv = item.get("iv") if item.get("iv") is not None else item.get("Supplied IV")
        supplied_iv = (float(raw_iv) / 100.0 if float(raw_iv) > 1.5 else float(raw_iv)) if raw_iv is not None else np.nan
        rel_spread = ((ask - bid) / mid * 100.0) if mid > 0 else np.inf
        rows.append({
            "Strike": strike,
            "Kind": kind,
            "Type": kind,
            "Bid": bid,
            "Mid": mid,
            "Ask": ask,
            "BidQty": int(item.get("bid_qty", 0)),
            "AskQty": int(item.get("ask_qty", 0)),
            "Supplied IV": supplied_iv,
            "Mid IV": supplied_iv,
            "Delta": float(item.get("delta", item.get("Delta", 0.0))),
            "Gamma": float(item.get("gamma", item.get("Gamma", 0.0))),
            "Theta": float(item.get("theta", item.get("Theta", 0.0))),
            "Vega": float(item.get("vega", item.get("Vega", 0.0))),
            "Spread %": rel_spread,
            "SpreadPct": rel_spread,
            "Liquid": rel_spread <= max_spread,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["Strike", "Type"]).reset_index(drop=True)
        strikes = sorted(df["Strike"].unique())
        atm_strike = float(min(strikes, key=lambda k_val: abs(k_val - spot)))
    else:
        atm_strike = float(spot)

    stamp = payload.get("asof") or payload.get("stamp") or payload.get("retrieved_at") or datetime.now(timezone.utc).isoformat()
    return {
        "spot": spot,
        "strike": atm_strike,
        "stamp": stamp,
        "asof": stamp,
        "expiry": expiry,
        "tte_days": tte,
        "years": max(tte / 365.0, 1e-6),
        "rate": rate,
        "dividend": dividend,
        "lot_size": int(payload.get("lot_size", 1)),
        "chain": df,
        "book": df,
    }


def atm_pair(context: dict[str, Any]) -> tuple[pd.Series, pd.Series]:
    df = context.get("chain") if "chain" in context else context.get("book", pd.DataFrame())
    spot = float(context["spot"])
    type_col = "Type" if "Type" in df.columns else "Kind"
    strikes = sorted(df["Strike"].unique())
    atm_strike = min(strikes, key=lambda k_val: abs(k_val - spot))
    ce = df[(df["Strike"] == atm_strike) & (df[type_col] == "CE")].iloc[0]
    pe = df[(df["Strike"] == atm_strike) & (df[type_col] == "PE")].iloc[0]
    return ce, pe


def parity_forward(context: dict[str, Any]) -> dict[str, float]:
    """Compute Put-Call Parity implied forward and basis at the ATM strike."""
    ce, pe = atm_pair(context)
    strike = float(ce["Strike"])
    spot = float(context["spot"])
    rate = float(context.get("rate") or 0.0)
    years = float(context.get("years") or max(float(context.get("tte_days", 1.0)) / 365.0, 1e-6))
    call_mid = float(ce["Mid"])
    put_mid = float(pe["Mid"])
    forward = strike + math.exp(rate * years) * (call_mid - put_mid)
    return {
        "strike": strike,
        "spot": spot,
        "forward": forward,
        "basis": forward - spot,
        "call_mid": call_mid,
        "put_mid": put_mid,
    }


class _FreshnessResult(str):
    """String subclass that also supports dict-style ['fresh'] / .get() access for unit tests and UI captions."""
    def __new__(cls, text: str, meta: dict[str, Any]):
        obj = super().__new__(cls, text)
        obj._meta = meta
        return obj

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._meta[key]
        return super().__getitem__(key)

    def get(self, key, default=None):
        return self._meta.get(key, default)


def freshness(context: dict[str, Any], now: datetime | None = None, spot_quote: Any = None) -> _FreshnessResult:
    current = now or datetime.now(timezone.utc)
    asof = context.get("asof") or context.get("stamp") or current
    if isinstance(asof, str):
        try:
            asof = datetime.fromisoformat(asof.replace("Z", "+00:00"))
        except Exception:
            asof = current
    if isinstance(asof, datetime) and isinstance(current, datetime):
        if asof.tzinfo is None and current.tzinfo is not None:
            asof = asof.replace(tzinfo=current.tzinfo)
        elif asof.tzinfo is not None and current.tzinfo is None:
            current = current.replace(tzinfo=asof.tzinfo)
        age_sec = max(0.0, (current - asof).total_seconds())
    else:
        age_sec = 0.0
    spot_age = 0.0
    if isinstance(spot_quote, datetime) and isinstance(current, datetime):
        spot_age = max(0.0, (current - spot_quote).total_seconds())
    is_fresh = age_sec <= 60.0 and spot_age <= 60.0
    meta = {"chain_age_seconds": age_sec, "spot_age_seconds": spot_age, "fresh": is_fresh}
    label = f"{'Fresh' if is_fresh else 'Stale'} ({int(age_sec)}s old)"
    return _FreshnessResult(label, meta)


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
    df = context.get("chain") if "chain" in context else context.get("book", pd.DataFrame())
    type_col = "Type" if "Type" in df.columns else "Kind"
    legs = [
        {**ce.to_dict(), "Kind": "CE", "Type": "CE", "Position": -1, "ExecPrice": float(ce["Bid"])},
        {**pe.to_dict(), "Kind": "PE", "Type": "PE", "Position": -1, "ExecPrice": float(pe["Bid"])},
    ]
    if put_wing is not None:
        pw_rows = df[(df["Strike"] == float(put_wing)) & (df[type_col] == "PE")]
        if pw_rows.empty:
            raise ValueError(f"Put wing strike {put_wing} not found in option chain.")
        pw = pw_rows.iloc[0]
        legs.append({**pw.to_dict(), "Kind": "PE", "Type": "PE", "Position": 1, "ExecPrice": float(pw["Ask"])})
    if call_wing is not None:
        cw_rows = df[(df["Strike"] == float(call_wing)) & (df[type_col] == "CE")]
        if cw_rows.empty:
            raise ValueError(f"Call wing strike {call_wing} not found in option chain.")
        cw = cw_rows.iloc[0]
        legs.append({**cw.to_dict(), "Kind": "CE", "Type": "CE", "Position": 1, "ExecPrice": float(cw["Ask"])})
    return pd.DataFrame(legs)


def position_greeks(context: dict[str, Any], position: pd.DataFrame) -> dict[str, float]:
    lot = float(context.get("lot_size", 1))
    return {
        greek: float((position["Position"] * position[greek]).sum() * lot)
        for greek in ("Delta", "Gamma", "Theta", "Vega")
    }


def gap_summary(frame: pd.DataFrame, as_of: Any) -> pd.DataFrame:
    """Historical overnight gap distributions for underlying spot."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["Statistic", "Value"])
    moves = frame["Close"].pct_change().dropna() if "Close" in frame.columns else pd.Series(dtype=float)
    if moves.empty:
        return pd.DataFrame(columns=["Statistic", "Value"])
    return pd.DataFrame([
        {"Statistic": "Mean Overnight Move", "Value": f"{moves.mean() * 100:.2f}%"},
        {"Statistic": "Median Overnight Move", "Value": f"{moves.median() * 100:.2f}%"},
        {"Statistic": "Max Overnight Gap", "Value": f"{moves.max() * 100:.2f}%"},
        {"Statistic": "Min Overnight Gap", "Value": f"{moves.min() * 100:.2f}%"},
        {"Statistic": "Standard Deviation", "Value": f"{moves.std() * 100:.2f}%"},
    ])


class _StressResult(dict):
    """Dict that also supports 2-tuple unpacking `scenarios, summary = stress_position(...)` for UI compatibility."""
    def __init__(self, grid_df: pd.DataFrame, max_loss: float | None):
        summary = {"Max defined loss · ₹": max_loss, "max_defined_loss": max_loss}
        super().__init__(grid=grid_df, max_defined_loss=max_loss, summary=summary)
        self._tuple = (grid_df, summary)

    def __iter__(self):
        return iter(self._tuple)


def stress_position(
    context: dict[str, Any],
    position: pd.DataFrame,
    elapsed_days: float = 1.0,
    costs: float = 0.0,
) -> _StressResult:
    """Compute spot/IV shock grid and aggregate multi-wing payoff across all long/short legs."""
    spot = float(context["spot"])
    lot = float(context.get("lot_size", 1))
    type_col = "Type" if "Type" in position.columns else "Kind"
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
            val = round(pnl - costs, 2)
            grid.append({
                "Spot move %": ds * 100.0,
                "IV shock · pp": dv * 100.0,
                "Unhedged P&L · ₹": val,
                "SpotShockPct": ds * 100.0,
                "IVShockPts": dv * 100.0,
                "UnhedgedPnL": val,
            })

    candidate_spots = [0.5 * spot, 1.5 * spot] + [float(k_val) for k_val in position["Strike"].unique()]
    worst_expiry_pnl = math.inf
    for s_exp in candidate_spots:
        exp_pnl = 0.0
        for _, row in position.iterrows():
            intrinsic = max(s_exp - row["Strike"], 0.0) if row[type_col] == "CE" else max(row["Strike"] - s_exp, 0.0)
            exp_pnl += float(row["Position"]) * (intrinsic - float(row["ExecPrice"])) * lot
        worst_expiry_pnl = min(worst_expiry_pnl, exp_pnl - costs)

    net_calls = (position.loc[position[type_col] == "CE", "Position"]).sum()
    net_puts = (position.loc[position[type_col] == "PE", "Position"]).sum()
    max_loss = round(max(0.0, -worst_expiry_pnl), 2) if (net_calls >= 0 and net_puts >= 0) else None
    return _StressResult(pd.DataFrame(grid), max_loss)
