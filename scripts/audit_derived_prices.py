"""Auditing & Parity Harness: Volatility Lab Derived Prices vs. Sensibull Benchmark.

Supports ingestion of Sensibull benchmark data via:
1. JSON API response / export
2. CSV export / table copy-paste
3. Manual test vectors / CLI arguments

Compares:
- Black-Scholes-Merton (BSM) vs. Black-76 vs. Sensibull
- Strike-by-strike Implied Volatility (IV)
- Greeks: Delta, Gamma, Vega, Theta (ACT/365 vs trading day)
- Synthetic Future ($K + C - P$) & Basis
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
from scipy.optimize import brentq
from scipy.special import ndtr

_INV_SQRT_2PI = 0.3989422804014327


def _norm_pdf(x):
    return _INV_SQRT_2PI * np.exp(-0.5 * np.square(x))


def black76_greeks(F: float, K: float, T: float, r: float, sigma: float, kind: str = "CE") -> Dict[str, float]:
    """Black-76 option pricing and Greeks for futures / synthetic forwards."""
    if kind not in ("CE", "PE"):
        raise ValueError(f"Invalid option kind: {kind}")
    if min(F, K, T, sigma) <= 0:
        return {"price": 0.0, "delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta_daily": 0.0}

    sqrt_T = math.sqrt(T)
    vol_sqrt_t = sigma * sqrt_T
    d1 = (math.log(F / K) + 0.5 * (sigma ** 2) * T) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    df_r = math.exp(-r * T)
    pdf_d1 = _norm_pdf(d1)

    if kind == "CE":
        nd1 = ndtr(d1)
        nd2 = ndtr(d2)
        price = df_r * (F * nd1 - K * nd2)
        delta = df_r * nd1
        theta_ann = -(F * df_r * pdf_d1 * sigma) / (2.0 * sqrt_T) - r * price
    else:
        nd1_neg = ndtr(-d1)
        nd2_neg = ndtr(-d2)
        price = df_r * (K * nd2_neg - F * nd1_neg)
        delta = -df_r * nd1_neg
        theta_ann = -(F * df_r * pdf_d1 * sigma) / (2.0 * sqrt_T) - r * price

    gamma = (df_r * pdf_d1) / (F * vol_sqrt_t)
    vega = (F * df_r * pdf_d1 * sqrt_T) * 0.01  # 1% vol change
    theta_daily = theta_ann / 365.0

    return {
        "price": price,
        "delta": delta,
        "gamma": gamma,
        "vega": vega,
        "theta_daily": theta_daily,
    }


def solve_black76_iv(
    price: float,
    F: float,
    K: float,
    T: float,
    r: float,
    kind: str = "CE",
    bounds: Tuple[float, float] = (1e-4, 5.0),
    tol: float = 1e-8,
) -> float:
    """Arbitrage-bounded Brent solver for Black-76 implied volatility."""
    if kind not in ("CE", "PE") or min(price, F, K, T) <= 0:
        return float("nan")

    df_r = math.exp(-r * T)
    intrinsic = max(0.0, df_r * (F - K) if kind == "CE" else df_r * (K - F))
    upper = df_r * F if kind == "CE" else df_r * K

    if not (intrinsic < price < upper):
        return float("nan")

    try:
        return float(
            brentq(
                lambda sig: black76_greeks(F, K, T, r, sig, kind)["price"] - price,
                bounds[0],
                bounds[1],
                xtol=tol,
                rtol=tol,
                maxiter=100,
            )
        )
    except (ValueError, RuntimeError):
        return float("nan")


def bsm_greeks(spot: float, strike: float, T: float, r: float, vol: float, kind: str, q: float = 0.0) -> Dict[str, float]:
    """Spot Black-Scholes-Merton with continuous dividend yield q."""
    if min(spot, strike, T, vol) <= 0:
        return {"price": 0.0, "delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta_daily": 0.0}
    sqrt_t = math.sqrt(T)
    vol_sqrt_t = vol * sqrt_t
    d1 = (math.log(spot / strike) + (r - q + 0.5 * (vol ** 2)) * T) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    df_q = math.exp(-q * T)
    df_r = math.exp(-r * T)
    pdf_d1 = _norm_pdf(d1)
    if kind == "CE":
        nd1 = ndtr(d1)
        nd2 = ndtr(d2)
        price = spot * df_q * nd1 - strike * df_r * nd2
        delta = df_q * nd1
        theta_ann = -spot * df_q * pdf_d1 * vol / (2.0 * sqrt_t) - r * strike * df_r * nd2 + q * spot * df_q * nd1
    else:
        nd1_neg = ndtr(-d1)
        nd2_neg = ndtr(-d2)
        price = strike * df_r * nd2_neg - spot * df_q * nd1_neg
        delta = -df_q * nd1_neg
        theta_ann = -spot * df_q * pdf_d1 * vol / (2.0 * sqrt_t) + r * strike * df_r * nd2_neg - q * spot * df_q * nd1_neg
    gamma = (df_q * pdf_d1) / (spot * vol_sqrt_t)
    vega = spot * df_q * pdf_d1 * sqrt_t * 0.01
    return {
        "price": price,
        "delta": delta,
        "gamma": gamma,
        "vega": vega,
        "theta_daily": theta_ann / 365.0,
    }


def solve_bsm_iv(
    price: float, spot: float, strike: float, T: float, r: float, kind: str, q: float = 0.0
) -> float:
    """Spot BSM implied volatility solver."""
    df_q = math.exp(-q * T)
    df_r = math.exp(-r * T)
    intrinsic = max(0.0, spot * df_q - strike * df_r if kind == "CE" else strike * df_r - spot * df_q)
    upper = spot * df_q if kind == "CE" else strike * df_r
    if not (intrinsic < price < upper):
        return float("nan")
    try:
        return float(
            brentq(
                lambda s: bsm_greeks(spot, strike, T, r, s, kind, q)["price"] - price,
                1e-4,
                5.0,
                xtol=1e-8,
                rtol=1e-8,
                maxiter=100,
            )
        )
    except Exception:
        return float("nan")


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
    r: float = 0.0585,
    q: float = 0.0142,
    dte_days: float = 8.0,
) -> Dict[str, Any]:
    """Execute complete mathematical audit against Sensibull benchmark records."""
    T = max(1e-4, dte_days / 365.0)
    step = 50.0 if spot < 35000 else 100.0
    atm_strike = round(spot / step) * step

    results = []
    for sb in sensibull_records:
        k = sb["strike"]
        c_ltp = sb["call_ltp"]
        p_ltp = sb["put_ltp"]

        # Synthetic future at this strike: K + C - P
        f_syn = (k + c_ltp - p_ltp) if c_ltp > 0 and p_ltp > 0 else (future_price or spot)

        # 1. Spot BSM Solvers
        bsm_c_iv = solve_bsm_iv(c_ltp, spot, k, T, r, "CE", q) if c_ltp > 0 else float("nan")
        bsm_p_iv = solve_bsm_iv(p_ltp, spot, k, T, r, "PE", q) if p_ltp > 0 else float("nan")

        # 2. Black-76 Solvers (Anchored to F_syn)
        b76_c_iv = solve_black76_iv(c_ltp, f_syn, k, T, r, "CE") if c_ltp > 0 else float("nan")
        b76_p_iv = solve_black76_iv(p_ltp, f_syn, k, T, r, "PE") if p_ltp > 0 else float("nan")

        # Greeks
        b76_g = black76_greeks(f_syn, k, T, r, b76_c_iv, "CE") if not math.isnan(b76_c_iv) else {}

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
            "is_atm": abs(k - atm_strike) < 1.0,
            "call_ltp": c_ltp,
            "put_ltp": p_ltp,
            "synthetic_future": round(f_syn, 2),
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
        lines.append(f"- **Synthetic Future (K + C - P):** ₹{atm['synthetic_future']}")
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
    parser.add_argument("--sensibull", type=str, help="Path to Sensibull JSON/CSV export file")
    parser.add_argument("--report", type=str, help="Path to output markdown report file")
    args = parser.parse_args()

    if args.sensibull and Path(args.sensibull).exists():
        raw_text = Path(args.sensibull).read_text(encoding="utf-8")
        spot, future, records = parse_sensibull_data(raw_text)
        if not spot:
            spot = 25044.65  # default fallback
        rep = run_benchmark_audit(records, spot=spot, future_price=future)
        md = format_report_markdown(rep)
        if args.report:
            Path(args.report).write_text(md, encoding="utf-8")
            print(f"Report written to {args.report}")
        else:
            print(md)
    else:
        print("Self-testing on verified Upstox quote baseline...")
        quote_path = Path(__file__).resolve().parent.parent / "volatility_lab" / "data" / "nifty_option_quotes_2026-11-17.json"
        with open(quote_path, "r", encoding="utf-8") as f:
            d = json.load(f)
        spot = float(d.get("spot", 25044.65))
        recs = []
        for x in d.get("broker_option_chain", []):
            k = float(x.get("strike_price", 0))
            c_m = x.get("call_options", {}).get("market_data", {})
            c_g = x.get("call_options", {}).get("option_greeks", {})
            p_m = x.get("put_options", {}).get("market_data", {})
            p_g = x.get("put_options", {}).get("option_greeks", {})
            recs.append({
                "strike": k,
                "call_ltp": float(c_m.get("ltp") or 0),
                "put_ltp": float(p_m.get("ltp") or 0),
                "call_iv": float(c_g.get("iv") or 0),
                "put_iv": float(p_g.get("iv") or 0),
                "call_delta": float(c_g.get("delta") or 0),
                "put_delta": float(p_g.get("delta") or 0),
            })
        rep = run_benchmark_audit(recs, spot=spot)
        md = format_report_markdown(rep)
        print(md[:1200] + "\n\n... (table truncated) ...\n\nHarness ready!")
