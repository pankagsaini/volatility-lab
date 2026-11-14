"""Optional local API and checkout website. Install volatility-lab[web] to run."""
from volatility_lab import PACKAGE_ROOT

from pathlib import Path
import sys
import asyncio
import json
import logging
from typing import Literal

ROOT = PACKAGE_ROOT
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEPS = ROOT / '.dependencies'
if DEPS.exists() and str(DEPS) not in sys.path:
    sys.path.insert(0, str(DEPS))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import web_data

WEB_DIR = ROOT.parent / 'docs'
IndexSymbol = Literal["NIFTY", "BANKNIFTY"]


def _resolve_plotly_bundle() -> Path:
    """Resolve plotly.min.js from installed plotly package first, falling back to .dependencies."""
    try:
        import plotly
        pkg_path = Path(plotly.__file__).resolve().parent / 'package_data' / 'plotly.min.js'
        if pkg_path.exists():
            return pkg_path
    except Exception:
        pass
    return ROOT / '.dependencies/plotly/package_data/plotly.min.js'


LOCAL_PLOTLY = _resolve_plotly_bundle()

app = FastAPI(
    title="Volatility Lab Workstation API",
    description="Quantitative Volatility Engine for NSE NIFTY & BANKNIFTY",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def unavailable():
    logging.getLogger(__name__).exception("Market-data request failed")
    return JSONResponse({"error": "Market data unavailable. Check the local service log."}, status_code=503)


@app.get("/plotly.min.js", include_in_schema=False)
def plotly_script():
    if LOCAL_PLOTLY.is_file():
        return FileResponse(LOCAL_PLOTLY, media_type="application/javascript")
    return JSONResponse({"error": "Plotly bundle unavailable"}, status_code=404)

@app.get("/api/health")
async def api_health():
    """Health check endpoint."""
    from volatility_lab.runtime.settings import load_settings
    from volatility_lab.runtime.health import inspect
    return await asyncio.to_thread(inspect, load_settings())

@app.get("/api/market-summary")
def api_market_summary():
    """Live tickers: NIFTY 50, BANKNIFTY, INDIA VIX, and cloud sync status."""
    try:
        data = web_data.get_market_summary()
        return data
    except Exception:
        return unavailable()

@app.get("/api/expiries")
def api_expiries(symbol: IndexSymbol = Query("NIFTY")):
    """Active listed expiries for an instrument."""
    sym = symbol.upper()
    try:
        expiries = web_data.get_available_expiries(sym)
        return {"symbol": sym, "expiries": expiries}
    except Exception:
        return unavailable()

@app.get("/api/option-chain")
def api_option_chain(symbol: IndexSymbol = Query("NIFTY"), expiry: str = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """Opstra-style option chain table with Call Greeks, Strike, Put Greeks, PCR, and Max Pain."""
    sym = symbol.upper()
    try:
        data = web_data.get_option_chain_table(sym, expiry)
        return data
    except Exception:
        return unavailable()

@app.get("/api/vol-surface")
def api_vol_surface(
    symbol: IndexSymbol = Query("NIFTY"),
    mode: Literal["otm", "call", "put", "mid"] = Query("otm"),
    space: Literal["strike", "moneyness"] = Query("strike"),
    range: float = Query(8.0, gt=0, le=100),
    include_0dte: bool = Query(True),
    selected_expiry: str = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
):
    """Opstra-grade 3D Volatility Surface, 2D Multi-Expiry Smile Overlay, and Term Structure."""
    sym = symbol.upper()
    try:
        data = web_data.get_vol_surface_payload(
            symbol=sym,
            iv_mode=mode,
            space_mode=space,
            range_pct=range,
            include_zero_dte=include_0dte,
            selected_expiry=selected_expiry
        )
        return data
    except Exception:
        return unavailable()

@app.get("/api/trade-radar")
def api_trade_radar(symbol: IndexSymbol = Query("NIFTY"), expiry: str = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """Trade opportunity signals, expected move, range bounds, and synthetic basis."""
    sym = symbol.upper()
    try:
        data = web_data.get_trade_radar(sym, expiry=expiry)
        return data
    except Exception:
        return unavailable()

@app.get("/api/futures-carry")
def api_futures_carry():
    """Futures basis, roll spreads, and annualized carry."""
    try:
        data = web_data.get_futures_carry_payload()
        return data
    except Exception:
        return unavailable()

@app.get("/api/candles")
def api_candles(symbol: IndexSymbol = Query("NIFTY")):
    """Candlestick and volume series for TradingView Lightweight Charts."""
    sym = symbol.upper()
    try:
        data = web_data.get_historical_candles(sym)
        return data
    except Exception:
        return unavailable()

@app.get("/api/backtest-summary")
def api_backtest_summary():
    """Recent backtest reports with win rate, net PnL, drawdowns, and trade records."""
    try:
        data = web_data.get_backtest_overview()
        return data
    except Exception:
        return unavailable()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time market ticks, live IST clock, and market status streaming."""
    await websocket.accept()
    try:
        while True:
            try:
                summary = await asyncio.to_thread(web_data.get_market_summary)
                await websocket.send_text(json.dumps({
                    "type": "market_tick",
                    "data": summary
                }, allow_nan=False))
            except (WebSocketDisconnect, asyncio.CancelledError):
                raise
            except Exception:
                pass
            await asyncio.sleep(2.0)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass

if (WEB_DIR / "index.html").is_file():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

def main():
    port = 8000
    try:
        from desktop_web import install_stop_listener
        install_stop_listener()
    except Exception:
        pass
    from volatility_lab.runtime.settings import data_dir
    from volatility_lab.storage.references import initialize_references
    initialize_references(data_dir())

    print(f"Starting Volatility Lab FastAPI Workstation on http://127.0.0.1:{port}...")
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='info')

if __name__ == '__main__':
    main()
