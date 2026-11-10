# Volatility Lab — Institutional NSE Volatility & Backtest Workbench

[![Status](https://img.shields.io/badge/Status-Production%20Live-10B981?style=flat-square)](https://volatilitylab.in)
[![Domain](https://img.shields.io/badge/Domain-volatilitylab.in-38BDF8?style=flat-square)](https://volatilitylab.in)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-D4AF37?style=flat-square)](https://github.com/pankagsaini/volatility-lab)
[![Audit](https://img.shields.io/badge/Tests-34%2F34%20Passed-10B981?style=flat-square)](test_audit_remediations.py)
[![Trading Rules](https://img.shields.io/badge/Backtest%20Rules-9%2F9%20Passed-10B981?style=flat-square)](volatility_lab/test_backtest_rules.py)
[![CI](https://github.com/pankagsaini/volatility-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/pankagsaini/volatility-lab/actions/workflows/ci.yml)

**Volatility Lab** is an institutional-grade quantitative volatility research, market monitoring, and strategy backtesting workstation focused on Indian equity index derivatives (**NIFTY 50** and **NIFTY BANK**).

The repository hosts both the full **Streamlit Analytical Engine** and the **Custom Domain GitHub Pages Web Portal** for [https://volatilitylab.in](https://volatilitylab.in).

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
│     volatilitylab.in Portal     │   │     Streamlit Analytical Desk  │   │      Local Development          │
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
├── .github/workflows/ci.yml      # Automated GitHub Actions test, lint, and parity validation
├── pyproject.toml                # Modern PEP 517/621 packaging metadata and editable install config
├── .env.example                  # Environment and secret configuration template
├── streamlit_desk/               # Dedicated Streamlit analytical workbench package
│   ├── .streamlit/config.toml    # Institutional Obsidian theme (#090d16) and low-memory config
│   ├── app.py                    # High-performance memory-bounded Streamlit entrypoint
│   ├── run.py                    # Cross-device LAN runner (auto-discovers local Wi-Fi IP)
│   └── README.md                 # Complete reactivation, deployment & secrets guide
├── docs/                         # GitHub Pages static production build
│   ├── index.html                # High-performance Quant Desk web portal (volatilitylab.in)
│   └── CNAME                     # Custom domain pointer (volatilitylab.in)
├── volatility_lab/               # Core institutional quantitative package
│   ├── core/                     # Market calendar, NSE holidays, market data helpers
│   ├── pricing/                  # BSM engine, Greeks, SVI surfaces, synthetic futures, rates
│   ├── forecasting/              # GARCH(1,1), HAR-RV, term structure & volatility regimes
│   ├── backtest/                 # Synchronous backtesting engine, 6 core options strategies
│   ├── ui/                       # Streamlit UI themes, dashboards, and research monitors
│   ├── data/                     # Upstox adapters, Google Drive sync, daily capture archives
│   ├── cloud/                    # Google Apps Script triggers for autonomous live collection
│   ├── dashboard.py              # Main multi-workspace Streamlit dashboard
│   └── test_backtest_rules.py    # 9 automated trading rules backtest suite
├── scripts/
│   └── fetch_market_data.py      # Local data hydration and schema initialization utility
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
- Python 3.10, 3.11, or 3.12
- Git

### Installation & Packaging

The project supports standard packaging via `pyproject.toml` and editable installations:

```bash
# Clone the repository
git clone https://github.com/pankagsaini/volatility-lab.git
cd volatility-lab

# Install in editable mode with development tooling (ruff, pytest, mypy)
pip install -e .[dev]

# Or install production dependencies directly:
pip install -r requirements.txt
```

### Environment & Secrets Configuration

All sensitive credentials (Upstox API keys, Google Drive access tokens, session secrets) must be stored in environment variables or a local `.env` file:

1. Copy the template:
   ```bash
   cp .env.example .env
   ```
2. Populate `.env` with your broker credentials and settings.
3. `.env` and `.env.*` are strictly ignored by Git (`.gitignore`) to prevent accidental leaks.

### Data Hydration & Snapshot Verification Utility

To verify local market data snapshots, inspect database health, and hydrate `monitor.sqlite` tables from committed Upstox captures:
```bash
python scripts/fetch_market_data.py --status  # Check database tables and snapshot counts
python scripts/fetch_market_data.py --seed    # Initialize schema & hydrate from committed snapshots
```
This utility ensures local database readiness without checking multi-megabyte binary database files into source control.

### Cloud Webhook & Deployment Security (`Code.gs`)

Google Apps Script (`volatility_lab/cloud/apps_script/Code.gs`) powers 24x7 option-chain collection and paper forward testing:
- **Public Read Mode (Default)**: When `WEBHOOK_KEY` is not set in Script Properties, `doGet()` serves read-only market snapshots and forward-test summaries to enable direct web dashboard queries on [volatilitylab.in](https://volatilitylab.in) without exposing API secrets.
- **Private / Authorized Mode**: To restrict access in private institutional deployments, configure `WEBHOOK_KEY` in Google Apps Script > Project Settings > Script Properties. All requests must then supply `?key=<WEBHOOK_KEY>` or be rejected with HTTP 401 Unauthorized.

---

## 🖥 Launching the Streamlit Workbench (Optional / Local Dev)

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

## 🧪 Automated Test Verification & CI/CD

The codebase maintains strict quality guarantees:

```bash
# 1. Run the Comprehensive 31-Test Audit Suite
python test_audit_remediations.py

# 2. Run the 9 Trading Backtest Rules Suite
python volatility_lab/test_backtest_rules.py

# 3. Lint the Package with Ruff
ruff check volatility_lab/ scripts/ pyproject.toml

# 4. Verify Streamlit AppTest Runtime Initialization
python -c "from streamlit.testing.v1 import AppTest; at = AppTest.from_file('streamlit_app.py', default_timeout=30); at.run(); assert len(at.exception) == 0; print('AppTest: 0 Exceptions')"
```

### GitHub Actions Workflow

Continuous Integration is automated via [`.github/workflows/ci.yml`](.github/workflows/ci.yml):
- **Matrix Testing**: Validates code quality on Python 3.10, 3.11, and 3.12 using `pip install -e .[dev]`.
- **Focused Linting & Audit Tests**: Runs `ruff check volatility_lab/ scripts/ pyproject.toml` (scoped to maintained packages only) and `test_audit_remediations.py`.
- **Web Parity Verification**: Enforces that `docs/index.html` and `index.html` maintain byte-level SHA-256 parity and validates `docs/CNAME`.
- **Zero-Risk Read-Only CI**: CI uses strict read-only token permissions (`contents: read`) with zero automated branch pushing.

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

1. **Black-76 & BSM Options Pricing Engine** ([`option_math.py`](volatility_lab/option_math.py)):
   - Native institutional Black-76 engine (Sensibull standard) anchored to Synthetic Forwards ($F_{\text{syn}} = K_{\text{ATM}} + C_{\text{ATM}} - P_{\text{ATM}}$) or listed index futures, ensuring strict ATM Put-Call parity symmetry.
   - Continuous ACT/365 time-to-expiry calculation precisely down to `15:30:00 IST` on expiry day (`sensibull_dte_years`).
   - Vectorized Indian market Greeks: Forward Delta ($e^{-rT} N(d_1)$), $\Gamma$, $\mathcal{V}$ (per 1% vol), and daily calendar $\Theta$ (ACT/365).
   - High-precision arbitrage-bounded Brentq implied volatility solver ($[0.01\%, 500\%]$).
   - 100% self-contained and offline-capable with zero external runtime dependencies.

2. **GARCH(1,1) Volatility Forecasting** ([`forecast.py`](volatility_lab/forecasting/forecast.py)):
   - Maximum Likelihood Estimation (MLE) of conditional variance with Nelder-Mead simplex optimizer.
   - Variance persistence modeling across multi-day horizons.

3. **SVI Volatility Surface** ([`smile.py`](volatility_lab/pricing/smile.py)):
   - Quasi-explicit Stochastic Volatility Inspired (SVI) parameterization.
   - Out-of-the-money butterfly and 25-delta risk reversal estimation.

4. **Empirical Strategy Backtester** ([`strategy_backtest.py`](volatility_lab/backtest/strategy_backtest.py)):
   - Minute-level order simulation accounting for slippage, bid-ask spreads, and realistic fill constraints.
   - Complete Upstox and statutory transaction cost calculation (STT, Exchange turnover fee, SEBI turnover, Stamp duty, and 18% GST).

---

## 📜 License & Disclaimer

This project is developed for quantitative research and educational backtesting on Indian equity derivatives. Trading options and futures involves substantial risk of loss. Historical performance and model forecasts do not guarantee future market results.
