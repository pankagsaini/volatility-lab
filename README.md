# Volatility Lab

Institutional-grade quantitative volatility research, market monitoring, and strategy backtesting workstation focused on Indian equity index derivatives (**NIFTY 50** and **NIFTY BANK**).

---

## Architecture Overview

```
                                      ┌────────────────────────────────┐
                                      │   Google Apps Script Trigger   │
                                      │   (Runs M-F 09:15-15:30 IST)   │
                                      └──────────────┬─────────────────┘
                                                     │ Captures Option Chains
                                                     ▼
┌─────────────────────────┐           ┌────────────────────────────────┐
│      Upstox API v2      │◄──────────┤   Google Drive Private Cloud   │
│   (Live Market Feeds)   │           │   (Live & Historical Store)    │
└────────────┬────────────┘           └────────────────────────────────┘
             │                                       │
             ▼                                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                       Streamlit Cloud Dashboard                      │
│     • Volatility Radar & Surface 3D  • Term Structure & Skew         │
│     • Real-time Greeks & ATM IV       • Multi-leg Backtesting Engine │
└──────────────────────────────────────────────────────────────────────────┘
```

- **Cloud Capture (Zero Laptop Dependency)**: Autonomous Google Apps Script runs daily on Google Cloud infrastructure during market hours, capturing snapshot options data and archiving directly to Google Drive.
- **Web Dashboard**: Hosted on Streamlit Cloud or locally via the FastAPI web app, pulling real-time options telemetry and displaying analytics with Bloomberg-grade precision.

---

## Repository Structure

```
├── .streamlit/             # Streamlit Cloud configuration & secrets templates
├── .python-version        # Supported Python runtime (3.11)
├── scripts/                # Local validation and smoke-test tooling
├── Volatility Lab/
│   ├── cloud/              # Apps Script automation routines & sync infrastructure
│   ├── data/               # Reference catalogues, active contracts & calibration data
│   │   ├── charges/        # Upstox fee schedules
│   │   ├── upstox/         # Master instrument catalogues & expiries
│   │   └── monitor.sqlite  # Active smile curve & intraday observations
│   ├── frontend/           # Modern TypeScript / React frontend source
│   ├── web/                # Built web assets
│   ├── dashboard.py        # Streamlit application main entry point
│   ├── monitor_ui.py       # Live Volatility Radar, Trade Review & Monitor UI
│   ├── backtest_*.py       # Institutional multi-leg backtesting engines
│   └── test_*.py           # Comprehensive unit & regression test suite
├── app.py                  # Streamlit Cloud loader
├── streamlit_app.py        # Cloud entrypoint
├── run_local.py            # Cross-platform local launcher
├── requirements.txt        # Python dependency manifest
├── scripts/validate_setup.py  # Setup validation utility
├── Open Dashboard.cmd      # Windows convenience launcher
├── Open Volatility Lab Web.cmd # Windows convenience launcher
└── Turn Off Dashboard.cmd # Windows stop helper
```

---

## Fresh Clone Setup

### Prerequisites
- Python 3.11+
- A virtual environment is recommended

### Install and validate
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python scripts/validate_setup.py
```

### Run locally
```bash
python run_local.py --dashboard
```

This starts the main Streamlit workstation.

```bash
python run_local.py --web
```

This starts the FastAPI web workstation and serves the built frontend assets.

```bash
python run_local.py --both
```

This runs both entry points together.

### Legacy Windows launchers
The files `Open Dashboard.cmd`, `Open Volatility Lab Web.cmd`, and `Turn Off Dashboard.cmd` are convenience wrappers for a Windows desktop setup. They are useful for local Windows use, but the cross-platform launcher in `run_local.py` is the recommended path for fresh-clone setup and CI validation.

---

## Running Tests

Execute the validation and smoke checks:
```bash
python scripts/validate_setup.py
python scripts/smoke_test.py
```

Execute the full regression test suite:
```bash
python -m unittest discover -s "Volatility Lab" -p "test_*.py"
```
*(The project includes a large regression suite; use the smoke checks for a rapid fresh-clone validation.)*
