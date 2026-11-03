"""Bespoke High-Performance FastAPI Web Server for Volatility Lab (Option 2 Modern SaaS).

Serves the modern React / TypeScript / Tailwind web client, Plotly 3D surfaces,
TradingView charts, and full REST & WebSocket APIs via FastAPI and Uvicorn.
100% local-first, zero cloud dependencies, zero cost.
"""

from pathlib import Path
import sys
import asyncio
import json

# Ensure local runtime dependencies are discoverable
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEPS = ROOT / '.dependencies'
if DEPS.exists() and str(DEPS) not in sys.path:
    sys.path.insert(0, str(DEPS))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import web_data

WEB_DIR = ROOT / 'web'
WEB_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_PLOTLY = ROOT / '.dependencies/plotly/package_data/plotly.min.js'
WEB_PLOTLY = WEB_DIR / 'plotly.min.js'
if LOCAL_PLOTLY.exists() and not WEB_PLOTLY.exists():
    import shutil
    shutil.copyfile(LOCAL_PLOTLY, WEB_PLOTLY)

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


@app.get("/api/health")
async def api_health():
    """Health check endpoint."""
    return {"status": "ok", "app": "Volatility Lab Modern SaaS Workstation", "engine": "FastAPI + Starlette"}


@app.get("/api/market-summary")
async def api_market_summary():
    """Live tickers: NIFTY 50, BANKNIFTY, INDIA VIX, and cloud sync status."""
    try:
        data = web_data.get_market_summary()
        return data
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/expiries")
async def api_expiries(symbol: str = Query("NIFTY")):
    """Active listed expiries for an instrument."""
    sym = symbol.upper()
    try:
        expiries = web_data.get_available_expiries(sym)
        return {"symbol": sym, "expiries": expiries}
    except Exception as exc:
        return JSONResponse({"symbol": sym, "error": str(exc)}, status_code=500)


@app.get("/api/option-chain")
async def api_option_chain(symbol: str = Query("NIFTY"), expiry: str = Query(None)):
    """Opstra-style option chain table with Call Greeks, Strike, Put Greeks, PCR, and Max Pain."""
    sym = symbol.upper()
    try:
        data = web_data.get_option_chain_table(sym, expiry)
        return data
    except Exception as exc:
        return JSONResponse({"symbol": sym, "error": str(exc)}, status_code=500)


@app.get("/api/vol-surface")
async def api_vol_surface(
    symbol: str = Query("NIFTY"),
    mode: str = Query("otm"),
    space: str = Query("strike"),
    range: float = Query(8.0),
    include_0dte: bool = Query(True),
    selected_expiry: str = Query(None)
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
    except Exception as exc:
        return JSONResponse({"symbol": sym, "error": str(exc)}, status_code=500)


@app.get("/api/trade-radar")
async def api_trade_radar(symbol: str = Query("NIFTY"), expiry: str = Query(None)):
    """Trade opportunity signals, expected move, range bounds, and synthetic basis."""
    sym = symbol.upper()
    try:
        data = web_data.get_trade_radar(sym, expiry=expiry)
        return data
    except Exception as exc:
        return JSONResponse({"symbol": sym, "error": str(exc)}, status_code=500)


@app.get("/api/futures-carry")
async def api_futures_carry():
    """Futures basis, roll spreads, and annualized carry."""
    try:
        data = web_data.get_futures_carry_payload()
        return data
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/candles")
async def api_candles(symbol: str = Query("NIFTY")):
    """Candlestick and volume series for TradingView Lightweight Charts."""
    sym = symbol.upper()
    try:
        data = web_data.get_historical_candles(sym)
        return data
    except Exception as exc:
        return JSONResponse({"symbol": sym, "error": str(exc)}, status_code=500)


@app.get("/api/backtest-summary")
async def api_backtest_summary():
    """Recent backtest reports with win rate, net PnL, drawdowns, and trade records."""
    try:
        data = web_data.get_backtest_overview()
        return data
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time market ticks, live IST clock, and market status streaming."""
    await websocket.accept()
    try:
        while True:
            try:
                summary = web_data.get_market_summary()
                await websocket.send_text(json.dumps({
                    "type": "market_tick",
                    "data": summary
                }))
            except Exception:
                pass
            await asyncio.sleep(2.0)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass


# Mount built production frontend assets
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


def main():
    port = 8000
    try:
        from desktop_web import install_stop_listener
        install_stop_listener()
    except Exception:
        pass
    log_path = ROOT / 'logs' / 'web_server.log'
    log_path.parent.mkdir(exist_ok=True)
    try:
        log_file = log_path.open('a', encoding='utf-8')
        sys.stdout = log_file
        sys.stderr = log_file
    except Exception:
        pass

    print(f"Starting Volatility Lab FastAPI Workstation on http://127.0.0.1:{port}...")
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='info')


if __name__ == '__main__':
    main()
