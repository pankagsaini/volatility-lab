"""Carry forward dated EGARCH forecasts into the current release after a failed refresh."""

from datetime import date, datetime
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import publish_live_state
from scripts.public_artifact import fetch_json
from volatility_lab.core.market_calendar import IST, session_window


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def forecast_fields(state, now):
    update = state["forecast_update"]
    through = date.fromisoformat(update["completed_session"])
    window = session_window(through)
    generated = datetime.fromisoformat(update["generated_at"])
    if (state["schema"] != 1 or update["status"] != "complete" or update["provider"] != "Upstox daily candles"
            or window is None or generated.tzinfo is None or not window[1] <= generated <= now):
        raise ValueError("Invalid dated forecast publication")
    forecasts = {}
    sources = {}
    for symbol in ("NIFTY", "BANKNIFTY"):
        name = symbol.lower() + "_one_year.csv"
        sources[name] = state["sources"][name]
        if not re.fullmatch(r"[0-9a-f]{64}", sources[name]):
            raise ValueError("Invalid candle source checksum")
        series = state["forecasts"][symbol]
        if series["training_through"] != through.isoformat() or not finite(series["yang_zhang20"]) or series["yang_zhang20"] < 0:
            raise ValueError("Inconsistent forecast series")
        points = []
        expiries = set()
        for row in series["expiries"]:
            expiry = date.fromisoformat(row["expiry"])
            anchor = datetime.fromisoformat(row["as_of"])
            if (expiry <= through or expiry in expiries or anchor != window[1] or row["training_through"] != through.isoformat()
                    or not isinstance(row["status"], str)):
                raise ValueError("Inconsistent dated expiry forecast")
            if row.get("egarch") is not None and (
                    not finite(row["egarch"]) or row["egarch"] <= 0 or row.get("model") != "EGARCH(1,1,1) Gaussian"
                    or any(type(row.get(key)) is not int or row[key] <= 0 for key in ("sessions", "training_returns", "simulations"))):
                raise ValueError("Invalid expiry EGARCH estimate")
            fields = ("expiry", "as_of", "training_through", "status", "model", "sessions", "training_returns",
                      "calendar_status", "simulations", "seed", "egarch")
            points.append({key: row[key] for key in fields if key in row})
            expiries.add(expiry)
        if not any(row.get("egarch") is not None for row in points):
            raise ValueError("Missing usable expiry forecast")
        forecasts[symbol] = {"training_through": through.isoformat(), "yang_zhang20": series["yang_zhang20"], "expiries": points}
        if "closing_risk" in series:
            risk = series["closing_risk"]
            metrics = ("close", "hv10", "hv20", "hv30", "ewma")
            if (risk["as_of"] != through.isoformat() or risk["units"] != "annualized_percent" or risk["source"] != "Lab daily close history"
                    or any(not finite(risk[key]) or risk[key] < 0 for key in metrics) or risk["close"] <= 0):
                raise ValueError("Invalid dated closing risk")
            forecasts[symbol]["closing_risk"] = {key: risk[key] for key in ("as_of", "source", "units", *metrics)}
    metadata = {key: update[key] for key in ("completed_session", "generated_at", "status", "provider")}
    return (through, generated), forecasts, metadata, sources


def retain(now=None):
    root = publish_live_state.ROOT
    state = json.loads((root / "docs/live_state.json").read_bytes())
    now = now or datetime.now(IST)
    local_rank, *_ = forecast_fields(state, now)
    rank, forecasts, update, sources = forecast_fields(fetch_json("live_state.json", 4_000_000), now)
    if rank > local_rank:
        state.update(forecasts=forecasts, forecast_update=update)
        state["sources"].update(sources)
        html = (root / "index.html").read_text(encoding="utf-8")
        publish_live_state.write_state(html, state)
    print("Retained dated EGARCH forecasts trained through", state["forecast_update"]["completed_session"])


if __name__ == "__main__":
    retain()
