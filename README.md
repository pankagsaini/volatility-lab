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
┌──────────────────────────────────────────────────────────────────────┐
│                       Streamlit Cloud Dashboard                      │
│     • Volatility Radar & Surface 3D  • Term Structure & Skew         │
│     • Real-time Greeks & ATM IV       • Multi-leg Backtesting Engine │
└──────────────────────────────────────────────────────────────────────┘
```

- **Cloud Capture (Zero Laptop Dependency)**: Autonomous Google Apps Script runs daily on Google Cloud infrastructure during market hours, capturing snapshot options data and archiving directly to Google Drive.
- **Web Dashboard**: Hosted on Streamlit Cloud, pulling real-time options telemetry and displaying analytics with Bloomberg-grade precision.

---

## Repository Structure

```
├── .streamlit/             # Streamlit Cloud configuration & secrets templates
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
├── requirements.txt        # Python dependency manifest
└── Open Dashboard.cmd      # Local desktop launcher (Windows)
```

---

## Running Locally

### Prerequisites
- Python 3.11+
- Install dependencies:
  ```bash
  pip install -r requirements.txt
  ```

### Launch
```bash
streamlit run "Volatility Lab/dashboard.py"
```
Or double-click `Open Dashboard.cmd` on Windows.

---

## Running Tests

Execute the full regression test suite:
```bash
python -m unittest discover -s "Volatility Lab" -p "test_*.py"
```
*(All 175 regression tests pass).*
