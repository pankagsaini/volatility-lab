# Volatility Lab — Institutional NSE Volatility & Backtest Workbench

[![Status](https://img.shields.io/badge/Status-Production%20Live-10B981?style=flat-square)](https://volatilitylab.in)
[![Domain](https://img.shields.io/badge/Domain-volatilitylab.in-38BDF8?style=flat-square)](https://volatilitylab.in)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12%20%7C%203.15-D4AF37?style=flat-square)](https://github.com/pankagsaini/volatility-lab)
[![Audit](https://img.shields.io/badge/Tests-31%2F31%20Passed-10B981?style=flat-square)](test_audit_remediations.py)
[![Trading Rules](https://img.shields.io/badge/Backtest%20Rules-9%2F9%20Passed-10B981?style=flat-square)](Volatility%20Lab/test_backtest_rules.py)

**Volatility Lab** is an institutional-grade quantitative volatility research, market monitoring, and strategy backtesting workstation focused on Indian equity index derivatives (**NIFTY 50** and **NIFTY BANK**).

The repository hosts both the full **Streamlit Cloud Analytical Engine** and the **Custom Domain GitHub Pages Web Portal** for [https://volatilitylab.in](https://volatilitylab.in).

---

## 🏛 Architecture Overview

```
                                      ┌────────────────────────────────┐
                                      │   Google Apps Script Trigger   │
                                      │   (Runs M-F 09:15-15:30 IST)   │
                                      └───────────────┬────────────────┘
                                                      │ Fetches live Upstox V2 quotes
                                                      ▼
                                      ┌────────────────────────────────┐
                                      │    Google Drive Storage Hub    │
                                      │  (JSON, GZIP, and CSV Archives)│
                                      └───────────────┬────────────────┘
                                                      │ Synchronizes at startup
                                                      ▼
┌─────────────────────────────────┐   ┌────────────────────────────────┐   ┌─────────────────────────────────┐
│     volatilitylab.in Portal     │   │     Streamlit Cloud Engine     │   │      Local Development          │
│   (docs/index.html + CNAME)     │   │       (streamlit_app.py)       │   │    (streamlit_desk/run.py)      │
│  - 0-delay browser execution    │   │  - Memory-bounded runtime (v11)│   │  - Auto-discovers LAN IP        │
│  - Options Quant Desk           │   │  - Full mathematical modules   │   │  - Multi-device local testing   │
│  - SVI What-If Sliders          │   │  - Plotly interactive charts   │   │  - Offline fallback datasets    │
└─────────────────────────────────┘   └────────────────────────────────┘   └─────────────────────────────────┘
```

---

## 📁 Repository Directory Structure

```
.
├── streamlit_desk/               # Dedicated Streamlit analytical workbench package
│   ├── .streamlit/config.toml    # Institutional Obsidian theme (#090d16) and low-memory config
│   ├── app.py                    # High-performance memory-bounded Streamlit entrypoint
│   ├── run.py                    # Cross-device LAN runner (auto-discovers local Wi-Fi IP)
│   └── README.md                 # Complete reactivation, deployment & secrets guide
├── docs/                         # GitHub Pages static production build
│   ├── index.html                # High-performance Quant Desk web portal (volatilitylab.in)
│   └── CNAME                     # Custom domain pointer (volatilitylab.in)
├── Volatility Lab/
│   ├── .streamlit/config.toml    # Subdirectory Streamlit configuration mirror
│   ├── cloud/
│   │   └── apps_script/Code.gs   # Autonomous Upstox live options chain collector
│   ├── data/                     # Offline 1-year CSV baselines and database fallback
│   │   ├── banknifty_one_year.csv
│   │   ├── nifty_one_year.csv
│   │   ├── monitor.sqlite
│   │   ├── dividend_yields.json
│   │   └── risk_free_rate.json
│   ├── backtest_common.py        # Contract lot sizes, dated schedules & blacklists
│   ├── backtest_engine.py        # Synchronous backtester with cloud SQLite fallback
│   ├── backtest_jobs.py          # Background task scheduler and queue state machine
│   ├── backtest_signals.py       # Signal evaluation with strict anti-lookahead guards
│   ├── backtest_ui.py            # Streamlit Backtest Workbench UI (6 scenario presets)
│   ├── broker_charges.py         # Statutory NSE/SEBI fees (STT, stamp duty, GST, brokerage)
│   ├── collector_support.py      # Thread-safe atomic JSON/CSV writers and DPAPI tools
│   ├── dashboard.py              # Main multi-workspace Streamlit dashboard
│   ├── expiry_forecast.py        # Term structure & forward volatility forecasting
│   ├── forecast.py               # GARCH(1,1) with resilient pure-Python Nelder-Mead fallback
│   ├── futures_backtest.py       # Dated futures carry engine with expiry roll logic
│   ├── google_archive_sync.py    # Multi-threaded Google Drive historical sync
│   ├── market_calendar.py        # NSE trading calendar & holidays (2024–2028)
│   ├── monitor_ui.py             # Options Desk UI (Radar, Regime, Term, Smile, Intraday)
│   ├── option_math.py            # Vectorized BSM Greeks & machine-precision Brent solver
│   ├── pricing_inputs.py         # Synthetic future calculation & dividend-adjusted spot
│   ├── relative_value.py         # Cross-strike implied volatility spread tracking
│   ├── smile.py                  # SVI fair volatility surface modeling
│   ├── strategy_backtest.py      # 6 core option strategies (Straddle, Strangle, Condor, etc.)
│   ├── term_structure.py         # Listed expiry term structure & calendar arbitrage checks
│   ├── trade_review.py           # Multi-leg payoff matrix, breakevens & Greeks breakdown
│   ├── ui_theme.py               # Obsidian slate & sovereign gold institutional design system
│   ├── vol_lib.py                # OHLC validation, Parkinson, Garman-Klass & Yang-Zhang RV
│   ├── vol_surface_3d.py         # 3D interactive volatility surface generator
│   └── test_backtest_rules.py    # 9 automated trading rules backtest suite
├── CNAME                         # Root custom domain pointer
├── MAIN_AUDIT.md                 # Master Audit & Technical Architecture Reference
├── README.md                     # This file
├── requirements.txt              # Production dependency specifications
├── streamlit_app.py              # Lightweight root delegator forwarding to streamlit_desk/app.py
└── test_audit_remediations.py    # Comprehensive 31-test audit verification suite
```

---

## 🚀 Getting Started

### Prerequisites
- Python 3.11, 3.12, or 3.15
- Git

### Installation
```bash
# Clone the repository
git clone https://github.com/pankagsaini/volatility-lab.git
cd volatility-lab

# Install production dependencies
pip install -r requirements.txt
```

### Launching the Streamlit Workbench (Optional / Local Dev)

The primary production portal is deployed at **[https://volatilitylab.in](https://volatilitylab.in)**.
All Streamlit analytical tools and UI runners are organized under `streamlit_desk/`:

#### 1. Standard Streamlit Run
```bash
streamlit run streamlit_app.py
# Or run directly from the package:
streamlit run streamlit_desk/app.py
```

#### 2. Any-Device LAN Runner
To access the dashboard from your phone, tablet, or another machine on the same Wi-Fi:
```bash
python streamlit_desk/run.py
```
This automatically binds to `0.0.0.0:8501`, identifies your local LAN IP address, and outputs a convenient link.

---

## 🧪 Automated Test Verification

The codebase maintains strict 100% test passing guarantees:

```bash
# 1. Run the Comprehensive 31-Test Audit Suite
python test_audit_remediations.py

# 2. Run the 9 Trading Backtest Rules Suite
python "Volatility Lab/test_backtest_rules.py"

# 3. Verify Streamlit AppTest Runtime Initialization
python -c "from streamlit.testing.v1 import AppTest; at = AppTest.from_file('streamlit_app.py', default_timeout=30); at.run(); assert len(at.exception) == 0; print('AppTest: 0 Exceptions')"
```

---

## 🌐 Custom Domain & Website

The repository powers **[https://volatilitylab.in](https://volatilitylab.in)** via GitHub Pages configured to deploy from `/docs`.

- **Domain Configuration**: Managed via `CNAME` and `docs/CNAME`.
- **DNS Records (GoDaddy)**:
  - `A` Record `@` ➔ `185.199.108.153`
  - `A` Record `@` ➔ `185.199.109.153`
  - `A` Record `@` ➔ `185.199.110.153`
  - `A` Record `@` ➔ `185.199.111.153`
  - `CNAME` Record `www` ➔ `pankagsaini.github.io`
- For step-by-step domain configuration details, refer to [README_DEPLOY_GODADDY.md](README_DEPLOY_GODADDY.md).

---

## 📊 Core Quantitative Models

1. **Black-Scholes-Merton Engine** ([`option_math.py`](Volatility%20Lab/option_math.py)):
   - Dividend-adjusted BSM analytical pricing with vectorized Greeks ($\Delta$, $\Gamma$, $\mathcal{V}$, $\Theta$).
   - High-precision root finder using Brent-Dekker algorithm with pure-Python fallback.

2. **GARCH(1,1) Volatility Forecasting** ([`forecast.py`](Volatility%20Lab/forecast.py)):
   - Maximum Likelihood Estimation (MLE) of conditional variance with Nelder-Mead simplex optimizer.
   - Variance persistence modeling across multi-day horizons.

3. **SVI Volatility Surface** ([`smile.py`](Volatility%20Lab/smile.py)):
   - Quasi-explicit Stochastic Volatility Inspired (SVI) parameterization.
   - Out-of-the-money butterfly and 25-delta risk reversal estimation.

4. **Empirical Strategy Backtester** ([`strategy_backtest.py`](Volatility%20Lab/strategy_backtest.py)):
   - Minute-level order simulation accounting for slippage, bid-ask spreads, and realistic fill constraints.
   - Complete Upstox and statutory transaction cost calculation (STT, Exchange turnover fee, SEBI turnover, Stamp duty, and 18% GST).

---

## 📜 License & Disclaimer

This project is developed for quantitative research and educational backtesting on Indian equity derivatives. Trading options and futures involves substantial risk of loss. Historical performance and model forecasts do not guarantee future market results.
