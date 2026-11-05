# Volatility Lab — Comprehensive AI Handover & Context Document
*Designed for Microsoft Copilot, Claude, ChatGPT, and AI Engineering Assistants*

---

## 1. Executive Summary & Project Purpose

**Volatility Lab** is an institutional-grade quantitative volatility research, real-time market monitoring, and algorithmic strategy backtesting workstation designed specifically for Indian equity index derivatives (**NIFTY 50** and **NIFTY BANK**) traded on the National Stock Exchange of India (NSE).

- **GitHub Repository**: `https://github.com/pankagsaini/volatility-lab` (branches `master` and `main` are identical and synchronized).
- **Primary Web Deployment**: Hosted 24/7 on **Streamlit Community Cloud** with continuous deployment from GitHub.
- **Data Engine**: Hybrid live-streaming via Upstox API v2 and automated serverless options capture via Google Apps Script into Google Drive.
- **Codebase Quality**: 175 / 175 unit tests passing (`python -m unittest discover -s "Volatility Lab" -p "test_*.py"`).

---

## 2. End-to-End System Architecture

```
                                 ┌──────────────────────────────────────────────┐
                                 │       Google Apps Script Automation          │
                                 │   • Runs 100% on Google Cloud (Triggered)   │
                                 │   • M-F 09:15 - 15:30 IST (Market Hours)    │
                                 └──────────────────────┬───────────────────────┘
                                                        │ Captures full option chains
                                                        ▼
┌──────────────────────────────┐         ┌──────────────────────────────────────┐
│        Upstox API v2         │◄────────┤      Google Drive Cloud Archive      │
│  • Live WebSocket / REST     │         │  • 5 TB Storage Tier                 │
│  • Intraday minute bars      │         │  • 5-Year Historical Archives (.zip) │
└──────────────┬───────────────┘         └──────────────────┬───────────────────┘
               │                                            │
               │ Direct Cloud Feed                          │ Cloud Mirror Sync
               ▼                                            ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                     Streamlit Community Cloud Dashboard                       │
│  • Classic Clean Workspace (No local laptop dependencies)                    │
│  • Volatility Radar, Live Greeks, Implied Volatility (IV) Smile & Skew Curves │
│  • 3D Volatility Surface, Term Structure, Real-time Synthetic Futures        │
│  • Multi-leg Options & Futures Carry Backtesting Engines                      │
└───────────────────────────────────────────────────────────────────────────────┘
```

### Key Infrastructure Principles:
1. **Zero Laptop Dependency**: The live capture and the web dashboard run 100% in the cloud. The user's laptop does NOT need to be powered on or connected for capture or dashboard serving.
2. **Google Drive Cloud Archives**: Over 5.8 GB of verified 5-year historical options data (119,932 files) is safely archived in `G:\My Drive\Volatility Lab - Historical Archives\`:
   - `backtest_1minute_5year_archive.zip` (2.08 GB)
   - `quote_archive_complete.zip` (3.24 GB)
   - `quote_quality_archive.zip` (189.2 MB)
   - `queue.sqlite` (307.8 MB)
3. **Local Cleanup Completed**: All heavy historical staging directories (`archive_to_verify_before_delete`, ~5.11 GB) were backed up and wiped from the laptop, freeing local disk space.

---

## 3. UI Design System & User Preferences (Strict Rules)

The user has explicitly defined their aesthetic and architectural preferences:

- **Style Preference: Classic & Elegant**:
  - The dashboard was previously experimented with a dense "Bloomberg Terminal" dark layout, which the user **explicitly rejected**.
  - **Always maintain the Classic, clean, uncluttered Streamlit UI**.
  - Header must remain clean: standard `st.title('Volatility Lab')` with an inline live market status pill (`● Market Live` / `○ Market Closed`).
- **Retained High-Value Visualizations**:
  - **Volatility Radar** (`render_trade_decision_radar`): Card-based tactical overview with mispricing flags, IV rank, and liquidity alerts.
  - **Term Structure & Smile Curves**: Dynamic IV vs Strike plotting for active expiries.
  - **3D Volatility Surface**: Interactive Plotly surface mesh (Strike vs Days-to-Expiry vs IV).
- **Excluded Features (Do NOT Re-add)**:
  - Do NOT re-add `🔬 Advanced Diagnostics & Secondary Research Models` expander.
  - Do NOT re-add the bulky multi-line Bloomberg executive banner.

---

## 4. Chronology of Recent Work Done

| Date / Phase | Task / Problem | Solution Implemented |
| :--- | :--- | :--- |
| **Nov 4, 2026** | Header crash (`TypeError: chg:+.2f%`) | Replaced bulky header with clean classic header and inline market status badge. |
| **Nov 4, 2026** | Volatility Radar HTML formatting glitch | Fixed 12-space CommonMark markdown indentation bug that caused cards to render as raw code boxes. |
| **Nov 4, 2026** | Flaky rate-limit test | Increased thread join timeout (`worker.join(10)`) in `test_upstox_rate_limit.py`. All 175 tests pass. |
| **Nov 5, 2026** | Local disk full & cloud dependency audit | Audited and verified all 5-year 1-minute historical data in Google Drive. Confirmed zero laptop dependency. |
| **Nov 5, 2026** | GitHub repository bloat & duplication | Removed duplicate files (`requirements.txt`, `.streamlit/secrets.toml.example`, duplicate `.cmd` scripts). Deleted legacy Zerodha broker files, obsolete test proof screenshots, and daily charge scrape dumps. |
| **Nov 5, 2026** | Database bloat in Git (`monitor.sqlite`) | Pruned expired October contracts from `monitor.sqlite`, reducing file size from **83 MB down to 27 MB** (67% reduction), well below GitHub's 50 MB warning threshold. |
| **Nov 5, 2026** | Documentation cleanup | Removed legacy files: `PROJECT_AUDIT.md`, `DEVELOPMENT_PLAN_2026-10-23.md`, `VOLATILITY_LAB_INVENTORY.json`, and `historical_dedup_report.json`. Added clean [README.md](README.md) and simplified [START HERE.md](START HERE.md). |
| **Nov 5, 2026** | Redundant tables & UI clutter removal | Removed duplicate overview table and Quote Details expander under Volatility Radar. Tucked 16-column Term Structure table into an expander. Replaced raw HV tables with metric badges in Vol Regime. Removed obsolete laptop capture warning. |

---

## 5. Repository File Map

```
D:\My First Algo\
├── .gitignore                     # Configured to ignore binary databases, locks, & ephemeral runs
├── .streamlit/
│   └── secrets.toml.example       # Example secrets for Streamlit Cloud (UPSTOX_TOKEN)
├── Volatility Lab/
│   ├── cloud/
│   │   ├── apps_script/           # Google Apps Script capture routines
│   │   │   ├── Code.gs            # Core live capture script running on Google Cloud
│   │   │   ├── appsscript.json    # Apps Script manifest
│   │   │   └── sync_config.json   # Clasp sync configuration
│   │   └── SETUP.md               # Cloud capture documentation
│   ├── data/
│   │   ├── charges/upstox/        # current.json & check_status.json (active fee schedules)
│   │   ├── upstox/                # master.json (instrument catalogue), NIFTY & BANKNIFTY expiries
│   │   ├── monitor.sqlite         # Active calibrated smile points & recent intraday snapshots
│   │   ├── dividend_yields.json   # Factsheet dividend yields (NIFTY 50 & BANKNIFTY)
│   │   ├── risk_free_rate.json    # RBI 91-day T-bill cutoff yield
│   │   └── index_contracts.csv    # NSE index contract master list
│   ├── frontend/                  # Modern TypeScript / React frontend source code
│   ├── web/                       # Compiled frontend assets served by web_server.py
│   ├── dashboard.py               # Streamlit application main entrypoint
│   ├── monitor_ui.py              # Live volatility monitor, Radar, & trade analytics UI
│   ├── smile.py                   # Spline & parametric smile fitting algorithms
│   ├── term_structure.py          # Variance term structure calculations
│   ├── vol_surface_3d.py          # Interactive 3D Plotly volatility surface
│   ├── backtest_engine.py         # Multi-leg options backtest execution engine
│   ├── backtest_ui.py             # Options backtesting interface & report builder
│   ├── futures_backtest.py        # Futures carry & calendar spread backtest engine
│   ├── broker_charges.py          # Upstox NSE transaction fee & STT calculator
│   └── test_*.py                  # 25 test files covering all modules (175 test cases)
├── app.py                         # Streamlit Cloud loader
├── streamlit_app.py               # Main Streamlit Cloud entrypoint
├── requirements.txt               # Pinned Python package dependencies
├── README.md                      # Primary repository README
├── START HERE.md                  # Quickstart guide
└── Open Dashboard.cmd             # Windows desktop launcher
```

---

## 6. How to Run & Validate

### Run Test Suite
To verify the entire analytical and backtesting engine:
```bash
python -m unittest discover -s "Volatility Lab" -p "test_*.py"
```
**Expected outcome**: `Ran 175 tests in ~26s ... OK`.

### Run Application Locally
```bash
streamlit run "Volatility Lab/dashboard.py"
```
*Or double-click `Open Dashboard.cmd`.*

---

## 7. Immediate Priorities & Guidance for Microsoft Copilot

When taking on tasks for Volatility Lab, follow these directives:

1. **Maintain Zero-Dependency Cloud Model**:
   - Never write code that assumes local files exist on a specific developer laptop path (`D:\...`).
   - Use relative paths via `Path(__file__).resolve().parent`.
   - Never add code that requires running local Windows task schedulers for data capture.
2. **Respect Git Cleanliness**:
   - Never commit raw `.json.gz` market tick archives, test dump folders, or databases > 50 MB to GitHub.
   - All ephemeral outputs belong in `.gitignore`.
3. **Preserve Test Integrity**:
   - Any refactor or feature addition must maintain 100% pass rate across all 175 tests in `test_*.py`.
4. **Market Conventions**:
   - Timezone: `Asia/Kolkata` (`IST`, UTC+5:30).
   - NSE Trading Hours: 09:15 to 15:30 IST.
   - Indices: NIFTY 50 (lot size: 25 / 75 dated) and BANKNIFTY (lot size: 15 / 30 dated).
   - Interest Rates: Annualized RBI 91-day T-Bill cutoff rate.
