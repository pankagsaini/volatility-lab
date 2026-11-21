# Google Apps Script capture

Active project: https://script.google.com/home/projects/1_nNJrwJaGMmtctFJYhZudef3rCEtRiZRM9fZqn8BHnI-fnAMupLNqk-A/edit
Private archive: https://drive.google.com/drive/folders/1SCNVxgK3WAbEyQDRzbb-t0PsPaq477i1

## Collector contract

Deploy only `Code.gs`. The collector stores Upstox market snapshots and candles, keeps archive/health metadata and serves recorded data to the desk. Paper entry, MTM, exits, fees and ledger publication run outside Apps Script. Removing the old evaluator requires no change to existing archives or ledger files. The read-only status endpoint reports `collection_only: true`; the old ledger routes return an unknown-action error.

The default full-quote selection includes ATM offsets 0–5, 7 and 10 strike steps on both sides, the session anchor and the observed broker 10Δ/25Δ brackets. Outside that set the raw chain remains available but may lack timestamped two-sided L1. Missing quotes remain missing for the paper worker. Existing operators may explicitly set `OPTION_QUOTE_DEPTH=full` to request additional depth at higher quota cost. A 19 Nov 2026 12:28 IST archived-snapshot dry run selected 430 instrument keys in five quote batches, versus 500 in five on the previous collector (no open ledger positions); both needed two extra daily-history calls on a cold cache. This measures request construction, not Google runtime.

Daily-history caching and the recorded spot/straddle series feed the website. They do not track paper positions. The latest/status/closing-risk endpoints read stored data. The historical-candle endpoint requests bounded instrument data from Upstox for forecast publication.

## Schedule

A one-minute trigger captures regular and announced special F&O sessions, checking for overlapping executions and successful prior slots. The capture may arrive late or miss a slot. Inspect `?action=status`, including the last snapshot, last gap, budget, pending EOD and reconciliation status. Exchange timestamps and capture timestamps are distinct.

Incomplete EOD candles can be retried by the scheduled reconciliation worker. To retry explicitly, use `backfillPendingEod('YYYY-MM-DD')` in the editor or its existing authenticated HTTP action. Reconciliation uses its own admission limit within the shared runtime guard.

Each snapshot preserves broker responses for NIFTY weekly/monthly options through the third futures month, BANKNIFTY three monthly options expiries, six futures contracts, both spot indices and India VIX. New listed contracts are discovered daily. Original IV/Greeks and source timestamps remain unchanged. One compressed JSON file per capture; partial failures are explicitly marked and do not replace successful files.

EOD collection starts five minutes after the regular derivatives close (or at the configured special-session EOD start). The scheduler retries pending EOD records through the configured evening window. A late-session snapshot allowance and a separate EOD reserve protect recording on days with heavy trigger runtime. Reaching the admission limit pauses the corresponding capture; check the status and archived-file coverage before describing a session as complete.

Capture Monitoring & Diagnostics: Google Apps Script executions and status are tracked through `COLLECTOR_STATUS`, `LAST_SNAPSHOT`, and `LAST_EOD` in Script Properties, or by running `auditStatus()` in the Apps Script editor. Capture runs automatically via the single `scheduledCapture` time-driven trigger without external dependencies, third-party alert services, or disruptions to Upstox capture routines.

## Verified 20 October 2026
Two full Google captures succeeded: 10.750 and 8.841 seconds, 11 data groups, no errors. Drive gzip was read back successfully: 11 groups, 108,692 bytes. Archive sharing checked PRIVATE. Three-minute frequency chosen for headroom. At this observed size, 140 snapshots are approximately 15.2 MB per trading day, 335 MB per 22-day month; payload sizes and available Drive capacity vary.

## Operations
Code.gs contains no credentials. UPSTOX_TOKEN is stored in private Script Properties; project editors can access it, so do not share the project. It is not an encrypted secret vault. The collector cannot place orders. It runs on Google even with the laptop off, while token, Google account, quotas and broker access remain valid.

Run testCapture for a manual snapshot, verifyArchive for a read-back check, and installIntradaySchedule only to install/replace this project's scheduledCapture trigger. SCHEDULE_MODE can be changed from intraday to paused to stop collection. Review COLLECTOR_STATUS, LAST_SNAPSHOT, LAST_EOD and executions for health; avoid copying/exporting all Script Properties because they contain the token.

Drive for desktop is now connected. google_archive_sync.py downloads and checksum-verifies raw archives into data/google_archive and imports broker IV, smile/reversal observations, futures quotes, and EOD candles into the existing lab databases. The dashboard checks in the background every three minutes; Windows task "Volatility Lab Private Drive Sync" checks every ten minutes while the user is logged in, without waking the laptop. Changes appear when opening/switching views; a Show updates control preserves the chart while new data arrives. First catch-up can take longer while Drive hydrates files.

The laptop does NOT need to remain on for cloud collection. Google Cloud runs 1-minute live intraday captures independently. Local imports resume when connected to Google Drive.

Permanent cloud archives are preserved on 5TB Google Drive storage with zero deletions. All cleanup and trashing routines have been permanently decommissioned and removed from the codebase.



Google official quota documentation: https://developers.google.com/apps-script/guides/services/quotas

Automatic collection verified later on 20 October: Drive displayed 50 consecutive complete snapshot files from 12:33 through 15:00 IST at three-minute intervals. Additional files may be beyond the initial Drive listing. Scheduled trigger executions continued after market hours as short no-op checks. EOD execution remains unverified until its evening window. Drive showed 10.91 GB of 15 GB used (about 4.09 GB remaining).

Final integration verification, 20 October 2026: 79 of 79 Drive captures downloaded, no import errors. Broker IV available for seven NIFTY expiries and three BANKNIFTY expiries. 158 cloud futures snapshots were reprocessed as dated closing estimates for 19 October, because 20 October is an exchange holiday. Six regression checks passed. Weekly receipt is already synced; captures arriving after it are included in the next weekly verification, so none are prematurely deleted.

Audit update (20 October 2026): cloud schedule checks the authenticated Upstox annual NFO holiday calendar, cached daily, before capturing. A verified cache may be used for seven days; without usable calendar data collection fails visibly rather than assuming a session. Nonempty but incomplete quote responses and wrong option expiries are rejected. Local sync restores missing/truncated local copies, records outer failures, and preserves a coherent observation at repeated broker timestamps. Cleanup rejects malformed/undated receipts. See CLOUD_AUDIT_2026-10-20.md for remaining limits.

## Automatic EOD recovery (19 November 2026)

The existing one-minute `scheduledCapture` trigger now drains prior-session EOD work at 07:00–09:00 IST, including ordinary weekends, and between the session's EOD start and 22:00 on trading days. After close, five-minute boundaries remain reserved for the current day's EOD request. No new trigger or public mutation endpoint is needed.

Each worker run handles at most three dates, stops starting work after 45 seconds, uses a script lock, and accounts for runtime in both the shared rolling 24-hour budget and the eight-minute reconciliation budget. Admission reserves a worst-case capture plus five minutes for current EOD. Failed dates receive 30-minute exponential backoff capped at six hours; dates with no attempt are processed first. A failing oldest date cannot starve the queue. Missing broker data stays pending.

The worker reads the original session catalogue, normalizes the gzip MIME type before decoding, and verifies the archive target date. If a saved file reference is unusable, it checks surviving EOD archives for the exact date inside the existing private folder, then records the actual source file ID. Original archives are never overwritten or removed. Expired futures resolve through the broker's expired-contract catalogue and historical endpoint, rather than a potentially reused live exchange token. Broker access restrictions remain explicit failures; the collector does not synthesize candles or IV.

Read-only `?action=status` exposes `eod_pending`, `recovery_state`, `last_recovery_result` and `reconciliation_status`. Completion means all required instruments supplied the requested session candle and a new archive was committed. A current-day partial stays queued for the next day's retry if the broker publishes after the normal EOD window.

Validation: `node scripts/test_collector_audit.cjs` covers failure isolation, cooldowns, budgets, target-date validation, missing-file fallback and expired-contract routing. `node scripts/test_live_connection.cjs` covers existing scheduler/capture behavior. Set `COLLECTOR_SOURCE_PATH` to a downloaded deployed `Code.js` to run the same checks against production source.
