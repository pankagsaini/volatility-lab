# Volatility Lab — audit and development plan

Audit date: 23 October 2026. Scope: local source, saved data/status, scheduled tasks, public instrument-catalogue connectivity and targeted tests. This is a development assessment, not validation of a profitable strategy. No trading orders were placed. Google server-side triggers were not independently inspected in this audit.

## Current follow-up — 24 October 2026

This update records work completed after the dated audit below. The audit's figures remain a historical snapshot; use this section for the current local state.

- **Historical download:** the worker was still running and advancing at the 13:08 IST snapshot: 86,042 completed jobs, 6,761 pending jobs, 184,964 superseded jobs and 190,583,296 stored minute rows. Pending jobs can change as discovery creates new minute work. These are queue/row counts, not certified unique candles.
- **Fair scheduling:** `backtest_download.py` now serves both indices within the same priority tier when both have work, while keeping expiry/contract/minute/discovery priority. The active worker was not restarted; the change takes effect when a worker next starts.
- **Coverage report:** ordinary weekends are no longer counted as unverified weekdays. Saturdays/special sessions remain explicitly unverified unless evidence exists; the report still does not claim a complete exchange calendar.
- **Cloud archive:** the latest mirrored EOD file is complete for 23 October, with nine series, eight daily candles per series from 13–23 October, and no recorded errors or pending requests. The dashboard now shows the latest saved capture, local sync result and receipt age; local files cannot verify live Google trigger/token/retention health.
- **Futures:** NIFTY and BANKNIFTY rolling audits are marked verified through 23 October. The current front three dated contracts per index have closes through that date. This does not certify every historical contract or minute interval.
- **Backtests:** options runs use each archived contract's broker lot size. Futures runs still label quantity as assumed units; dated-lot sizing and capital feasibility remain research limitations. Open futures positions are reported separately from closed-trade results.
- **Tests and source checks:** all 49 tests discovered by `unittest` passed. The changed dashboard, coverage and download modules parse successfully. No active Python source imports or calls Kite.
- **Kite cleanup:** disconnected Kite source files were removed earlier. Remaining orphaned credentials/session/cache/status files are now gathered in `D:\My First Algo\Files for manual deletion\Kite setup - review and delete\` for the user to review and delete. The active Upstox token and market archives were left in place. The project drive had about 78 GiB free at the latest check.
- **Needs follow-up:** Windows reports the backtest scheduled task as Running and the queue is moving, but its latest `LastTaskResult` is the non-zero value `0x800710E0`. Recheck that result after the worker finishes; it is not, by itself, evidence that the current run has stopped. Google-side trigger executions, token status and cleanup status cannot be verified from the local project.

The governing IV decision is now explicit: retain only broker-supplied Upstox IV captured through Google Cloud/Drive; never reconstruct or backfill historical IV. Forecasts remain separate outputs. `IV DATA POLICY.md` is the policy reference and supersedes older contradictory wording in this historical audit.

## Assessment

### Development checkpoint 2 — coverage reporting, 23 October 2026

Implemented `archive_coverage.py`, a read-only queue inventory with per-index and per-contract request status, empty responses, failed requests, missing raw-file checks and returned-expiry ranges. The report appears in Backtest → Archive & execution assumptions and offers a contract CSV download. Saved snapshots: `data/audit/historical_coverage.json` and `historical_contract_coverage.csv`; run the module to regenerate them. UI inventory caching is limited to 60 seconds.

Options runs now save a date-by-date evidence report. Saved daily index candles independently confirm sessions; those missing spot minutes reach the engine and are explicitly excluded. Dates without daily or minute evidence remain unverified, including possible holidays. This avoids inventing a full exchange calendar. Full-session completeness, contract listing dates and candle-level invalid/zero-volume/conflict auditing across the entire archive remain unfinished; the new inventory is deliberately not called a completeness certificate. Reported stored rows may overlap.

Observed expiry lists begin October 2024 for both indices, despite a five-year request. This does not establish the earliest available candle date. NIFTY discovery includes empty responses; BANKNIFTY option discovery is still pending. No raw archives or queue entries were modified. Fourteen coverage/strategy/filter tests passed, including confirmed sessions absent from the minute series. A Windows SQLite handle leak discovered during tests was fixed.

Next within the coverage work: build the incremental candle-level index after prioritizing small resumable batches, then verify listing/session calendars. Until then use current reports to expose gaps, not to certify completeness.

### Development checkpoint 1 — collector recovery, completed 23 October 2026

- The local catalogue has bounded retries and preserves safe failure phase/class/HTTP status across later successes. Invalid responses cannot replace a valid cache. Retained stale catalogues are not silently used as current.
- The Windows Daily Collector task now retries failed executions up to three times at five-minute intervals; existing triggers, IgnoreNew policy and 18-minute execution limit were preserved. The before/after settings are in `data/audit/collector_retry_settings.json`.
- A process-level collector file lock prevents manual and scheduled invocations overlapping. A competing invocation leaves the active worker's status alone. Completed stages remain idempotent and partial/failed stages remain retryable.
- `data/daily_collector/runs.jsonl` now retains sanitized outcome/stage/error-count history. This begins with future executions; it does not reconstruct previous discarded exceptions. Detailed catalogue events remain in `data/upstox/catalogue_refresh_events.jsonl`.
- Fourteen targeted tests passed: catalogue failures/recovery, corrupt input, transient write contention, overlap protection, failed-run retry eligibility, completed-stage deduplication, accuracy guards and cloud import tests. Windows settings were read back after applying them. An actual future scheduled failure/restart has not been forced against the live collector.
- Google reconciliation was deployed and live-tested in the preceding checkpoint. No cloud permissions or capture schedules were changed in this local reliability step.

Next: Priority 0 item 2, an honest historical coverage report. Start with a read-only report over the evolving archive; do not wait to build the report, but do not label the history complete until downloads and coverage checks finish.

### Cloud reconciliation implemented, 23 October 2026

The next cloud improvement described below is now deployed: the existing Google minute trigger checks prior-session EOD recovery from 07:00–09:00 IST, including weekends. It retains original contracts via pending Drive file IDs, uses 30-minute retry spacing and a separate runtime allowance within the shared guard. Today's snapshot success and prior-day recovery status remain separate. A live manual recovery completed at 12:11 IST with all nine 22 October daily series and no errors/pending items. The local collector remains a backup, not a dependency for Google capture. Future scheduled execution is still subject to Google quota/trigger delivery and broker availability. Existing schedule/retention and new reconciliation tests passed.

### Follow-up verified on 23 October, approximately 12:03 IST

- Google Drive contained 60 complete snapshots for today through 12:03 IST. The 09:02 failure belonged to the separate laptop catch-up, not these cloud snapshots.
- The laptop retry finished successfully at 12:01:57 IST with no errors.
- The last Google EOD payload from 22 October (21:35 IST) was partial because all nine requested daily series ended on 21 October; there were no recorded request errors. This proves the requested current-day bars were absent from those responses, not why the broker omitted them.
- Implemented `catalogue_fetch.py`: three bounded attempts, validation before replacing cache, safe phase/error-class/HTTP-status event logging, and retained prior cache on failure. No stale catalogue is silently presented as current. Dashboard labels now explicitly identify laptop catch-up / backup capture.
- Three new retry/validation/privacy tests plus six existing accuracy/cloud tests passed (9 total for this change); edited modules compiled successfully.
- The original 09:02 exception was discarded by the old code and cannot be recovered exactly. Do not describe a network, broker or write failure as proven.
- Next cloud improvement: a Google-side morning reconciliation of prior-session daily bars, with separate status from live snapshots. Verify that end-to-end before considering the laptop catch-up redundant. Dashboard calculations and permanent local retention still require local import, but cloud capture does not require the laptop or dashboard to be running.

Keep the lab focused on NIFTY/BANKNIFTY option volatility and futures calendar carry. Its useful differentiator is a permanent, traceable history plus reproducible strategy research. More indicators are not the next priority. Reliable collection, explicit coverage and realistic execution assumptions are.

The dashboard works and the archive is growing. The backtester remains a research draft: incomplete history, fixed contract quantities, simplified execution and limited historical IV prevent treating its results as live-trading evidence.

## Verified findings

| Area | Evidence | Assessment |
| --- | --- | --- |
| Dashboard | Local Streamlit health endpoint returned OK | Server healthy; not a full visual or every-control test |
| Regression checks | 23 tests passed across accuracy guards, download queue, options engines/rules and Google sync | Useful safeguards; not exhaustive validation |
| Download | At 11:57 IST: 127.18 million minute candle rows, 58,736 completed requests, 15,839 pending | Active, incomplete; row count is not verified unique coverage |
| Coverage imbalance | Around 21,066 NIFTY discovery requests completed; 10,264 BANKNIFTY discovery requests still pending | A total progress count conceals symbol imbalance |
| Daily capture | 09:02 attempt partial: catalogue refresh failed; completed futures candles missing | Current reliability issue; do not present stale history as current |
| Catalogue recheck | Public endpoint HTTP 200; adapter successfully loaded 2,810 relevant records | Recovered at audit time; original cause was not logged |
| Drive sync | Ready, 431 local files, no errors in saved status | Local sync healthy at snapshot time; does not establish complete cloud capture |
| Saved option observations | Each index spans 24 September–23 October across only 14 distinct observation dates | This is not five years of IV history or even every trading day in that span |
| Futures download | Historical audit snapshot: Kite and Upstox reported finished_available_requests; current Python integration no longer includes Kite | Completion of returned requests does not prove full requested historical coverage |
| Storage | About 79 GiB free on project drive | Adequate currently; keep the downloader's free-space guard |
| Charges | Saved official-page check dated 23 October | Current check exists; historical effective dates and rounding still need reconciliation |

All five Volatility Lab scheduled tasks remain registered; the backtest download task was Running. The daily capture was retriggered after the successful catalogue refresh. A retry being launched is not proof of successful completion; inspect its final status.

## Priority 0 — operational trust before new features

### 1. Make collection failures recoverable and diagnosable

Evidence: `UpstoxAdapter.master()` makes one catalogue fetch and converts every exception into the same message. This hides HTTP status, timeout, parsing and local-write failures. Today's original cause cannot be reconstructed from that message.

Implement bounded retries with backoff for transient failures; record safe error class and HTTP status without tokens or raw authenticated URLs. Separate download, schema validation and atomic-write diagnostics. Retain the previous valid catalogue; if used temporarily, label its age and do not claim new expiries have been discovered. Retry new-expiry discovery later.

Acceptance: simulated timeout, 429/5xx, malformed gzip/JSON and write contention preserve the last valid file, expose the correct failure category and recover without duplicate collectors. A failed morning capture must be retried and verified, not silently considered complete.

### 2. Publish a real coverage report

After download completion, inventory symbol × expiry × contract × date × interval. Compare with the exchange trading calendar and actual listing intervals. Distinguish no returned contract, empty broker response, missing candle, zero-volume candle, conflict and failed request. Do not count all missing intervals as market closures.

Acceptance: each backtest shows requested sessions, eligible sessions, covered sessions, skipped sessions and reasons. Days absent from the spot series must appear in the coverage denominator: the current options engine iterates only days found in that series. Do not infer that an unobserved day did not trade.

### 3. Verify futures freshness separately

Inspect the last completed daily candle for each actual contract and each rolling rank. Diagnose conflicting midnight/09:15 daily records before choosing a normalization rule. Keep conflicts visible; never fill historical closes using current quotes or join different contracts into apparent trade P&L.

Acceptance: after EOD catch-up, every supported rank has the expected completed session or a precise reason it does not. Missing bars cannot disappear behind an overall successful task exit code.

### 4. Make cloud health self-service

The dashboard now shows the latest saved Cloud capture, local sync status and receipt age. Remaining: surface latest local import and actionable retention/token status where available, then verify actual Apps Script executions after a long laptop-off period. Local files alone cannot prove every scheduled cloud snapshot ran.

Retention must continue to require a recent receipt plus identical SHA-256 and size before moving captures older than 14 days to Drive Trash. Missing/stale receipts should prevent deletion. Keep permanent local copies and an independent private backup; a ZIP containing only handover text is not a data backup.

## Priority 1 — make backtests decision-useful

1. **Historical contract sizing:** options now use archived broker lot sizes and validate each dated contract lot. Futures backtests still use assumed units; add dated lot sizing and capital/margin feasibility before treating futures P&L as tradable or showing return-on-capital metrics.
2. **Execution realism:** retain next-minute-open fills after minute-close signals. Test slippage sensitivity, delayed/partial multi-leg fills and bid/ask execution where recorded. A positive-volume OHLC candle does not guarantee every leg could fill at its open. Minute data cannot establish tick-level stop ordering.
3. **End-of-sample accounting:** report open positions, unresolved exits and mark-to-market separately from closed trades. Current futures engine returns an open position separately; closed-trade P&L/drawdown is not total equity risk. Add tests for gaps before expiry, pending exits, expiry boundaries and direction/sign conventions.
4. **Cost verification:** compare representative option/futures round trips against dated official schedules and sample contract notes. Preserve the simple Total trading costs display. Automate checking, but do not assume the first observed new rate is its legal effective date; version effective-date evidence and rounding.
5. **Point-in-time signals:** keep same-expiry IV/IV–RV signals known before entry. Current signal loading uses the later of market and retrieval timestamps and a 15-minute freshness bound. Downloaded historical price candles do not supply historical broker IV or bid/ask. Do not fabricate five years of IV-triggered results from these candles.
6. **Validation protocol:** predefine one simple strategy, freeze settings, train chronologically and test on unseen periods. Compare net results under multiple slippage assumptions and regimes. Account for repeated parameter trials. Preserve failed/skipped trades in the audit trail instead of optimizing on the clean subset.

Acceptance: reproduce saved results from the same immutable inputs and engine version; show coverage, assumptions and costs alongside results. No profit claims based only on the currently partial archive.

## Priority 2 — efficient research and a simpler interface

- Keep Options, Futures and optional Research/Backtest as the primary workspaces. Keep operational detail collapsed, but expose stale-data and incomplete-coverage warnings.
- Preserve broker IV/Greeks as supplied, with timestamps/source. Keep invalid/missing broker fields explicit. Do not restore the blanket 3% spread rejection for raw history; instead apply execution assumptions in simulations.
- Keep HV, IV–RV spread, term structure, skew and futures carry context. Treat IV–HV as a comparison, not an automatic sell signal. Broker IV may have undocumented inputs; matching another platform is not a correctness test without matched time, expiry and convention.
- Do not generate or retain reconstructed historical IV. Accept historical IV only from broker observations captured through Google Cloud/Drive, under `IV DATA POLICY.md`; keep forecasts separate. Chart connectors must not become synthetic backtest observations.
- Evaluate GARCH/EWMA/Yang–Zhang forecasts against simple baselines using proper out-of-sample targets and effective independent sample counts. A model deserves space only if it improves the user's decision beyond those baselines.
- Build a canonical indexed candle store with unique provider/contract/interval/time keys and provenance. Preserve raw responses while indexing. Quarantine conflicts; do not silently average or replace broker values.
- Cache by data revision, query only the chosen date range, and keep refresh work off the UI rendering path. Benchmark cold start, warm chart changes and a fixed one-year backtest before and after changes; avoid broad rewrites.
- Add fair per-symbol download scheduling and observed runtime/throughput counters so progress is meaningful for both indices.

Do not prioritize more decorative indicators, CSV-only tools, automatic order execution, position-risk replicas or a complex optimizer. Do not reintroduce NSE/GitHub capture, phone login or paid cloud services without a new user request.

## Download outlook at this audit

At about 48 completed requests per active minute, 15,800 remaining discovery calls alone need roughly 5.5 hours. Discovery creates additional minute jobs. Using the overall observed ratio of completed minute to discovery requests gives roughly 44,000 further calls, or about 15.5 active hours, but BANKNIFTY's listing history and candle availability can differ materially.

Planning range: **16–30 further awake/online hours**, potentially **24–48 elapsed hours** given interruptions. This is not a guaranteed deadline. The earlier 24–36 hour estimate was too uncertain to use as a completion promise. Historical queue state survives restarts; execution needs Windows sign-in, internet, valid access and disk space. Finished requests still require the coverage audit above.

## Suggested delivery order

1. Finish archive download while repairing collector diagnostics/retries and verifying completed futures history.
2. Produce the coverage matrix and canonical data index; resolve conflicts without destroying source evidence.
3. Correct sizing, execution/cost assumptions and open-position accounting; expand regression tests.
4. Validate one options strategy and one futures carry strategy out of sample; save reproducible reports.
5. Improve performance and presentation using measured bottlenecks, then reconsider additional features.

## Local maintenance notes

This plan and the inventory are local project records. No handover document or share bundle is maintained. When you return to the lab, inspect live status before trusting this dated snapshot. Preserve the downloader and unique archives. Do not print credentials. Follow `IV DATA POLICY.md`: trust only broker IV captured through Google Cloud/Drive; keep forecasts separate. Update this plan after meaningful changes. The lab should stay focused on practical research, total costs, dependable collection and a clean interface.
