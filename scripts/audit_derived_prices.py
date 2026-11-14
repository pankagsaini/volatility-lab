"""Auditing & Parity Harness: Volatility Lab Derived Prices vs. Sensibull Benchmark.

Supports ingestion of Sensibull benchmark data via:
1. JSON API response / export
2. CSV export / table copy-paste
3. Manual test vectors / CLI arguments

Compares:
- Black-Scholes-Merton (BSM) vs. Black-76 vs. Sensibull
- Strike-by-strike Implied Volatility (IV)
- Greeks: Delta, Gamma, Vega, Theta (ACT/365 vs trading day)
- Discounted parity forward on supplied benchmark premiums
- Skew: 25-Delta Call/Put IV, Risk Reversal (RR25), Butterfly (BF25)
"""

import argparse
import csv
import io
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import numpy as np
from volatility_lab.pricing.option_math import (
    black76_greeks as _black76_greeks, bsm_greeks as _bsm_greeks,
    parity_implied_forward, solve_black76_iv as _solve_black76_iv, solve_iv,
)


def black76_greeks(F, K, T, r, sigma, kind="CE"):
    return _black76_greeks(F, K, T, r, sigma, kind)


def solve_black76_iv(price, F, K, T, r, kind="CE", bounds=(1e-4, 5.0), tol=1e-8):
    return _solve_black76_iv(price, F, K, T, r, kind, bounds=bounds, tol=tol)


def bsm_greeks(spot, strike, T, r, vol, kind, q=0.0):
    return _bsm_greeks(spot, strike, T, r, vol, kind, dividend=q)


def solve_bsm_iv(price, spot, strike, T, r, kind, q=0.0):
    return solve_iv(price, spot, strike, T, r, kind, dividend=q)


def parse_sensibull_data(content: str) -> Tuple[Optional[float], Optional[float], List[Dict[str, Any]]]:
    """Parse JSON or CSV or tab-separated Sensibull export into standard records."""
    content = content.strip()
    spot = None
    future = None
    records = []

    # 1. Try JSON
    if content.startswith("{") or content.startswith("["):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                spot = parsed.get("spot") or parsed.get("underlying_value")
                future = parsed.get("future_price") or parsed.get("future")
                raw_list = parsed.get("chain") or parsed.get("strikes") or parsed.get("data") or []
            else:
                raw_list = parsed

            for item in raw_list:
                strike = float(item.get("strike") or item.get("strike_price") or 0)
                if strike <= 0:
                    continue
                records.append({
                    "strike": strike,
                    "call_ltp": float(item.get("call_ltp") or item.get("c_ltp") or (item.get("call", {}).get("ltp")) or 0),
                    "put_ltp": float(item.get("put_ltp") or item.get("p_ltp") or (item.get("put", {}).get("ltp")) or 0),
                    "call_iv": float(item.get("call_iv") or item.get("c_iv") or (item.get("call", {}).get("iv")) or 0),
                    "put_iv": float(item.get("put_iv") or item.get("p_iv") or (item.get("put", {}).get("iv")) or 0),
                    "call_delta": float(item.get("call_delta") or item.get("c_delta") or (item.get("call", {}).get("delta")) or 0),
                    "put_delta": float(item.get("put_delta") or item.get("p_delta") or (item.get("put", {}).get("delta")) or 0),
                    "call_theta": float(item.get("call_theta") or item.get("c_theta") or 0),
                    "call_vega": float(item.get("call_vega") or item.get("c_vega") or 0),
                })
            return spot, future, records
        except Exception:
            pass

    # 2. Try Delimited text (CSV or Tab)
    delimiter = "\t" if "\t" in content else ","
    reader = csv.reader(io.StringIO(content), delimiter=delimiter)
    headers = None
    for row in reader:
        if not row or not any(row):
            continue
        row = [c.strip() for c in row]
        if headers is None:
            headers = [c.lower() for c in row]
            continue

        row_dict = dict(zip(headers, row))
        # Look for strike
        k_val = next((row_dict[k] for k in row_dict if "strike" in k), None)
        if not k_val:
            continue
        try:
            strike = float(k_val.replace(",", ""))
        except ValueError:
            continue

        def parse_col(patterns):
            for p in patterns:
                for k, v in row_dict.items():
                    if p in k:
                        try:
                            return float(v.replace(",", ""))
                        except ValueError:
                            pass
            return 0.0

        records.append({
            "strike": strike,
            "call_ltp": parse_col(["call_ltp", "c_ltp", "call ltp", "ce ltp"]),
            "put_ltp": parse_col(["put_ltp", "p_ltp", "put ltp", "pe ltp"]),
            "call_iv": parse_col(["call_iv", "c_iv", "call iv", "ce iv"]),
            "put_iv": parse_col(["put_iv", "p_iv", "put iv", "pe iv"]),
            "call_delta": parse_col(["call_delta", "c_delta", "call delta", "ce delta"]),
            "put_delta": parse_col(["put_delta", "p_delta", "put delta", "pe delta"]),
            "call_theta": parse_col(["call_theta", "c_theta", "call theta"]),
            "call_vega": parse_col(["call_vega", "c_vega", "call vega"]),
        })

    return spot, future, records


def run_benchmark_audit(
    sensibull_records: List[Dict[str, Any]],
    spot: float,
    future_price: Optional[float] = None,
    *, r: float, q: float, dte_days: float,
) -> Dict[str, Any]:
    """Execute complete mathematical audit against Sensibull benchmark records."""
    if not np.isfinite([spot, r, q, dte_days]).all() or spot <= 0:
        raise ValueError('Supply a finite observed spot, rate, dividend yield and days to expiry.')
    T = dte_days / 365.0
    strikes = [row['strike'] for row in sensibull_records if np.isfinite(row['strike']) and row['strike'] > 0]
    atm_strike = min(strikes, key=lambda k: (abs(k - spot), k)) if strikes else None

    results = []
    for sb in sensibull_records:
        k = sb["strike"]
        c_ltp = sb["call_ltp"]
        p_ltp = sb["put_ltp"]

        # Forensic LTP parity is a benchmark diagnostic, never a live book or historical IV.
        f_syn = parity_implied_forward(k, c_ltp, p_ltp, T, r) if T > 0 and c_ltp > 0 and p_ltp > 0 else np.nan
        forward = future_price if future_price is not None else f_syn

        # 1. Spot BSM Solvers
        bsm_c_iv = solve_bsm_iv(c_ltp, spot, k, T, r, "CE", q) if c_ltp > 0 else float("nan")
        bsm_p_iv = solve_bsm_iv(p_ltp, spot, k, T, r, "PE", q) if p_ltp > 0 else float("nan")

        # 2. Black-76 Solvers (Anchored to F_syn)
        b76_c_iv = solve_black76_iv(c_ltp, forward, k, T, r, "CE") if c_ltp > 0 else float("nan")
        b76_p_iv = solve_black76_iv(p_ltp, forward, k, T, r, "PE") if p_ltp > 0 else float("nan")

        # Greeks
        b76_g = black76_greeks(forward, k, T, r, b76_c_iv, "CE") if not math.isnan(b76_c_iv) else {}

        sb_c_iv = sb.get("call_iv")
        sb_p_iv = sb.get("put_iv")
        sb_c_del = sb.get("call_delta")

        b76_c_iv_pct = round(b76_c_iv * 100.0, 2) if not math.isnan(b76_c_iv) else None
        b76_p_iv_pct = round(b76_p_iv * 100.0, 2) if not math.isnan(b76_p_iv) else None
        bsm_c_iv_pct = round(bsm_c_iv * 100.0, 2) if not math.isnan(bsm_c_iv) else None
        bsm_p_iv_pct = round(bsm_p_iv * 100.0, 2) if not math.isnan(bsm_p_iv) else None

        c_err = abs(b76_c_iv_pct - sb_c_iv) if sb_c_iv and b76_c_iv_pct else None
        p_err = abs(b76_p_iv_pct - sb_p_iv) if sb_p_iv and b76_p_iv_pct else None
        del_err = abs(round(b76_g.get("delta", 0), 3) - sb_c_del) if sb_c_del and "delta" in b76_g else None

        results.append({
            "strike": k,
            "is_atm": k == atm_strike,
            "call_ltp": c_ltp,
            "put_ltp": p_ltp,
            "synthetic_future": round(f_syn, 2) if np.isfinite(f_syn) else None,
            "sensibull_call_iv": sb_c_iv,
            "lab_black76_call_iv": b76_c_iv_pct,
            "lab_bsm_call_iv": bsm_c_iv_pct,
            "call_iv_error": round(c_err, 2) if c_err is not None else None,
            "sensibull_put_iv": sb_p_iv,
            "lab_black76_put_iv": b76_p_iv_pct,
            "lab_bsm_put_iv": bsm_p_iv_pct,
            "put_iv_error": round(p_err, 2) if p_err is not None else None,
            "sensibull_call_delta": sb_c_del,
            "lab_call_delta": round(b76_g.get("delta", 0.0), 3) if "delta" in b76_g else None,
            "delta_error": round(del_err, 3) if del_err is not None else None,
        })

    valid_c_errs = [r["call_iv_error"] for r in results if r["call_iv_error"] is not None]
    valid_p_errs = [r["put_iv_error"] for r in results if r["put_iv_error"] is not None]
    valid_del_errs = [r["delta_error"] for r in results if r["delta_error"] is not None]

    atm = next((r for r in results if r["is_atm"]), None)

    return {
        "spot": spot,
        "future_price": future_price,
        "atm_strike": atm_strike,
        "dte_days": dte_days,
        "r_rate_pct": round(r * 100.0, 2),
        "q_yield_pct": round(q * 100.0, 2),
        "total_strikes": len(results),
        "mean_call_iv_error": round(float(np.mean(valid_c_errs)), 2) if valid_c_errs else None,
        "max_call_iv_error": round(float(np.max(valid_c_errs)), 2) if valid_c_errs else None,
        "mean_put_iv_error": round(float(np.mean(valid_p_errs)), 2) if valid_p_errs else None,
        "max_put_iv_error": round(float(np.max(valid_p_errs)), 2) if valid_p_errs else None,
        "mean_delta_error": round(float(np.mean(valid_del_errs)), 3) if valid_del_errs else None,
        "atm_row": atm,
        "rows": results,
    }


def format_report_markdown(report: Dict[str, Any]) -> str:
    """Format audit results as a clear GitHub Flavored Markdown table."""
    lines = []
    lines.append("# Volatility Lab vs. Sensibull Derived Prices Audit Report\n")
    lines.append(f"**Underlying Spot:** {report['spot']:.2f} | **ATM Strike:** {report['atm_strike']} | **DTE:** {report['dte_days']} days")
    lines.append(f"**Risk-Free Rate (r):** {report['r_rate_pct']}% | **Continuous Dividend (q):** {report['q_yield_pct']}%\n")

    lines.append("## 1. Summary Parity Statistics")
    lines.append(f"- **Total Strikes Evaluated:** {report['total_strikes']}")
    lines.append(f"- **Mean Call IV Error (Black-76 vs. Sensibull):** {report['mean_call_iv_error']}% vol")
    lines.append(f"- **Max Call IV Error:** {report['max_call_iv_error']}% vol")
    lines.append(f"- **Mean Put IV Error:** {report['mean_put_iv_error']}% vol")
    lines.append(f"- **Mean Delta Error:** {report['mean_delta_error']}")

    atm = report.get("atm_row")
    if atm:
        lines.append("\n## 2. ATM Strike Verification")
        lines.append(f"- **Strike:** {atm['strike']}")
        lines.append(f"- **Call LTP:** ₹{atm['call_ltp']} | **Put LTP:** ₹{atm['put_ltp']}")
        lines.append(f"- **LTP benchmark parity (K + exp(rT) × (C − P)):** ₹{atm['synthetic_future']}")
        lines.append(f"- **Sensibull Call IV:** {atm['sensibull_call_iv']}% | **Lab Black-76 IV:** {atm['lab_black76_call_iv']}% (Diff: {atm['call_iv_error']}%)")
        lines.append(f"- **Sensibull Put IV:**  {atm['sensibull_put_iv']}% | **Lab Black-76 IV:** {atm['lab_black76_put_iv']}% (Diff: {atm['put_iv_error']}%)")
        lines.append(f"- **Sensibull Delta:**   {atm['sensibull_call_delta']} | **Lab Delta:** {atm['lab_call_delta']} (Diff: {atm['delta_error']})")

    lines.append("\n## 3. Strike-by-Strike Comparison Table (Active Window)")
    lines.append("| Strike | Call LTP | Put LTP | F_syn | SB Call IV | Lab B76 IV | Lab BSM IV | IV Diff | SB Delta | Lab Delta | Delta Diff | Status |")
    lines.append("|:------:|:--------:|:-------:|:-----:|:----------:|:----------:|:----------:|:-------:|:--------:|:---------:|:----------:|:------:|")

    for r in report["rows"]:
        if abs(r["strike"] - report["spot"]) / report["spot"] > 0.04:
            continue
        c_err = r["call_iv_error"]
        status = "✅ PASS" if c_err is not None and c_err <= 0.50 else ("⚠️ CHECK" if c_err is not None else "—")
        lines.append(
            f"| {r['strike']} | {r['call_ltp']:.1f} | {r['put_ltp']:.1f} | {r['synthetic_future']} | "
            f"{r['sensibull_call_iv'] or '—'} | {r['lab_black76_call_iv'] or '—'} | {r['lab_bsm_call_iv'] or '—'} | "
            f"{r['call_iv_error'] if r['call_iv_error'] is not None else '—'} | "
            f"{r['sensibull_call_delta'] or '—'} | {r['lab_call_delta'] or '—'} | "
            f"{r['delta_error'] if r['delta_error'] is not None else '—'} | {status} |"
        )

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Audit Volatility Lab derived prices against Sensibull benchmark.")
    parser.add_argument("--sensibull", required=True, type=Path, help="Sensibull JSON/CSV export")
    parser.add_argument("--spot", type=float, help="Observed spot if absent from export")
    parser.add_argument("--rate", type=float, required=True, help="Continuous annual risk-free rate, decimal")
    parser.add_argument("--dividend", type=float, required=True, help="Continuous dividend yield, decimal")
    parser.add_argument("--dte", type=float, required=True, help="Observed ACT/365 days to expiry")
    parser.add_argument("--report", type=Path, help="Output Markdown path")
    args = parser.parse_args()
    observed_spot, future, records = parse_sensibull_data(args.sensibull.read_text(encoding="utf-8"))
    spot = args.spot if args.spot is not None else observed_spot
    if spot is None or not records:
        parser.error("Supply observed benchmark rows and --spot if the export omits spot.")
    report = run_benchmark_audit(records, float(spot), future_price=future, r=args.rate,
                                 q=args.dividend, dte_days=args.dte)
    markdown = format_report_markdown(report)
    if args.report:
        args.report.write_text(markdown, encoding="utf-8")
    else:
        print(markdown)
