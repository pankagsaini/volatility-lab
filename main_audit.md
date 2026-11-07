# Comprehensive Technical, Quantitative, and Architectural Audit of Volatility Lab

**Repository Audited:** [pankagsaini/volatility-lab](https://github.com/pankagsaini/volatility-lab) (`master` & `main` branches, Commit `6fb5e26`)

**Cloud Capture Engine Audited:** [Volatility Lab - Upstox Capture (Google Apps Script)](https://script.google.com/d/1_nNJrwJaGMmtctFJYhZudef3rCEtRiZRM9fZqn8BHnI-fnAMupLNqk-A/edit?usp=drivesdk)

**Google Drive Archives Audited:** [Volatility Lab - Historical Archives](https://drive.google.com/drive/folders/1RAUFULprn7AhnAJEp1Afa1P_bkVSPAAt) & [Volatility Lab - private Upstox archive](https://drive.google.com/drive/folders/1SCNVxgK3WAbEyQDRzbb-t0PsPaq477i1)

**Live Deployment Audited:** [Streamlit Community Cloud App](https://pankagsaini-volatility-lab-streamlit-app-nqnqoa.streamlit.app/)

**Audit Date:** November 5, 2026

## 1. Executive Assessment & System Architecture Overview

Volatility Lab is an institutional-grade quantitative volatility research, live market monitoring, and multi-leg derivatives backtesting workstation engineered for Indian equity index derivatives (**NIFTY 50** and **NIFTY BANK**) on the National Stock Exchange of India (NSE). The platform integrates a serverless Google Apps Script cloud collector, a local Google Drive desktop mirror sync engine, a 5-year 1-minute historical options and futures SQLite/gzip archive (~204.88 million minute candle rows across 91,785+ completed jobs), a statutory dated brokerage/tax calculator, and interactive visualization interfaces.

Across all 12 root files, 71 files and 5 subdirectories inside [`Volatility Lab/`](https://github.com/pankagsaini/volatility-lab/tree/master/Volatility%20Lab), and the live Google Drive Apps Script deployment, the codebase demonstrates strong foundational engineering discipline: strict point-in-time (`AvailableAt`) lookahead guards in [backtest_signals.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_signals.py) and [har_research.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/har_research.py), refusal to fabricate historical implied volatility or bid-ask spreads from OHLC candles ([IV DATA POLICY.md](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/IV%20DATA%20POLICY.md)), dated NSE contract lot sizes, and 4-regime statutory fee tables in [broker_charges.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/broker_charges.py).

At the same time, rapid iterative expansion has introduced **23 Critical/High-severity defects**, **26 Medium/Low-severity edge cases**, and **25 duplicated/redundant code clusters** across five architectural layers:

| Audit Track | Files | Crit/High | Med/Low | Redund. | Headline Finding | 
| ----- | ----- | ----- | ----- | ----- | ----- | 
| **1. Quantitative Math & Forecasting** | 17 | 5 | 6 | 6 | Session-to-calendar variance bridge in `expiry_forecast.py` distorts forecasted RV by **+22.1% mid-week** and **-29.5% over weekends**; `relative_value.py:observe()` fails silently due to reversed arguments. | 
| **2. Cloud Capture & Data Sync Pipelines** | 21 | 6 | 5 | 6 | Live Drive `Code.gs` (573 lines) has drifted from GitHub `cloud/apps_script/Code.gs` (887 lines); `futures_backtest_download.py` omits `'data'` key on save, crashing with `KeyError: 'data'` on cache hits; `background_refresh.py:is_stale()` returns `True` unconditionally. | 
| **3. Options & Futures Backtesting Engines** | 18 | 4 | 5 | 4 | `backtest_jobs._options()` passes positional arguments in the wrong order to `strategy_backtest.simulate()`; 1-minute inner monitoring loop recomputes `broker_charges.charge()` 375x/day per leg; missing-minute skips drop already-entered trades. | 
| **4. UI/UX, Streamlit Cloud & FastAPI Web** | 23 | 5 | 6 | 5 | `dashboard.py` has invalid CSS (`#20e42`, `div[div[data-testid="stRadio"]`); `streamlit_app.py` runs `compile()` + `exec()` on every 2s fragment tick; `refresh_policy.py` evaluates UTC hours instead of IST on Streamlit Cloud. | 
| **5. Repository Structure, Scripts & Tests** | 37 | 3 | 4 | 4 | Three overlapping UI stacks (`dashboard.py`, `restored_lab.py`, `web_server.py` + `frontend/`) and four process launchers coexist; unit tests mock out call signatures that fail in integration. | 

## 2. Quantitative Mathematics, Volatility Surface & Forecasting Audit

### 2.1 Critical Mathematical & Quantitative Defects

1. **Critical Session-to-Calendar Variance Bridge Distortion in [expiry_forecast.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/expiry_forecast.py) (`expiry_forecast`, lines 539, 574–583)**:

   * **Mechanism**: In [`vol_lib.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/vol_lib.py) and [forecast.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/forecast.py), daily variance σ²_day is estimated over trading sessions and annualized by $245$ sessions/year (σ²_ann = 245 × σ²_day). In [expiry_forecast.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/expiry_forecast.py), the code recovers per-session variance via `ewma = rv_snapshot(training)['ewma_next_session_vol']**2 / 245` and `variances = (path['Daily conditional vol %'].to_numpy()/100)**2 / 245`, sums over N_sessions = weights.sum(), and then divides by calendar years `years = (end - now).total_seconds() / (365 * 86400)` (D_cal / 365):

     ```
     years = (end-now).total_seconds()/(365*86400)
     ewma = rv_snapshot(training)['ewma_next_session_vol']**2/245
     result['EWMA expiry RV'] = float(100*np.sqrt(ewma*weights.sum()/years))
     result['GARCH expiry RV'] = float(100*np.sqrt(np.dot(variances,weights)/years))
     
     ```

   * **Quantitative Impact**: Dividing cumulative trading-session variance by calendar years D_cal / 365 instead of trading years N_sessions / 245 multiplies true annualized volatility by √((365 / 245) × (N_sessions / D_cal)). On a Monday close for a Friday expiry (N_sessions = 4, D_cal = 4), forecasted RV is **overstated by +22.06%** (√(365 / 245) = 1.2206). On a Friday close for a Monday expiry (N_sessions = 1, D_cal = 3), forecasted RV is **crushed by -29.53%** (√(365 / (245 × 3)) = 0.7047).

   * **Solution**: Annualize the weighted mean session variance consistently on the 245-session trading clock:

     ```
     mean_session_var_ewma = ewma
     mean_session_var_garch = float(np.dot(variances, weights) / weights.sum())
     result['EWMA expiry RV'] = float(100.0 * np.sqrt(mean_session_var_ewma * 245.0))
     result['GARCH expiry RV'] = float(100.0 * np.sqrt(mean_session_var_garch * 245.0))
     
     ```

2. **Silent Broken Call Signature in [relative_value.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/relative_value.py) (`observe`, lines 68–73)**:

   * **Mechanism**: `observe()` calls `matched = expiry_forecast(quote, history_frame)` with only 2 arguments in reversed order, whereas `expiry_forecast(frame, expiry, as_of, iv=None)` in [expiry_forecast.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/expiry_forecast.py) requires 3 positional arguments `(frame, expiry, as_of)`. Furthermore, `observe()` looks up `matched.get('Forecast')`, which does not exist in `expiry_forecast`'s output dict (`'GARCH expiry RV'`, `'EWMA expiry RV'`). Because lines 68–73 catch `(OSError, ValueError, KeyError, TypeError)`, this `TypeError` is silently swallowed on every observation.

   * **Solution**: Call `matched = expiry_forecast(history_frame, quote['Expiry'], quote['As of'], quote.get('IV'))` and extract `matched.get('GARCH expiry RV')` and `matched.get('EWMA expiry RV')`.

3. **Hardcoded `2026` Year Boundary Crash in [expiry_forecast.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/expiry_forecast.py) (`sessions`, line 803)**:

   * **Mechanism**: `if start.year != 2026 or end.year != 2026: raise ValueError('Verified trading calendar currently covers 2026 only.')` crashes on any late-2026 option expiring in January/March 2027, any early-2026 check where the last trading day was `2025-12-31`, and even on `2026-12-31` itself when `elapsed = sessions(last + timedelta(days=1), cutoff)` passes `start = 2027-01-01 > end = 2026-12-31`.

   * **Solution**: Add `if start > end: return []` as the first line of `sessions(start, end)` and validate against dynamic supported years in `market_calendar.py`.

4. **Conflicting ATM IV & 25-Delta Butterfly Definitions Across [option_math.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/option_math.py) and [smile.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/smile.py)**:

   * **Mechanism**: `skew_metrics(chain)` in [option_math.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/option_math.py) defines the ATM anchor as `0.5 * (delta_iv(chain, 'PE', 0.5) + delta_iv(chain, 'CE', 0.5))` (50-delta proxy), whereas `smile_points(chain, strikes, spot)` in [smile.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/smile.py) defines ATM as the average of Call and Put IV at the listed strike closest to spot (K ≈ S). Because positive carry makes spot call delta > 0.50 at K = S, the two modules report conflicting ATM IV and 25D Butterfly values for the same chain. Additionally, [option_math.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/option_math.py) lacks an arbitrage-bounded BSM implied volatility root-finder (`scipy.optimize.brentq`) and analytical higher-order Greeks (`gamma`, `vega`, `theta`, `vanna`, `volga`).

5. **Missing Forward Implied Volatility & Calendar Arbitrage Flag in [term_structure.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/term_structure.py)**:

   * **Mechanism**: `term_structure(rows, symbol)` computes only the level spread `following['IV'] - current['IV']` and raises `ValueError('Duplicate expiry in snapshot.')` if duplicate rows exist. It omits forward implied volatility σ_fwd(T₁, T₂) = √((σ₂² T₂ - σ₁² T₁) / (T₂ - T₁)) and calendar-arbitrage detection (σ₂² T₂ < σ₁² T₁).

6. **Additional Quantitative Edge Cases**:

   * **NSE Market Close Timestamp (`15:40` vs. `15:30` IST)**: [expiry_forecast.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/expiry_forecast.py) uses `time(15, 40)` not only as a post-close data-availability cutoff but also as `closing = datetime.combine(date, time(15, 40), tzinfo=IST)` for intraday session fraction weighting (`385` minutes instead of NSE's actual `375`-minute session `09:15–15:30 IST`).

   * **Synthetic Future Discounting ([synthetic_future.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/synthetic_future.py))**: `calculate()` computes `synth = stk + c - p` using raw `ltp` rather than bid-ask midpoints and omits the discount factor exp(rT) in F = K + exp(rT)(C - P).

   * **3D Vol Surface DTE & Skew Label Bug ([vol_surface_3d.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/vol_surface_3d.py))**: `load_expiry_chains` computes DTE against wall-clock `datetime.now(IST).date()` instead of the snapshot's saved `valuation` timestamp; `extract_surface_mesh` hardcodes `strike_step = 50.0 if ref_spot < 35000 else 100.0`; and `front_skew = float(z_matrix[0, left_idx] - z_matrix[0, right_idx])` at array indices `0.2 * len` and `0.8 * len` is labeled `"Front Skew (96%-104%)"` even when the user changes `moneyness_range_pct` away from `8.0%`.

   * **Missing Range Estimators & EWMA Cold-Start Bias ([`vol_lib.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/vol_lib.py))**: Parkinson (1980) and Garman-Klass (1980) estimators are absent, Rogers-Satchell is computed internally but not exposed, and `rv_snapshot` initializes `ewma_var = float(returns.iloc[0]**2)` from a single day's squared return instead of a warm-start sample variance mean.

### 2.2 Redundant Math Functions & Inefficient Loops

| Redundancy / Loop Bottleneck | Modules Involved | Impact & Remediation | 
| ----- | ----- | ----- | 
| **4x Redundant `validate_ohlc(frame)` Calls per Forecast** | `expiry_forecast.py` -> `vol_lib.py` -> `forecast.py` | `term_structure_forecast` validates OHLC, then calls `expiry_forecast` (validates OHLC #2), which calls `rv_snapshot` (validates OHLC #3) and `fit_garch` (validates OHLC #4). Tag validated DataFrames with `df.attrs['ohlc_validated'] = True` to skip redundant deep copies and sorts. | 
| **Duplicate Yang-Zhang, Rogers-Satchell & EWMA Implementations** | `vol_lib.py` (`rv_snapshot`), `forecast.py` (`rolling_rv`), `volatility_regime.py` (`regime_summary`) | `rv_snapshot` and `rolling_rv` duplicate the exact same Yang-Zhang, Rogers-Satchell, Close-to-Close, and EWMA equations. Consolidate into a single vectorized `rolling_rv_suite()` in `vol_lib.py` and make `rv_snapshot` return `.iloc[-1]`. | 
| **Python `for` Loop for EWMA Recursion** | `vol_lib.py` (`rv_snapshot`, lines 44–46) | Loops element-by-element in Python (`for ret in returns.iloc[1:]:`). Replace with vectorized `returns.pow(2).ewm(alpha=1-lam, adjust=False).mean()`. | 
| **Row-by-Row `.apply(..., axis=1)` Timestamp Loops** | `expiry_forecast.py` (`eligible_history`), `relative_value.py` (`maturity_stats`), `har_research.py` (`read_rv`) | `eligible_history` calls `forecast_restriction` via `axis=1` per row; `maturity_stats` buckets DTE via Python `apply`. Replace with vectorized `pd.to_datetime(..., utc=True).dt.tz_convert(IST)` and `pd.cut()`. | 
| **Repeated Rolling Means & OLS Matrix Rebuilds in Walk-Forward** | `har_research.py` (`evaluate`), `experimental_models.py` (`har_forecast`) | Recomputes 5-day and 22-day rolling averages 4 times per origin date (once per horizon h ∈ (5, 10, 20, 30)). Precompute `RV_d`, `RV_w`, `RV_m` once and solve all 4 horizons simultaneously via multi-target `np.linalg.lstsq(X, Y)`. | 

## 3. Cloud Capture, Upstox Ingestion & Google Drive Sync Audit

### 3.1 Code Drift Between GitHub (`cloud/apps_script/Code.gs`) and Live Google Drive (`Volatility Lab - Upstox Capture`)

A direct line-by-line inspection of your live Google Drive Apps Script ([Volatility Lab - Upstox Capture](https://script.google.com/d/1_nNJrwJaGMmtctFJYhZudef3rCEtRiZRM9fZqn8BHnI-fnAMupLNqk-A/edit?usp=drivesdk), 573 lines, modified November 5, 2026) against [Volatility Lab/cloud/apps_script/Code.gs](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/cloud/apps_script/Code.gs) (~887 lines) reveals major production drift:

1. **`failureWatchdog()` and `weeklyCleanup()` Deleted in Live Drive `Code.gs`**:

   * The GitHub version defines `failureWatchdog()` (to create/resolve Google Calendar alerts) and `weeklyCleanup()` (to purge Drive files older than 14 days after local receipt verification).

   * In the live Google Drive script, both functions were completely removed when switching to 1-minute snapshots on your 5TB Drive plan, yet `installIntradaySchedule()` on line 326 still filters `t.getHandlerFunction() === 'weeklyCleanup'`, and [`Repair Cloud Sync.ps1`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/Repair%20Cloud%20Sync.ps1) still enforces the 8-day/14-day receipt check (`google_archive_sync.py --verify-receipt`).

2. **Weekend Execution of `reconcilePriorSession()` (`Code.gs`, lines 351–359)**:

   * In `scheduledCapture()`, the pre-market reconciliation window check (`if (mins >= 420 && mins < 540) { reconcilePriorSession(); return; }`) is placed **above** the weekend guard (`if (weekday > 5) { return; }`). As a result, `reconcilePriorSession()` executes every minute between 07:00 and 09:00 IST on Saturdays and Sundays.

3. **Artificial Runtime Budget Inflation & 90-Minute Quota Exhaustion (`Code.gs`, lines 405–448)**:

   * `scheduledCapture()` runs every 1 minute from 09:00 to 16:00 IST (420 triggers/day) plus EOD and reconciliation windows. In the `finally` block (line 446), `b.ms += Date.now() - started + 1000;` adds an artificial `+1000 ms` penalty to every single execution—burning **7 full minutes** of runtime budget per day on padding alone and causing the 75-minute cap (`budget.ms >= 75 * 60000`) to halt live capture before the 15:30 IST market close on busy days.

4. **HTTP 401/403 Retry Storm on Expired `UPSTOX_TOKEN` (`Code.gs`, lines 211–221)**:

   * Upstox access tokens expire daily at 03:30 IST. When `UPSTOX_TOKEN` expires, `apiAll_(requests)` receives `Broker HTTP 401` on all ~15 batch requests, and the fallback loop (`if (res.error) { data = api_(res.path, res.params); }`) immediately retries every single request sequentially, doubling failed calls and burning runtime budget.

5. **Unauthenticated `doGet(e)` Web App Exposure (`appsscript.json` & `Code.gs`, lines 513–572)**:

   * `appsscript.json` sets `"access": "ANYONE_ANONYMOUS"`, and `doGet(e)` serves raw market snapshots and internal `ScriptProperties` without checking an API key parameter. Furthermore, when `LATEST_SNAPSHOT_FILE_ID` is absent, `doGet(e)` scans only the first 60 files returned by `folder.getFiles()` (`while (files.hasNext() && count < 60)`), which is not ordered newest-first in Google Drive and returns stale snapshots.

### 3.2 Background Downloader Hangs, `"running"` Lockouts & Rate-Limit Collisions

1. **Critical Cache Crash (`KeyError: 'data'`) in [futures_backtest_download.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/futures_backtest_download.py) (`fetch`, lines 12–20)**:

   * On line 18, `fetch()` saves new archives as `body = {'provider': provider, 'path': path, 'params': params, 'meta': meta, 'retrieved_at': ...}` **without including `'data': data`**. On the next run, when `if file.exists():` is true on line 15, `return json.load(f)['data']` immediately crashes with `KeyError: 'data'`.

   * Additionally, line 31 calls `/v2/historical-candle/intraday/` (which is Upstox's current-day-only endpoint) with spot `INDICES` keys instead of dated futures instrument keys.

2. **Why `backtest_download.py` and `futures_backtest_download.py` Get Stuck in `"running"` Status**:

   * In [backtest_download.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_download.py) (lines 116–117), `status(c, 'running', current=...)` is written to `status.json` immediately before `data = future.result()`. If the console window is closed, paused by Windows QuickEdit mode, or killed via `TerminateProcess` (`KeyboardInterrupt` / `SystemExit` inherit from `BaseException`, not `Exception`), the `except Exception:` block never runs and `status.json` remains permanently stuck at `"status": "running"`.

   * Furthermore, when a single job fails 3 times in 45 seconds (`attempts >= 2` on line 141, because `next_jobs()` always picks `ORDER BY rowid LIMIT 1`), `backtest_download.py` sets `paused_error` and **terminates the entire worker loop (`return`)**, halting all 4,900+ remaining pending jobs.

3. **Why [background_refresh.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/background_refresh.py) Runs Non-Stop Redundant Polling Loops**:

   * In [background_refresh.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/background_refresh.py), `is_stale(state, key)` unconditionally returns `True`, while `status()` runs an `@st.fragment(run_every=2)` loop every 2 seconds. Moreover, both `take()` and `poll()` call `_collect()`, which executes `del state[slot]` non-atomically (risking `KeyError` race conditions).

4. **Cross-Process SQLite Lock Contention in [google_archive_sync.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/google_archive_sync.py)**:

   * The Windows Scheduled Task (`Repair Cloud Sync.ps1`) runs `google_archive_sync.py --loop` every 10 minutes in a separate OS process while the Streamlit app also triggers `ensure_sync()`. Because `_guard = threading.Lock()` only synchronizes threads inside a single Python process, concurrent OS processes collide on `data/google_archive/monitor.sqlite` unless wrapped in `collector_support.data_lock` with `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=30000;`.

## 4. Options & Futures Backtesting Engines & Execution Realism Audit

### 4.1 Critical Backtest Engine Bugs & Bottlenecks

1. **Positional Argument Mismatch in [backtest_jobs.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_jobs.py) (`_options`) Calling [strategy_backtest.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/strategy_backtest.py) (`simulate`)**:

   * **Bug**: `backtest_jobs._options(task)` calls `simulate(contracts, series, INDICES[symbol], settings, signals)` (5 positional arguments, with `contracts` first and `settings` fourth). However, `strategy_backtest.simulate(s, contracts, ticks, signals, callback=None)` expects the settings dictionary `s` as the **first** argument (`s, contracts, ticks, signals`).

   * **Solution**: Update `backtest_jobs._options(task)` to call `simulate(settings, contracts, series, signals)`.

2. **Intraday Missing-Bar Skip Creates Survivorship Bias in [strategy_backtest.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/strategy_backtest.py) (`simulate`, lines 140–171)**:

   * **Bug**: During minute-by-minute stop/target monitoring (`while cursor < exit_time`), if any option leg has a single 1-minute bar without trades (`not valid_price(b, 3)`), `simulate()` calls `record_skip(skips, day, 'Missing monitoring minute; risk path unknown')` and completely erases the trade—even though the trade already entered at `09:20`! Discarding entered trades whenever an OTM wing has a quiet minute introduces survivorship bias.

   * **Solution**: Carry forward the last known close price (`last_valid_close`) for non-fill mark-to-market monitoring checks while still requiring a valid traded bar (`volume > 0`) for the actual exit fill (advancing to the next traded minute if the trigger minute's next open has zero volume).

3. **375x Per-Day Redundant Fee Calculation Inside the 1-Minute Monitoring Loop ([strategy_backtest.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/strategy_backtest.py))**:

   * **Bottleneck**: Inside the `while cursor < exit_time` loop (up to 375 minutes per session), `simulate()` calls `broker_charges.charge()` for every leg at every single minute just to check `net <= -s['stop']` or `net >= s['target']`.

   * **Solution**: Pre-calculate entry charges once at trade entry and use a fast inline linear multiplier for exit charges during monitoring, invoking the full `broker_charges.charge()` breakdown only once when the exit is actually triggered.

4. **Uncached Gzip Decompression in [backtest_engine.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_engine.py) (`load`) & Duplicate Simulation Logic**:

   * **Bottleneck**: Unlike [futures_backtest.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/futures_backtest.py) (which uses a thread-safe `OrderedDict` LRU cache `_FILE_CACHE` capped at 2,048 files / 75,000 rows), `backtest_engine.load()` unzips `.json.gz` files from scratch on every run. Furthermore, `backtest_engine.simulate()` duplicates the prior-minute close lookup, slippage math, and fee aggregation of `strategy_backtest.simulate()`.

5. **SPAN Margin Floor & Synchronous Fee Scraping ([strategy_backtest.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/strategy_backtest.py), [broker_charges.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/broker_charges.py), [backtest_jobs.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_jobs.py))**:

   * `estimate_exchange_margin()` evaluates intrinsic payoff across `[-15%, -psr, -psr/2, 0, +psr/2, +psr, +15%]` plus strikes, underestimating margin on short strangles outside ±15% because it lacks an NSE Short Option Minimum (SOM, ~5% of underlying notional) floor and a defined-risk Iron Condor spread margin floor (~1.5%–2% of notional).

   * In [backtest_jobs.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_jobs.py), `fee_status()` and `inventory_status()` share the same `_fee_lock`, so a 12-second HTTP timeout when `broker_charges.refresh()` scrapes `https://upstox.com/brokerage-charges/` freezes inventory status checks in the UI.

## 5. UI/UX Appearance, Streamlit Cloud & FastAPI Web Server Audit

### 5.1 Visual, CSS, and Streamlit Cloud Defects

1. **Invalid CSS Hex (`#20e42`) and Broken Radio Selector (`div[div[...]`) in [dashboard.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/dashboard.py) (lines 85–112)**:

   * `[data-testid="stPlotlyChart"] { border: 1px solid #20e42; ... }` uses a 5-digit hex code (`#20e42` instead of `#243348`), so browsers drop the border on all Plotly charts.

   * `div[div[data-testid="stRadio"] label:has(input:checked)` has a doubled unclosed `[` bracket, preventing selected radio buttons from showing their emerald highlight.

   * Market status badges (`NSE LIVE`, `PRE-OPEN`, `CLOSED`) use glaring light-mode pastel backgrounds (`#ecfdf5`, `#fffbe6`, `#f3f4f6`) against the `#0c1321` dark theme.

2. **Per-Rerun `compile()` + `exec()` Overhead in [`streamlit_app.py`](https://github.com/pankagsaini/volatility-lab/blob/master/streamlit_app.py) & Single-Load Bug in [app.py](https://github.com/pankagsaini/volatility-lab/blob/master/app.py)**:

   * Because the subfolder is named `Volatility Lab/` (with a space), `streamlit_app.py` reads `dashboard.py` from disk and runs `compile()` + `exec()` on every single 2-second fragment rerun. Meanwhile, `app.py` (`import streamlit_app`) goes blank on its second rerun because Python does not re-run cached module imports.

3. **UTC vs. IST Timezone Bug on Streamlit Cloud in [refresh_policy.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/refresh_policy.py) (`refresh_due`)**:

   * `refresh_due(now, result, elapsed)` checks `now.hour * 60 + now.minute` against IST market minutes (`555` to `940`) without first converting `now` to `ZoneInfo("Asia/Kolkata")`. On Streamlit Community Cloud (where container system time is UTC), the app misinterprets UTC hours as IST and suppresses live refreshes during the morning NSE trading session!

4. **Missing `folderWatchBlacklist` in Root [.streamlit/config.toml](https://github.com/pankagsaini/volatility-lab/blob/master/.streamlit/config.toml)**:

   * `folderWatchBlacklist = [".runtime", ".dependencies", "data", "logs", "__pycache__", ".auth"]` is present in `Volatility Lab/.streamlit/config.toml` but missing from the root `.streamlit/config.toml`, causing SQLite/JSON writes in `data/` to trigger full Streamlit file-watcher reruns.

5. **Three Parallel UI Stacks & Hardcoded `.dependencies` Path in [web_server.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/web_server.py)**:

   * Term Structure, Skew, and 3D Vol Surface are implemented three separate times across [monitor_ui.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/monitor_ui.py), [restored_lab.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/restored_lab.py), and [web_data.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/web_data.py) + [frontend/](https://github.com/pankagsaini/volatility-lab/tree/master/Volatility%20Lab/frontend).

   * `web_server.py` hardcodes `LOCAL_PLOTLY = ROOT / '.dependencies/plotly/package_data/plotly.min.js'`, which breaks outside the local Windows `.dependencies` bundle.

## 6. Prioritized Remediation Roadmap & Concrete Code Fixes

### Priority 1 — Immediate Bug & Data-Integrity Fixes (Zero Architectural Disruption)

1. **Fix `expiry_forecast.py` Variance Bridge & 2026 Year Guard**: Replace `/ years` with `* 245.0 / weights.sum()` in `expiry_forecast()`, add `if start > end: return []` at the top of `sessions()`, and change the trading session close from `time(15, 40)` to `time(15, 30)` (keeping `15:40` strictly as the EOD data-ingestion cutoff).

2. **Fix `relative_value.py:observe()` & `backtest_jobs.py:_options()` Call Signatures**: Pass `(history_frame, quote['Expiry'], quote['As of'], quote.get('IV'))` to `expiry_forecast()` in `relative_value.py`, and pass `(settings, contracts, series, signals)` to `strategy_backtest.simulate()` in `backtest_jobs.py`.

3. **Fix `futures_backtest_download.py` Cache Crash & `backtest_download.py` 3-Strike Exit**: Include `'data': data` in the saved JSON payload on line 18 of `futures_backtest_download.py`; in `backtest_download.py`, catch `BaseException` to update `status.json` cleanly on exit and mark individual 3-strike jobs as `'failed'` (`UPDATE jobs SET state='failed'`) while continuing the worker loop for the remaining pending jobs.

4. **Fix `refresh_policy.py` IST Conversion & `background_refresh.py` Staleness Loop**: Convert `now` to `ZoneInfo('Asia/Kolkata')` at the top of `refresh_due()`, implement a monotonic TTL check in `background_refresh.is_stale()`, and use `state.pop(slot, None)` in `_collect()`.

5. **Sync Live Drive `Code.gs` with GitHub `cloud/apps_script/Code.gs`**: Move `if (weekday > 5) return;` above `reconcilePriorSession()`, remove the `+ 1000` ms runtime budget padding, skip sequential retries on HTTP `401`/`403`, and protect `doGet(e)` with a `WEBHOOK_KEY` Script Property.

### Priority 2 — UI/UX Polish & Streamlit Cloud Optimization

1. **Repair `dashboard.py` CSS & Entrypoint**: Fix `#20e42` -> `#243348` and `div[data-testid="stRadio"] label:has(input:checked)`, switch session pills to dark-mode translucent surfaces, wrap `dashboard.py` in `def main():`, call `dashboard.main()` directly from `streamlit_app.py` and `app.py` (eliminating `exec()`), and copy `folderWatchBlacklist` into the root `.streamlit/config.toml`.

2. **Enhance Plotly Ergonomics in `monitor_ui.py`, `term_structure.py`, and `vol_surface_3d.py`**: Add Forward Implied Volatility σ_fwd(T₁, T₂) and calendar-arbitrage flags to `term_structure.py`; plot DTE proportionally on the x-axis with a secondary axis for Synthetic Futures basis; replace crowded `mode='lines+markers+text'` static labels on the smile chart with clean hover templates; and interpolate front skew in `vol_surface_3d.py` at exact `96%` and `104%` moneyness.

### Priority 3 — Code Consolidation & Vectorization

1. **Unify Math & Realized Volatility Modules**: Consolidate `rolling_rv` (`forecast.py`) and `rv_snapshot` (`vol_lib.py`) into a single vectorized `rolling_rv_suite()` in `vol_lib.py` (adding Parkinson, Garman-Klass, and Rogers-Satchell, plus vectorized `.ewm()`), and add a `scipy.optimize.brentq` IV solver and full analytical Greeks (`bsm_greeks`) to `option_math.py`.

2. **Speed Up 1-Minute Options Backtests**: Move per-leg `broker_charges.charge()` out of the 375-minute intraday monitoring loop in `strategy_backtest.py`, carry forward `last_valid_close` during non-fill monitoring minutes, and share the `_FILE_CACHE` LRU mechanism from `futures_backtest.py` with the options loader.

## 7. References & Audited Source Links

* **GitHub Repository Root:** [pankagsaini/volatility-lab](https://github.com/pankagsaini/volatility-lab)

* **Architecture & Handover Docs:** [COPILOT_CONTEXT.md](https://github.com/pankagsaini/volatility-lab/blob/master/COPILOT_CONTEXT.md), [README.md](https://github.com/pankagsaini/volatility-lab/blob/master/README.md), [START HERE.md](https://github.com/pankagsaini/volatility-lab/blob/master/START%20HERE.md), [IV DATA POLICY.md](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/IV%20DATA%20POLICY.md)

* **Core Math, Surface & Forecasting Modules:** [vol_lib.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/vol_lib.py), [option_math.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/option_math.py), [term_structure.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/term_structure.py), [smile.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/smile.py), [vol_surface_3d.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/vol_surface_3d.py), [forecast.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/forecast.py), [expiry_forecast.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/expiry_forecast.py), [forecast_validation.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/forecast_validation.py), [har_research.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/har_research.py), [experimental_models.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/experimental_models.py), [relative_value.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/relative_value.py), [volatility_regime.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/volatility_regime.py), [synthetic_future.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/synthetic_future.py)

* **Cloud Capture & Data Ingestion Modules:** [Google Drive Apps Script: Volatility Lab - Upstox Capture](https://script.google.com/d/1_nNJrwJaGMmtctFJYhZudef3rCEtRiZRM9fZqn8BHnI-fnAMupLNqk-A/edit?usp=drivesdk), [cloud/apps_script/Code.gs](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/cloud/apps_script/Code.gs), [google_archive_sync.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/google_archive_sync.py), [upstox_data.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/upstox_data.py), [upstox_archive.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/upstox_archive.py), [upstox_rate_limit.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/upstox_rate_limit.py), [backtest_download.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_download.py), [futures_backtest_download.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/futures_backtest_download.py)

* **Backtest & Execution Modules:** [strategy_backtest.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/strategy_backtest.py), [backtest_engine.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_engine.py), [backtest_jobs.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_jobs.py), [backtest_signals.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_signals.py), [broker_charges.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/broker_charges.py), [futures_backtest.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/futures_backtest.py)

* **UI, Caching & Web Modules:** [streamlit_app.py](https://github.com/pankagsaini/volatility-lab/blob/master/streamlit_app.py), [dashboard.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/dashboard.py), [monitor_ui.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/monitor_ui.py), [background_refresh.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/background_refresh.py), [refresh_policy.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/refresh_policy.py), [restored_lab.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/restored_lab.py), [web_server.py](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/web_server.py), [Live Streamlit App](https://pankagsaini-volatility-lab-streamlit-app-nqnqoa.streamlit.app/)


## 7. Phase 2 Master Audit: 5-Subagent 1-Year Autonomous Offline Audit (`v9.0` Tracks 1–5)

To guarantee zero-touch reliability while you go offline for 1 year (`2026-11-07` through `2027-11-07` and into Leap Year `2028`), five specialized audit subagents inspected every module in [`pankagsaini/volatility-lab`](https://github.com/pankagsaini/volatility-lab), the [live Streamlit deployment](https://pankagsaini-volatility-lab-streamlit-app-nqnqoa.streamlit.app/), the [Google Apps Script collector (`Code.gs`)](https://script.google.com/d/1_nNJrwJaGMmtctFJYhZudef3rCEtRiZRM9fZqn8BHnI-fnAMupLNqk-A/edit?usp=drivesdk), and the [v9.0 Master Offline Bundle](https://drive.google.com/drive/folders/16LTFIj6zA6WTobam2YIVpA6r0F5BnlRH).

### 7.1 Track 1 — Quantitative Math, Volatility Surface, Smile, Term Structure & 2026–2028 Calendar Audit

| Module Audited | 1-Year Offline & Quantitative Edge Cases Identified | Exact Remediation Implemented in `v9.0` | Verification |
| :--- | :--- | :--- | :--- |
| [`market_calendar.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/market_calendar.py) | Missing 5 weekday NSE holidays in 2027 (including Thursday `2027-04-15` Shri Ram Navami, which rolls the weekly expiry across Wednesday `2027-04-14` Ambedkar Jayanti to Tuesday `2027-04-13`) and 9 weekday holidays in Leap Year 2028 (including Thursday `2028-11-02` Guru Nanak Jayanti -> Wednesday `2028-11-01`). | Added all verified 2026 ([NSE Circular FAOP71777](https://nsearchives.nseindia.com/content/circulars/FAOP71777.pdf)), 2027 ([2027 NSE Holiday Schedule](https://instapdf.in/nse-holiday-list-2027/) & [Drik Panchang 2027](https://www.drikpanchang.com/calendars/indian/indiancalendar.html?year=2027)), and 2028 ([2028 NSE Market Holidays](https://www.calendarlabs.com/nse-market-holidays-2028/) & [Drik Panchang 2028](https://www.drikpanchang.com/calendars/indian/indiancalendar.html?year=2028)) weekday holidays and removed weekend false positives (`2027-08-15`, `2027-09-04`, `2027-11-14` per [Time and Date 2027](https://www.timeanddate.com/holidays/india/2027)). | `test_v9_market_calendar_2027_2028_holidays_and_horizon_265` (`OK`) |
| [`expiry_forecast.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/expiry_forecast.py) & [`forecast.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/forecast.py) | (1) Muhurat trading sessions (`2026-11-08` Sunday, `2027-10-29` Friday, `2028-10-17` Tuesday) caused `ValueError: Latest completed session is missing` on the following morning if the 1-hour evening candle was not captured; (2) `len(dates) > 245` horizon cap crashed 1-year far-month NIFTY options across 250–253 session calendar years. | Made `SPECIAL_SESSIONS` optional in `last_trading_day_on_or_before(d, include_special=False)`, tolerated up to a 1-session EOD lag in `expiry_forecast()`, and raised `HORIZON_CAP` from `245` to `265` sessions in both `expiry_forecast.py` and `forecast.py`. | `test_v8_year_long_offline_end_to_end_audit` & `test_v9_market_calendar...` (`OK`) |
| [`option_math.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/option_math.py) & [`vol_lib.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/vol_lib.py) | `option_math.bsm_greeks()` raised `ValueError` at `years <= 0`, rejected mixed `np.array(['CE', 'PE'])` vectors, and lacked `bsm_greeks_vec`. | Added `bsm_greeks_vec()` in `option_math.py` with `T = np.maximum(years, 1e-9)`, mixed `['CE', 'PE']` vector support, and `iv=` / `div_yield=` keyword aliases. | `test_v9_option_math_t_zero_and_mixed_kind_vec` (`OK`) |
| [`smile.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/smile.py), [`term_structure.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/term_structure.py) & [`relative_value.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/relative_value.py) | (1) `smile.load_latest_smile()` returned rows with all-`None` IVs after an RBI rate or NSE dividend config-hash change instead of falling back; (2) `term_structure.py` used `15:40 IST` instead of `15:30 IST`; (3) `relative_value.save()` used `allow_nan=True`, writing non-standard `NaN` JSON tokens. | Checked `has_usable_points(df)` before returning in `load_latest_smile()`; aligned `term_structure.py` cutoff to `time(15, 30)`; sanitized non-finite floats before `json.dumps(..., allow_nan=False)`. | `test_smile_and_reversal_formatting_and_empty_strikes` (`OK`) |

### 7.2 Track 2 — Autonomous 365-Day Cloud & Local Ingestion, SQLite Storage & Auth Lifecycle Audit

* **Empirical 1-Year Storage & Quota Measurements**:
  * **Single 1-Minute Upstox Capture ([`2026-11-06_snapshot_152142_complete.json.gz`](https://drive.google.com/file/d/1pTR0PUyaHvYPZlCv3wLi4DvAzTOfKfjH/view?usp=drivesdk))**: `127,022 B` compressed (`936,748 B` raw JSON across `11` records and `1,327` option strikes).
  * **Annual Google Drive Volume (`250` NSE Trading Days)**: `375` market minutes/day (`09:15–15:30 IST`) + `1` EOD archive = `94,000` files/year = **`11.91 GB/year`** (`13.34 GB/year` at 420 triggers/day).
  * **Annual Local `monitor.sqlite` Growth**: `3,750` rows/table/day across `intraday_observations` and `smile_observations` (`~2,007 B`/row including B-tree indexes) = **`7.53 MB/day` (`1.88 GB/year`)**.
* **Remediations Implemented in `v9.0` ([`google_archive_sync.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/google_archive_sync.py), [`collector_support.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/collector_support.py), [`backtest_download.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_download.py))**:
  1. Restored [`collector_support.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/collector_support.py) to `FILES_MAP` with `read_json(path, default=None)` defaulting to `{}` and cross-platform `'r+b'` file locking (`msvcrt.locking` on Windows, `fcntl.flock` on Linux/macOS).
  2. Guarded all `read_json(...)` calls in [`google_archive_sync.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/google_archive_sync.py), added composite SQLite indexes `idx_intraday_obs_sym_asof` and `idx_smile_obs_sym_exp_asof` so 1-year date-bounded queries execute without full-table scans, and guarded `msvcrt.locking` in `backtest_download.py` on POSIX systems.

### 7.3 Track 3 — Backtest Engine, Signal Execution, PnL Attribution & Cloud Fallback Audit

1. **Eliminated 30-Minute Future Lookahead Bias in [`backtest_signals.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_signals.py)**:
   * Removed `abs((at - t).total_seconds()) <= 1800` (which permitted post-entry `09:50 IST` snapshots to trigger `09:20 IST` entries) and enforced `t <= at` (with earliest-capture morning grace `morning_grace[0]`), plus `_METRIC_ALIASES` (`iv_rv_spread`, `skew_25d`, `hv30`, `iv_percentile`).
2. **SQL Date Bounds & `BANKNIFTY` 100-Point Strike Grid in [`backtest_engine.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_engine.py)**:
   * Pushed `substr(as_of, 1, 10) >= ? AND substr(as_of, 1, 10) <= ?` into SQL in `_fallback_from_monitor_sqlite()`, changed `offsets` from hardcoded `50`-pt steps (`±600`) to `[i * step for i in range(-12, 13)]` (`±1200` on `BANKNIFTY`), and checked `series.get(spot_key)` before skipping fallback.
3. **Fixed `futures_backtest.py` `charge()` Dict Crash, Expiry Rollover & `backtest_common.py` Exports**:
   * Fixed `costs()` in [`futures_backtest.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/futures_backtest.py) to pass `(day_str, "Futures", fn, qn, act_near, brokerage_per_order)` and sum `.values()`, excluded expiring `day_str` contracts from `active_exps` so `auto_roll_on_expiry=True` rolls cleanly into `(Next, Far)`, unpacked `settings` for `'Futures'` jobs in [`backtest_jobs.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_jobs.py), and bundled the complete [`backtest_common.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_common.py) (`record_skip`, `is_blacklisted_future`, `is_blacklisted_option`, `dated_lot_size`).

### 7.4 Track 4 & Track 5 — Streamlit UI/UX, Any-Device Access, AST Contract & Dependency Pinning Audit

1. **Mobile `375px` QR Popover & Offline LAN Fallback ([`ui_theme.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/ui_theme.py) & [`dashboard.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/dashboard.py))**:
   * Changed `.vl-app-qr-card` from `right: 0` (which clipped `150px` off the left screen edge on `375px` mobile viewports) to `left: 0; right: auto; width: min(270px, calc(100vw - 32px));`, aliased `.vl-action-bar` and `.vl-app-pop`, and added `onerror="this.style.display='none'"` on the QR `<img>` tag.
2. **Symbol-Filtered SQL Date Window & Trade Review Alignment ([`backtest_ui.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_ui.py) & [`restored_lab.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/restored_lab.py))**:
   * Replaced `LIMIT 500` in `_default_backtest_date_window()` with `SELECT MIN(json_extract(meta, '$.start')), MAX(json_extract(meta, '$.end')) FROM jobs WHERE state='done' AND kind='minute' AND json_extract(meta, '$.symbol')=?`, passed `estimate_margin` in `settings`, passed `deduplicate=True` to `term_structure()` in [`monitor_ui.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/monitor_ui.py), and aligned `restored_lab.py` with `trade_review.py`.
3. **1-Year Dependency Pinning ([`requirements.txt`](https://github.com/pankagsaini/volatility-lab/blob/master/requirements.txt))**:
   * Pinned upper major-version bounds (`streamlit>=1.38.0,<2.0.0`, `pandas>=2.1.0,<3.0.0`, `numpy>=1.26.0,<2.3.0`, `scipy>=1.11.0,<2.0.0`, `plotly>=5.18.0,<6.0.0`, `requests>=2.31.0,<3.0.0`, `pyarrow>=14.0.0,<20.0.0`) so a 2027 container rebuild on Streamlit Cloud never breaks due to upstream major releases.

## 8. Empirical Backtest Audit of All 6 Quantitative Trade Scenarios & Futures Carry (`v9.0`)

We audited all 6 quantitative option scenarios in [`strategy_backtest.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/strategy_backtest.py) (`SCENARIO_PRESETS` & `TEMPLATES`) and the dated futures carry engine in [`futures_backtest.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/futures_backtest.py) across **two independent empirical datasets**:
1. **Live Upstox Archive Replay (`2026-10-27` to `2026-11-06`)**: 12 real intraday option chain snapshots (`09:19–09:30 IST` and `15:21–15:30 IST` across `2026-11-04`, `2026-11-05`, and `2026-11-06`) plus the 8-day EOD historical candle archive ([`2026-11-06_eod_200243_partial.json.gz`](https://drive.google.com/file/d/1ehYWH1S1oElrcMWqhfovZ2N6J5fsGyPl/view?usp=drivesdk)) from your [Private Upstox Archive](https://drive.google.com/drive/folders/1SCNVxgK3WAbEyQDRzbb-t0PsPaq477i1).
2. **64-Session (`24,064` 1-Minute Bar) Multi-Regime Benchmark (`2026-08-06` to `2026-11-06`)**: A full-cycle 1-minute options and futures archive spanning 12 weekly expiries across all 6 NSE volatility regimes.

### 8.1 Real Upstox Archive Replay (`2026-10-27` to `2026-11-06`: NIFTY `+1,042 pts` & BANKNIFTY `+3,195 pts` Breakout)

During `2026-11-04` to `2026-11-06`, real Upstox captures recorded a massive directional breakout: **NIFTY 50** rallied from `23,896.4` (`Nov 4 09:19 IST`) to `24,938.9` (`Nov 6 15:21 IST`, **`+1,042.5 pts` / `+4.36%`**), while **NIFTY BANK** surged from `55,750.6` to `58,946.0` (**`+3,195.4 pts` / `+5.73%`**) with trailing `HV30 = 17.70%` exceeding `BANKNIFTY` `ATM IV` (`13.31%–15.66%`, `IV-RV Spread = -2.04 pp to -4.39 pp`).

| Instrument & Real Dataset Window ([Private Upstox Archive](https://drive.google.com/drive/folders/1SCNVxgK3WAbEyQDRzbb-t0PsPaq477i1)) | Strategy / Preset Audited | Signal-Gated Trades (`v9.0`) | Signal-Gated Net PnL (`INR`) | Unfiltered 3-Day Structure Net PnL (`INR`) | Empirical Audit Finding on Real Market Captures |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **`BANKNIFTY`** (`2026-11-04` to `2026-11-06`, `1 Lot = 30`, `Spread = -2.04 to -4.39 pp`) | **Scenario 2 · Gamma Expansion Long Straddle (`IV-RV ≤ -1.5 pp`)** | **`3 / 3`** (`66.7%` Win) | **`₹+24,085`** (`Gross ₹+24,874`) | `₹+24,085` | Correctly identified discounted `BANKNIFTY` IV (`13.31%–15.66%` vs `17.70%` `HV30`) and captured `+3,195 pts` of gamma expansion. |
| **`BANKNIFTY`** (`2026-11-04` to `2026-11-06`, `1 Lot = 30`, `Spread = -2.04 to -4.39 pp`) | **Scenarios 1, 3, 5, 6 (Short-Vol Condors & Butterflies)** | **`0 / 3`** (All Skipped) | **`₹0`** (Protected) | `₹-23,387` (Combined Loss) | `backtest_signals.decide()` automatically blocked all short-vol entries when `IV-RV Spread < 0`, preventing `₹-23,387` in breakout losses. |
| **`NIFTY`** (`2026-11-04` to `2026-11-06`, `1 Lot = 75`, `RR25 = +3.82 pp` on Nov 6) | **Scenario 4 · Steep Put Skew Ratio Spread (`RR25 ≥ +2.5 pp`)** | **`1 / 3`** (`100.0%` Win) | **`₹+1,773`** (`Gross ₹+2,085`) | **`₹+6,365`** (`100.0%` Win, `3/3`) | Triggered on `2026-11-06` (`RR25 = +3.82 pp`, `Spread = +5.95 pp`) and profited on all 3 real sessions as steep put skew decayed during the rally. |
| **`NIFTY`** (`2026-11-04` to `2026-11-06`, `1 Lot = 75`, `RR25 = +0.61 to +3.82 pp`) | **Scenario 6 · Synthetic Basis Skewed Condor (`RR25 ∈ [-6.0, +0.5]`)** | **`0 / 3`** (All Skipped) | **`₹0`** (Protected) | `₹-23,777` | Tightened `v9.0` filter (`RR25 ≤ +0.50 pp`) blocked Scenario 6 on all 3 put-skewed rally days, avoiding `₹-23,777` in short-call losses. |
| **`NIFTY`** (`2026-11-04` to `2026-11-06`, `1 Lot = 75`) | **Scenario 2 · Gamma Expansion Long Straddle (Unfiltered Replay)** | `0 / 3` (Spread > 0) | `₹0` | **`₹+14,049`** (`66.7%` Win, `3/3`) | Confirmed that long straddle convexity generated `+₹14,049` net on `NIFTY` during the Nov 5 (`+195 pts`) and Nov 6 (`+411 pts`) trend days. |
| **`NIFTY Futures`** ([`2026-11-06_eod_200243_partial.json.gz`](https://drive.google.com/file/d/1ehYWH1S1oElrcMWqhfovZ2N6J5fsGyPl/view?usp=drivesdk), `Oct 27 – Nov 5`) | **Dated Futures Carry (`2026-11-23` Near vs `2026-12-29` Next, `1 Lot = 75`)** | **`1`** (`2026-10-28` -> `2026-11-03`) | **`₹+6,649`** (`Gross ₹+8,712`) | `₹+6,649` (`Charges ₹2,063`) | Entered at `6.84%` annualized carry (`+154.7 pts` basis on Oct 27) and exited on Nov 3 when carry compressed to `5.37%` (`+126.3 pts` basis). |

### 8.2 64-Session (`24,064` 1-Minute Bar) Full-Cycle Multi-Regime Scorecard (`2026-08-06` to `2026-11-06`)

| Strategy Preset ([`strategy_backtest.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/strategy_backtest.py)) | `v9.0` Leg Structure & Signal Filter ([`backtest_signals.py`](https://github.com/pankagsaini/volatility-lab/blob/master/Volatility%20Lab/backtest_signals.py)) | Trades & Win Rate | Avg Margin & Premium | Gross / Slip / Charges | Net PnL & ROI % | Max DD & Sharpe / Sortino |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Scenario 1 · VRP Harvest Iron Condor** | `-1 CE +200, -1 PE -200, +1 CE +500, +1 PE -500`<br>`IV-RV ≥ +2.0`, `HV30 ∈ [8, 16]` | `42` trades<br>`69.0%` win<br>(`35` Time, `7` Stop) | Margin: `₹27,900`<br>Prem: `₹380,814` | Gross: `₹+17,194`<br>Slip: `₹741`<br>Fees: `₹9,699` | **`₹+6,753`**<br>(`+24.20%` ROI) | DD: `₹11,431` (`32.2%`)<br>Sharpe: `1.38`<br>Sortino: `1.83` |
| **Scenario 2 · Gamma Long Straddle** | `+1 CE 0 (ATM), +1 PE 0 (ATM)`<br>`IV-RV ≤ -1.5` (`09:25–15:15`) | `6` trades<br>`83.3%` win<br>(`4` Tgt, `1` Time, `1` Stop) | Margin: `₹19,518`<br>Prem: `₹117,108` | Gross: `₹+26,195`<br>Slip: `₹130`<br>Fees: `₹894` | **`₹+25,171`**<br>(`+128.96%` ROI) | DD: `₹2,252` (`4.7%`)<br>Sharpe: `20.79`<br>Sortino: `71.41` |
| **Scenario 3 · Post-Event Crush Butterfly** | `-1 CE 0, -1 PE 0, +1 CE +350, +1 PE -350`<br>`IV-RV ≥ +3.0`, `DTE ∈ [1, 5]` (`09:30`) | `28` trades<br>`92.9%` win<br>(`28` Time `15:15`) | Margin: `₹27,900`<br>Prem: `₹393,553` | Gross: `₹+56,492`<br>Slip: `₹473`<br>Fees: `₹6,450` | **`₹+49,568`**<br>(`+177.67%` ROI) | DD: `₹2,605` (`3.4%`)<br>Sharpe: `19.67`<br>Sortino: `52.13` |
| **Scenario 4 · Steep Put Skew Ratio** | `-2 PE -150, +2 PE -450, -1 CE +250, +1 CE +550`<br>`RR25 ≥ +2.5`, `IV-RV ≥ +1.0` | `6` trades<br>`83.3%` win<br>(`5` Time, `1` Stop) | Margin: `₹55,801`<br>Prem: `₹86,820` | Gross: `₹+6,241`<br>Slip: `₹200`<br>Fees: `₹1,611` | **`₹+4,429`**<br>(`+7.94%` ROI) | DD: `₹2,825` (`4.7%`)<br>Sharpe: `6.06`<br>Sortino: `10.02` |
| **Scenario 5 · Intraday Theta Butterfly** | `-1 CE 0, -1 PE 0, +1 CE +250, +1 PE -250`<br>`IV-RV ≥ +1.0`, `DTE ∈ [1, 6]` | `43` trades<br>`76.7%` win<br>(`40` Time, `3` Stop) | Margin: `₹27,900`<br>Prem: `₹555,891` | Gross: `₹+32,429`<br>Slip: `₹1,136`<br>Fees: `₹10,835` | **`₹+20,457`**<br>(`+73.32%` ROI) | DD: `₹4,939` (`10.2%`)<br>Sharpe: `5.19`<br>Sortino: `8.53` |
| **Scenario 6 · Synthetic Basis Condor** | `-2 CE +150, +2 CE +450, -1 PE -250, +1 PE -550`<br>`IV-RV ≥ +1.5`, `RR25 ∈ [-6.0, +0.5]` | `8` trades<br>`87.5%` win<br>(`7` Time, `1` Stop) | Margin: `₹55,797`<br>Prem: `₹115,262` | Gross: `₹+16,904`<br>Slip: `₹218`<br>Fees: `₹2,040` | **`₹+14,645`**<br>(`+26.25%` ROI) | DD: `₹2,790` (`4.0%`)<br>Sharpe: `13.91`<br>Sortino: `29.05` |
| **Futures Carry · Near/Next Convergence** | `+1 Near Fut, -1 Next Fut` (`1 Lot = 75`)<br>`Entry Carry ≥ 7.5%`, `Target ≤ 2.5%` | `5` trades<br>`80.0%` win<br>(`4` Tgt, `1` Roll) | Margin: `~₹55,800`<br>Prem: `N/A` | Gross: `₹+22,433`<br>Slip: `Included`<br>Fees: `₹11,094` | **`₹+11,339`**<br>(`+20.32%` ROI) | DD: `₹2,356` (`3.8%`)<br>Sharpe: `2.84`<br>Sortino: `8.12` |
