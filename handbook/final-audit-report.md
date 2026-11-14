# Final Audit & Production Lock Report

14 November 2026 · baseline `cd7a9986eebc4a03cde5cd1550e1f70b768fca33` · local branch `codex/final-systems-audit`

The audit corrected confirmed pricing, provenance, collector, API and presentation defects in place. **214 Python tests passed with warnings treated as errors.** All five JavaScript/browser checks, Ruff, wheel build and an installed-wheel smoke test passed. The changes remain local and uncommitted; the website and Apps Script deployment have not been replaced by this audit.

This is a verified remediation report, not a certification that every execution path is suitable for live trading. Package-wide statement/branch coverage is **42.67%**. Full HTML/payload parsing, targeted source review and executable checks were completed; an exhaustive manual reading of every repository line was not established. Actual `monitor.sqlite` and `upstox/research.sqlite` files were unavailable in the inspected stores, so their real contents could not be certified. Database behavior was tested with isolated fixtures. No live broker order was placed.

## Findings and disposition

References identify canonical implementations rather than compatibility aliases. “Verified Clean” applies to the stated checks, not the entire enclosing module.

| Layer / finding | Severity | Status | Evidence / resulting behavior |
| --- | --- | --- | --- |
| 1 · Degenerate and near-expiry option calculations | High | Confirmed & Fixed | `volatility_lab/pricing/option_math.py:128`, `:444`: invalid inputs return missing values cleanly; expired contracts retain intrinsic payoff; near-expiry Black-76 gamma/theta no longer disappear behind an arbitrary cutoff. Scalar/vector and numerical-boundary regressions pass. |
| 1 · Chain forward inferred from the wrong strike or unvalidated prices | High | Confirmed & Fixed | `volatility_lab/pricing/option_math.py:581`: ATM selection, synchronized event times, valid two-sided depth and both spreads strictly below 3%; no caller bypass of quote validation. |
| 1 · Model smile/ATM fallback mixed broker IV and stale LTP with model output | High | Confirmed & Fixed | `volatility_lab/pricing/smile.py:90`; `volatility_lab/services/market_table.py:207`: unavailable model IV remains missing; broker observations retain separate provenance. |
| 1 · Forecast cache collision and incomplete 265-session horizon | High | Confirmed & Fixed | `volatility_lab/forecasting/expiry_forecast.py:38`, `:55`; `volatility_lab/forecasting/forecast.py:108`: distinct return histories cannot reuse the same fit merely because length/end date/final price match; all 265 requested sessions are produced. |
| 1 · Mutable OHLC validation bypass | High | Confirmed & Fixed | `volatility_lab/core/vol_lib.py:12`: changing a previously validated frame cannot bypass OHLC consistency checks. |
| 1 · Relative-value percentile and HV timing leakage | High | Confirmed & Fixed | `volatility_lab/research/relative_value.py:179`; `volatility_lab/services/market_table.py:248`: only strictly prior same-index observations enter ranks; HV does not fall forward to a future close. |
| 1 · Completed-close cache and expiry grouping | High | Confirmed & Fixed | `volatility_lab/storage/snapshot_history.py:64`; `volatility_lab/services/option_context.py:9`: pre/post-close captures receive different available daily history; expiry-day fallback is selected independently for each date. |
| 1 · Recorded rate/dividend metadata lost or missing values raised | High | Confirmed & Fixed | `volatility_lab/storage/data_accuracy.py:36`; `volatility_lab/storage/input_provenance.py:45`: nested recorded pricing inputs are read; missing model inputs do not block broker-only captures. Nonfinite/future-checked yield caches are rejected. |
| 1 · Validation receipts described a different model | Medium | Confirmed & Fixed | `volatility_lab/research/run_model_validation.py:13`, `:20`: Yang–Zhang uses the configured annualization; receipts record actual configuration and canonical implementation hashes, not alias hashes or a hardcoded 252 claim. |
| 1 · Realized-volatility convention | Medium | Calibrated | `volatility_lab/core/vol_lib.py:38`: retained the tested open-to-close Yang–Zhang component (`yang-zhang-2000-open-close-v3`), rather than restoring the review's older close-to-close variant. GARCH and EWMA forecast realized volatility; neither is relabeled as captured option IV. |
| 2 · Archive replacement hidden by cache, rewritten event times and manufactured volume | High | Confirmed & Fixed | `volatility_lab/backtest/backtest_engine.py:19`, `:36`, `:65`: archive revisions invalidate cached contents; event timestamps remain observed; non-executable marks do not become tradable minute bars with invented volume. |
| 2 · Signal available after entry admitted at entry | High | Confirmed & Fixed | `volatility_lab/backtest/backtest_signals.py:36`, `:103`: explicit `AvailableAt` is honored; prior EOD qualification uses the effective session close. |
| 2 · Legacy ledger performance looked verified | High | Confirmed & Fixed | `volatility_lab/backtest/forward_test.py:398`; `volatility_lab/cloud/apps_script/Code.gs:2112`; `scripts/publish_live_state.py:18`: inherited records without execution evidence are quarantined. Raw originals remain preserved; public performance excludes them. |
| 2 · Stop replay reported the threshold instead of the observed executable loss | High | Confirmed & Fixed | `volatility_lab/backtest/forward_test.py:1286`: stop replay requires per-leg exit prices and event times, uses the observed fill, and skips missing evidence. |
| 2 · Historical reports exposed unaudited P&L | High | Confirmed & Fixed | `volatility_lab/backtest/backtest_report.py:14`; `volatility_lab/services/web_data.py:636`; `docs/index.html:2479`: legacy runs remain countable, but monetary performance requires captured-minute execution/cost provenance. |
| 2 · Fees double-counted the IPFT component | Medium | Confirmed & Fixed | `volatility_lab/backtest/broker_charges.py:16`, `:36`: effective-dated components match the published bundled NSE exchange totals; GST base follows the cited Upstox tariff. Python, Apps Script and browser calculations were reconciled. |
| 2 · Daily capital returns, dated lots and option floor | High | Verified Clean | `volatility_lab/backtest/strategy_backtest.py:573`; `volatility_lab/backtest/backtest_common.py:45`: regressions cover daily aggregation, intervening zero-activity sessions, dated lot sizes and the configured 5% short-option floor. The capital estimate is not a live clearing-member SPAN quote. |
| 2 · Wrong-expiry futures cross-fill | High | Confirmed & Fixed | `volatility_lab/services/expiry_data.py:451`: recovery requires exact expiry and an accepted, finite, contemporaneous midpoint; unrelated monthly/weekly contracts cannot borrow a future. |
| 3 · Historical candle download fabricated complete snapshots | High | Confirmed & Fixed | `volatility_lab/cloud/apps_script/Code.gs:1543`: one explicitly typed historical-candle bundle retains broker timestamps and actual download time. It creates zero live snapshots and no option IV. Empty/partial responses remain explicit. |
| 3 · Python downloader always declared success and called a nonexistent sync API | High | Confirmed & Fixed | `volatility_lab/ingestion/manual_download_session.py:87`: calls `sync_once`, reads both supported candle envelopes, keeps archive-sync outcome separate, and reports empty/error/partial honestly. Optional cloud key stays in the local environment; transport error output omits credential-bearing URLs. |
| 3 · Special-session capture and closing timestamps | High | Confirmed & Fixed | `volatility_lab/core/market_calendar.py:87`, `:108`; `volatility_lab/storage/daily_capture.py:26`: collector, quote status, refresh policy, valuation and replay use sourced effective windows, including Muhurat. |
| 3 · Web API aliases/JSONP and unbounded pending references | Medium | Confirmed & Fixed | `volatility_lab/cloud/apps_script/Code.gs:1643`, `:242`: latest/ledger aliases use shared 55-second caches; callback names are validated; pending properties are bounded and overflow references archived before removal. |
| 3 · Retry attempts and infinite Retry-After | Medium | Confirmed & Fixed | `volatility_lab/runtime/external.py:23`: invalid attempt counts are rejected and nonfinite retry delays cannot permanently hold the circuit open. |
| 3 · Seven-day raw deletion | High | Calibrated | `volatility_lab/cloud/apps_script/Code.gs:200`: raw minute archives remain retained. Existing EOD candle bundles do not preserve every option chain, quote, Greek and event timestamp; deleting originals would lose evidence. The pruner explicitly reports zero deletions until full-fidelity consolidation exists. |
| 4 · Missing API data became zero, estimated volume, or assumed carry rate | High | Confirmed & Fixed | `volatility_lab/services/web_data.py:20`, `:141`, `:449`, `:565`, `:588`: absent values remain null; explicit invalid expiry is rejected; zero observations remain zero; volume and rates are not invented. Straddle breakevens are anchored to strike. |
| 4 · API import writes, blocking async routes and unsafe error responses | Medium | Confirmed & Fixed | `volatility_lab/ui/web_server.py:85`: blocking endpoints use worker threads; query inputs are bounded; public failures are redacted; importing the optional API does not write/copy files into the installed package. The web extra is declared in `pyproject.toml`. |
| 4 · Archived straddle filled missing history from the selected snapshot | High | Confirmed & Fixed | `volatility_lab/research/restored_lab.py:52`, `:132`: fixed-strike captured prices replace inferred historical price/delta curves; missing observations stay blank. Separate model helpers require explicit rate/forward and sum both leg gammas. |
| 4 · Offline performance, forecasts and public state lacked consistent provenance | High | Confirmed & Fixed | `scripts/publish_live_state.py:18`; `docs/index.html:17`, `:1771`, `:3774`; `docs/live_state.json:1`: source-hashed dated state hydrates offline. Forecasts identify GARCH(1,1)/EWMA and training date; invalid fits remain unavailable. |
| 4 · Header/term HV spread disagreed | Medium | Confirmed & Fixed | `docs/index.html:1494`, `:1780`: both use the same completed-session HV30 when available. Actual Chrome regression compares the displayed table spread with its inputs. |
| 4 · Controls, chart reuse, mobile clipping and palette | Medium | Confirmed & Fixed | `scripts/test_frontend_dom.cjs:1`; `volatility_lab/ui/ui_theme.py:1`: Chrome exercises all workspaces/tabs, both indices, 10 expiries, 60 setups and real Chart.js/Plotly. The 390px stress table fits. Root/published HTML are byte-identical; prohibited gold tokens are absent. |
| 4 · Captured IV fidelity | High | Verified Clean | `scripts/verify_dashboard_iv.py:1`; `scripts/publish_snapshot_history.py:23`: 2,396 IV observations and every embedded chain IV reconcile with the private Upstox archive. Invalid/incomplete archives cannot overwrite published history. |
| Repository · Duplicate data and generated clutter | Medium | Confirmed & Fixed | Root `data/` removed after reconciliation; `volatility_lab/` is canonical. Compatibility aliases remain intentionally identity-preserving. Cache/build artifacts moved outside the repository; `.gitignore` prevents recurrence. |

The fee comparison used the [Upstox tariff](https://upstox.com/brokerage-charges/). The configured 5% option floor is only one component of an approximate margin model, not confirmation that every trade will pass broker margin checks.

## Corrections to assumptions in the supplied review

- **Calendar:** a fixed 15:30/15:31 window would contradict the effective 2026 timetable. The code uses the dated NSE schedule and the separately published [8 November Muhurat window, 14:20–15:40](https://archives.nseindia.com/content/circulars/FAOP76940.pdf). The [CAS implementation circular](https://nsearchives.nseindia.com/content/circulars/FAOP75472.pdf) establishes the August change; the [NSE CAS FAQ](https://nsearchives.nseindia.com/web/mediaattachment/2026-11/FAQs_for_CM_and_FO_FOR_CAS_2.0_20261109152618.pdf) describes the subsequent timetable. 2027–2028 holidays remain explicitly provisional pending official annual calendars; they are not certified exchange calendars.
- **Runtime:** Google currently lists 90 minutes/day of trigger runtime for consumer accounts, six minutes/execution, 20,000 URL fetches/day, 9 KB/property and 500 KB/property store. The local collector policy admits at most 85 minutes, with five of those reserved for EOD; its normal snapshot allocation is therefore 80 minutes. An existing lower operator setting is honored. This is headroom, not proof that all account-wide usage fits. No fresh live execution/quota log was obtained during this final pass. See [Google quotas](https://developers.google.com/apps-script/guides/services/quotas) and `Code.gs:101`.
- **Endpoint:** the pasted URL has character differences from the working deployed URL. The functioning configured URL was preserved (`docs/index.html:3399`), rather than replaced with the pasted spelling. Both the website and configured endpoint responded HTTP 200 during inspection. The deployed ledger alias still requires the local Apps Script update; local success is not evidence of deployment.
- **History:** uncaptured broker IV remains null. The two previously authorized closing-price estimates remain separate dotted estimate fields for 8 and 13 November; they do not replace captured IV. The 13 November 13:14 capture remains the recorded observation. Black-76 does not justify smoothing away a broker expiry-day observation.
- **Scope of real data:** each canonical index CSV has 2,498 OHLC rows through 11 November. Nine canonical chain files contain 1,336 strike rows, traced to the original 11 November capture. Public history/quotes also include separately reconciled 13 November captures. Forecast training remains through 11 November; BANKNIFTY's invalid GARCH fit is withheld. The offline public ledger contains 13 recorded closes without verified execution evidence; 72 historical backtest rows likewise have no publishable audited performance. These are not presented as profitable verified trades.

## Repository footprint

Counts include regular working-tree files, hidden files and this report/evidence, excluding `.git`. Baseline counts included generated caches; after-cleanup counts exclude the relocated caches/build outputs. The separate source inventory follows Git's nonignored file list, counts only files that exist, and includes proposed new files before staging.

| Inventory | Before | After |
| --- | ---: | ---: |
| Working files | 809 | 300 |
| Working bytes | 13,820,774 | 8,600,643 |
| Versioned/proposed source files | 305 | 300 |
| Versioned/proposed source bytes | 9,848,903 | 8,600,643 |
| Duplicate root data files | 18 | 0 |
| Duplicate root data bytes | 1,455,006 | 0 |

The duplicate files were reconciled against canonical CSV/JSON inputs before relocation to `C:\Users\panka\AppData\Local\Temp\vl-audit-legacy-data-cd7a998`. Canonical chain prices/Greeks were compared with the original compressed capture SHA-256 `b58885f08fb4fc0b464317d1b5aad5904da548e1f0987240613ab50828ba853d`. Unique raw Drive archives were preserved. No duplicate spaced package directory was found. Runtime stores remain separate from bundled reference data by design; forcing writable databases into a wheel installation would regress packaging safety.

The exact final `git status -s` and command outputs are retained in [audit-evidence.txt](audit-evidence.txt).

## Verification

Python 3.12.14 on Windows. The four named unittest modules now reside under `tests/integration` and `tests/unit`; their module names are unchanged. Tests used isolated temporary stores with offline mode. Coverage used `COVERAGE_CORE=pytrace` because Windows Application Control blocks the native tracer; warnings were not suppressed.

| Check | Exact outcome |
| --- | --- |
| `python -W error -m unittest -v test_audit_remediations` | `Ran 50 tests in 2.388s` / `OK` |
| `python -W error -m unittest -v test_quant_audit_fixes` | `Ran 14 tests in 0.076s` / `OK` |
| `python -W error -m unittest -v test_execution_safety_fixes` | `Ran 15 tests in 3.198s` / `OK` |
| `python -W error -m unittest -v test_quant_runtime_safety` | `Ran 7 tests in 0.053s` / `OK` |
| Entire suite, warnings as errors, branch coverage | `214 passed in 88.07s (0:01:28)`; `Total coverage: 42.67%` |
| `ruff check volatility_lab/ scripts/ tests/ pyproject.toml` | `All checks passed!` |
| `python -m build --wheel --no-isolation` | `Successfully built volatility_lab-18.0.0-py3-none-any.whl` |
| Wheel installed outside checkout in clean venv | 26 reference checksums, alias identity, lot sizes, optional API and truthful degraded health passed |
| Live connection / collector / frontend / pricing / real DOM | All five Node scripts passed; exact output in evidence |
| Broker archive reconciliation | `Verified 2396 IV observations and every embedded option-chain IV against Upstox archives.` |
| Locked dependency audit | `No known vulnerabilities found` |
| Source/payload integrity | All Python sources parsed, JSON payloads validated; HTML parity and no prohibited gold tokens verified |

The four requested unittest suites are included in the 214-test run, not additional independent tests. Python 3.11/3.13 and Ubuntu checks are configured in CI but were not executed locally in this audit. Headless Streamlit startup and HTTP endpoint behavior passed; every Streamlit interaction and a real market session were not exercised. The last frontend-only spread correction was rechecked by all five Node suites after the Python run.

## Production lock and branch synchronization commands

The audited changes are prepared locally. `master` is protected; a normal pull request preserves required CI. The commands below intentionally contain no force push or protection bypass. Run from PowerShell after reviewing the diff:

```powershell
Set-Location 'D:\volatility-lab'
git switch codex/final-systems-audit
git diff --stat
git add -A
git commit -m "Fix final audit findings across pricing, capture, API and desk"
git push -u origin codex/final-systems-audit
gh pr create --base master --head codex/final-systems-audit --title "Fix final systems audit findings" --body "Correct pricing and capture provenance, quarantine unverified performance, verify frontend/API behavior, and remove duplicate data. Audit details and exact test evidence are in handbook/final-audit-report.md and handbook/audit-evidence.txt."
gh pr checks codex/final-systems-audit --watch
gh pr merge codex/final-systems-audit --squash
git switch master
git pull --ff-only origin master
git fetch origin
git merge-base --is-ancestor origin/main origin/master
if ($LASTEXITCODE -ne 0) { throw 'main has diverged; inspect before synchronizing' }
git push origin origin/master:refs/heads/main
```

If `main` gains protection, synchronize it through a `master` → `main` pull request instead of bypassing that protection. A Git merge publishes the website through its existing Pages workflow; it does not deploy `Code.gs`. Apps Script must separately be updated and a new version attached to the existing web-app deployment. Until remote CI, deployment verification and a fresh market-session capture pass, production lock remains pending.
