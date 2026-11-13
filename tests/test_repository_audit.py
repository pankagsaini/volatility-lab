import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from volatility_lab import backtest_engine, forecast, forward_test, strategy_backtest
from volatility_lab.backtest.forward_test import evaluate_strategies_for_chain as legacy_evaluate


ROOT = Path(__file__).resolve().parents[1]
AT = "2026-11-13T10:00:00+05:30"


def option_chain(spot, step, wing_offset=4, level=18):
    rows = []
    for offset in range(-16, 17):
        strike = spot + offset * step
        row = {"strike": strike}
        for prefix, sign in (("c", 1), ("p", -1)):
            mid = max(0, (spot - strike) * sign) + 200 / (1 + abs(offset) / 4)
            delta = max(.01, min(.99, .5 - sign * .25 * offset / wing_offset))
            row.update({prefix + "Bid": round(mid - .5, 2), prefix + "Ask": round(mid + .5, 2),
                        prefix + "BidQty": 5000, prefix + "AskQty": 5000,
                        prefix + "QuoteTimestamp": AT, prefix + "Delta": sign * delta,
                        prefix + "Iv": level + (5 if prefix == "p" else 1) * (.5 - delta)})
        rows.append(row)
    return rows


def evaluate(sym, spot, rows, lots=1, dte=6):
    return forward_test.evaluate_strategies_for_chain(sym, "2026-11-23", spot, rows, 14, 15, dte,
                                                      lots=lots, quote_reference=AT, require_event_timestamps=True)


def test_expiry_payoff_and_execution_across_engines():
    cases = []
    for sym, spot, step, lot_size in (("NIFTY", 25000, 50, 65), ("BANKNIFTY", 60000, 100, 30)):
        rows = option_chain(spot, step)
        for lots in (1, 3):
            setups = evaluate(sym, spot, rows, lots)
            assert len(setups) == 6
            for setup in setups:
                setup["exitThresholds"] = forward_test.exit_thresholds(
                    setup["strat"], setup["capital"], setup["maxCredit"], setup["maxLoss"], "SL_50_MAXLOSS", "TP_50_30")
                legs = setup["legs"]

                def pnl(price):
                    total = 0
                    for leg in legs:
                        intrinsic = max(0, price - leg["strike"]) if leg["type"] == "CE" else max(0, leg["strike"] - price)
                        total += leg["side"] * leg["mult"] * (intrinsic - leg["entryPx"])
                    return total * lot_size * lots

                values = [pnl(x) for x in [0, *(leg["strike"] for leg in legs), 2 * spot]]
                assert setup["maxLoss"] == pytest.approx(max(0, -min(values)), abs=.005)
                if setup["strat"] == "S2":
                    assert setup["maxProfit"] is None and setup["netPremium"] < 0
                else:
                    assert setup["maxProfit"] == pytest.approx(max(values), abs=.005)
                    assert setup["netPremium"] > 0
                assert len(setup["breakEvens"]) >= 1
                assert all(abs(pnl(price)) < .05 for price in setup["breakEvens"])
            cases.append(dict(sym=sym, spot=spot, rows=rows, lots=lots, setups=setups))
    subprocess.run(["node", str(ROOT / "scripts/test_strategy_payoff.cjs")],
                   input=json.dumps(cases), text=True, check=True)


def test_wing_metrics_use_signed_delta_brackets():
    for offset, level, dte in [(3.5, 16, 6), (7.5, 27, 21)]:
        rows = option_chain(25000, 50, offset, level)
        def skew():
            return next(s for s in evaluate("NIFTY", 25000, rows, dte=dte) if s["strat"] == "S4")
        assert "RR25 +1.00 pp" in skew()["metrics"]
        for row in rows:
            row["pDelta"] = abs(row["pDelta"])
        assert "unavailable" in skew()["metrics"] and not skew()["triggered"]


def test_reject_bad_books_and_out_of_policy_premium():
    assert legacy_evaluate is forward_test.evaluate_strategies_for_chain
    assert not evaluate("NIFTY", 25000, [])
    rows = option_chain(25000, 50)
    for row in rows:
        row["cBid"], row["cAsk"], row["pBid"], row["pAsk"] = 99, 101, 99, 101
    assert [s["strat"] for s in evaluate("NIFTY", 25000, rows)] == ["S2"]
    for remaining in (5, 0):
        for row in rows:
            row["cAskQty"] = remaining
        assert not evaluate("NIFTY", 25000, rows)


@pytest.mark.parametrize("entry_close", [24000, 30000])
def test_no_preceding_bar_skips_both_backtests(entry_close):
    day, key = "2026-11-11", "NSE_INDEX|Nifty 50"
    ticks = {key: {day + " 09:20": [25000, 30000, 24000, entry_close, 1000]}}
    results = [strategy_backtest.simulate({"start": day, "end": day}, [], ticks),
               backtest_engine.simulate([], ticks, key, day, day)]
    for trades, skips in results:
        assert trades.empty
        assert skips.iloc[0]["Reason"] == "Missing preceding spot minute"


def test_interrupted_and_concurrent_ledger_writes(tmp_path, monkeypatch):
    ledger = forward_test.load_forward_ledger(tmp_path)
    ledger["intraday_mtm_series"].append({"date": "2026-11-13", "gross": 101})
    target = forward_test.save_forward_ledger(ledger, tmp_path)
    first, second = (forward_test.load_forward_ledger(tmp_path) for _ in range(2))
    first["intraday_mtm_series"].append({"date": "2026-11-13", "gross": 102})
    second["intraday_mtm_series"].append({"date": "2026-11-13", "gross": 103})
    with ThreadPoolExecutor(2) as pool:
        results = [pool.submit(forward_test.save_forward_ledger, state, tmp_path) for state in (first, second)]
    assert sum(r.exception() is None for r in results) == 1
    assert any(isinstance(r.exception(), RuntimeError) for r in results)
    previous = target.read_bytes()
    with monkeypatch.context() as patch:
        import collector_support
        replace = collector_support.os.replace
        def interrupt_target_write(source, destination):
            if destination == target:
                raise OSError("interrupted")
            return replace(source, destination)
        patch.setattr(collector_support.os, "replace", interrupt_target_write)
        with pytest.raises(OSError, match="interrupted"):
            forward_test.save_forward_ledger(forward_test.load_forward_ledger(tmp_path), tmp_path)
    assert target.read_bytes() == previous
    target.write_bytes(b'{"open_positions":')
    with pytest.raises(ValueError, match="Invalid forward ledger"):
        forward_test.load_forward_ledger(tmp_path)
    with pytest.raises(ValueError):
        forward_test.save_forward_ledger(first, tmp_path)
    restored = forward_test.restore_forward_ledger(tmp_path)
    assert restored["intraday_mtm_series"] == json.loads(previous)["intraday_mtm_series"]
    assert next(tmp_path.glob("*.before-restore")).read_bytes() == b'{"open_positions":'


@pytest.mark.parametrize("success,objective,parameters", [
    (False, 1, [.1, .1, .8]), (True, np.nan, [.1, .1, .8]), (True, 1, [.1, .4, .9]),
])
def test_invalid_scipy_fit_withheld(monkeypatch, success, objective, parameters):
    fit = SimpleNamespace(success=success, fun=objective, status=2, x=parameters)
    monkeypatch.setattr(forecast, "minimize", lambda *a, **kw: fit)
    with pytest.raises(ValueError):
        forecast._scipy_garch11_fallback(np.array([-.3, .5, .1, -.2]), 5)


@pytest.mark.parametrize("flag,alpha,variance", [(1, .1, .1), (0, .5, .1), (0, .1, np.nan)])
def test_invalid_arch_fit_withheld(monkeypatch, flag, alpha, variance):
    import arch
    fit = MagicMock(convergence_flag=flag, nobs=4, loglikelihood=-2., aic=10., bic=12.)
    fit.params = {"omega": .1, "alpha[1]": alpha, "beta[1]": .7}
    fit.conditional_volatility = np.ones(4)
    fit.forecast.return_value.variance = pd.DataFrame([[variance] * 245])
    model = MagicMock()
    model.fit.return_value = fit
    monkeypatch.setattr(arch, "arch_model", lambda *a, **kw: model)
    with pytest.raises(ValueError):
        forecast._cached_garch_mle("invalid", np.ones(4).tobytes(), 4)
