"""Bespoke High-Performance Web Server for Volatility Lab (Option 2 Architecture).

Serves modern Opstra/Definedge-inspired Single Page Web Application and JSON REST APIs
via Starlette and Uvicorn. Zero external dependencies, 100% local-first and zero cost.
"""

from pathlib import Path
import sys

# Ensure local dependencies are discoverable
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEPS = ROOT / '.dependencies'
if DEPS.exists() and str(DEPS) not in sys.path:
    sys.path.insert(0, str(DEPS))

from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
import uvicorn

import web_data

WEB_DIR = ROOT / 'web'
WEB_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_PLOTLY = ROOT / '.dependencies/plotly/package_data/plotly.min.js'
WEB_PLOTLY = WEB_DIR / 'plotly.min.js'
if LOCAL_PLOTLY.exists() and not WEB_PLOTLY.exists():
    import shutil
    shutil.copyfile(LOCAL_PLOTLY, WEB_PLOTLY)


async def api_health(request):
    """Health check endpoint."""
    return JSONResponse({'status': 'ok', 'app': 'Volatility Lab Opstra Workstation'})


async def api_market_summary(request):
    """Live tickers: NIFTY 50, BANKNIFTY, INDIA VIX, and market status."""
    try:
        data = web_data.get_market_summary()
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({'error': str(exc)}, status_code=500)


async def api_expiries(request):
    """Active listed expiries for an instrument."""
    symbol = request.query_params.get('symbol', 'NIFTY').upper()
    try:
        expiries = web_data.get_available_expiries(symbol)
        return JSONResponse({'symbol': symbol, 'expiries': expiries})
    except Exception as exc:
        return JSONResponse({'symbol': symbol, 'error': str(exc)}, status_code=500)


async def api_option_chain(request):
    """Opstra-style option chain table with Call Greeks, Strike, Put Greeks, PCR, and Max Pain."""
    symbol = request.query_params.get('symbol', 'NIFTY').upper()
    expiry = request.query_params.get('expiry')
    try:
        data = web_data.get_option_chain_table(symbol, expiry)
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({'symbol': symbol, 'error': str(exc)}, status_code=500)


async def api_vol_surface(request):
    """Opstra-grade 3D Volatility Surface, 2D Smile Overlay, and Term Structure."""
    symbol = request.query_params.get('symbol', 'NIFTY').upper()
    iv_mode = request.query_params.get('mode', 'otm')
    space_mode = request.query_params.get('space', 'strike')
    try:
        range_pct = float(request.query_params.get('range', '8.0'))
    except ValueError:
        range_pct = 8.0
    include_zero_dte = request.query_params.get('include_0dte', 'true').lower() == 'true'

    try:
        data = web_data.get_vol_surface_payload(
            symbol=symbol,
            iv_mode=iv_mode,
            space_mode=space_mode,
            range_pct=range_pct,
            include_zero_dte=include_zero_dte
        )
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({'symbol': symbol, 'error': str(exc)}, status_code=500)


async def api_trade_radar(request):
    """Trade opportunity signals, expected move, and synthetic basis."""
    symbol = request.query_params.get('symbol', 'NIFTY').upper()
    try:
        data = web_data.get_trade_radar(symbol)
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({'symbol': symbol, 'error': str(exc)}, status_code=500)


async def api_futures_carry(request):
    """Futures basis, roll spreads, and annualized carry."""
    try:
        data = web_data.get_futures_carry_payload()
        return JSONResponse(data)
    except Exception as exc:
        return JSONResponse({'error': str(exc)}, status_code=500)


middleware = [
    Middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])
]

routes = [
    Route('/api/health', api_health),
    Route('/api/market-summary', api_market_summary),
    Route('/api/expiries', api_expiries),
    Route('/api/option-chain', api_option_chain),
    Route('/api/vol-surface', api_vol_surface),
    Route('/api/trade-radar', api_trade_radar),
    Route('/api/futures-carry', api_futures_carry),
    Mount('/', StaticFiles(directory=WEB_DIR, html=True), name='web')
]

app = Starlette(debug=True, routes=routes, middleware=middleware)


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

    print(f"Starting Volatility Lab Opstra Workstation on http://127.0.0.1:{port}...")
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='info')


if __name__ == '__main__':
    main()
