# Google Apps Script capture

Active project: https://script.google.com/home/projects/1_nNJrwJaGMmtctFJYhZudef3rCEtRiZRM9fZqn8BHnI-fnAMupLNqk-A/edit
Private archive: https://drive.google.com/drive/folders/1SCNVxgK3WAbEyQDRzbb-t0PsPaq477i1

## Schedule
Update 23 October 2026: the existing scheduledCapture trigger also runs prior-session reconciliation between 07:00 and 09:00 IST, including weekends. Partial EOD captures are queued by target date using Drive file IDs. Recovery uses the original contracts, retries no more than every 30 minutes, and validates the target session rather than today's date. The reconciliation budget is checked at eight minutes per day within the shared 55-minute guard (one in-flight run may exceed the threshold). Live snapshot hours are unchanged.

Run reconcilePriorSession manually to retry now; it uses the same lock, backoff and budget. EOD_PENDING, LAST_RECONCILIATION and RECONCILIATION_STATUS record recovery separately from LAST_EOD. The initial migration picks up the previously saved LAST_EOD partial file. Older partial captures predating that last file are not automatically migrated. The cloud requires no laptop for this recovery; Drive import and permanent local retention still need the laptop periodically.

Live verification: 23 October at 12:11 IST, Google recovered the 22 October target with nine series, status complete, no errors or pending series. Local schedule/retention tests and reconciliation tests passed. No new permissions, public deployment or additional trigger were required.

Three-minute snapshots on weekdays, 09:00 to before 16:00 IST. A one-minute Google trigger checks the current three-minute slot; successful slots are not duplicated. Trigger delivery is approximate, so this is not guaranteed exact minute-bar data. Weekends are skipped. Holidays may contain unchanged broker quotes; retrieval time is not exchange trade time.

Each snapshot preserves broker responses for NIFTY weekly/monthly options through the third futures month, BANKNIFTY three monthly options expiries, six futures contracts, both spot indices and India VIX. New listed contracts are discovered daily. Original IV/Greeks and source timestamps remain unchanged. One compressed JSON file per capture; partial failures are explicitly marked and do not replace successful files.

EOD daily candle collection runs between 18:00 and 22:00 IST, retrying every 30 minutes until today's candles are present. It requests a ten-day overlap. Holidays may remain pending rather than fabricate candles. A lock prevents overlap. A conservative 55-minute measured daily runtime guard stops API work before the consumer 90-minute quota; unrelated projects may still consume the shared quota. Google failures appear under Executions and trigger failure notifications.

Alerts & Watchdog Cleanup: As of November 2026, the failure watchdog trigger and calendar alert integration have been decommissioned and removed to maintain zero-overhead, non-disruptive cloud capture. All project triggers for `failureWatchdog` are purged automatically by `scheduledCapture` (or via `removeFailureWatchdogTrigger()`), leaving only the core `scheduledCapture` and retention schedules active without modifying Upstox capture frequency or credentials.

## Verified 20 October 2026
Two full Google captures succeeded: 10.750 and 8.841 seconds, 11 data groups, no errors. Drive gzip was read back successfully: 11 groups, 108,692 bytes. Archive sharing checked PRIVATE. Three-minute frequency chosen for headroom. At this observed size, 140 snapshots are approximately 15.2 MB per trading day, 335 MB per 22-day month; payload sizes and available Drive capacity vary.

## Operations
Code.gs contains no credentials. UPSTOX_TOKEN is stored in private Script Properties; project editors can access it, so do not share the project. It is not an encrypted secret vault. The collector cannot place orders. It runs on Google even with the laptop off, while token, Google account, quotas and broker access remain valid.

Run testCapture for a manual snapshot, verifyArchive for a read-back check, and installIntradaySchedule only to install/replace this project's scheduledCapture trigger. SCHEDULE_MODE can be changed from intraday to paused to stop collection. Review COLLECTOR_STATUS, LAST_SNAPSHOT, LAST_EOD and executions for health; avoid copying/exporting all Script Properties because they contain the token.

Drive for desktop is now connected. google_archive_sync.py downloads and checksum-verifies raw archives into data/google_archive and imports broker IV, smile/reversal observations, futures quotes, and EOD candles into the existing lab databases. The dashboard checks in the background every three minutes; Windows task "Volatility Lab Private Drive Sync" checks every ten minutes while the user is logged in, without waking the laptop. Changes appear when opening/switching views; a Show updates control preserves the chart while new data arrives. First catch-up can take longer while Drive hydrates files.

The laptop does NOT need to remain on for cloud collection. If it is off, Google retains the backlog. Local imports resume when it is on, signed in, and Drive is connected. These are three-minute snapshots, not one-minute OHLC bars. Futures rolling charts use EOD candles, never intraday LTP presented as daily closes.

Permanent local archives are outside the Drive sync folder. Weekly cloud cleanup keeps 14 days, and only moves a file to Drive Trash when a recent local receipt has an identical SHA-256 and size. Google permanently removes Trash after 30 days. A daily evening trigger checks whether weekly cleanup is due and resumes large batches within runtime limits. Missing/expired receipts, missing local copies, or mismatched hashes prevent deletion. This bounds this collector's usage while the laptop reconnects regularly; it cannot guarantee available storage if other Drive/Gmail/Photos data grows or the laptop remains offline indefinitely. No automatic deletion of local archives.

Google official quota documentation: https://developers.google.com/apps-script/guides/services/quotas

Automatic collection verified later on 20 October: Drive displayed 50 consecutive complete snapshot files from 12:33 through 15:00 IST at three-minute intervals. Additional files may be beyond the initial Drive listing. Scheduled trigger executions continued after market hours as short no-op checks. EOD execution remains unverified until its evening window. Drive showed 10.91 GB of 15 GB used (about 4.09 GB remaining).

Final integration verification, 20 October 2026: 79 of 79 Drive captures downloaded, no import errors. Broker IV available for seven NIFTY expiries and three BANKNIFTY expiries. 158 cloud futures snapshots were reprocessed as dated closing estimates for 19 October, because 20 October is an exchange holiday. Six regression checks passed. Weekly receipt is already synced; captures arriving after it are included in the next weekly verification, so none are prematurely deleted.

Audit update (20 October 2026): cloud schedule checks the authenticated Upstox annual NFO holiday calendar, cached daily, before capturing. A verified cache may be used for seven days; without usable calendar data collection fails visibly rather than assuming a session. Nonempty but incomplete quote responses and wrong option expiries are rejected. Local sync restores missing/truncated local copies, records outer failures, and preserves a coherent observation at repeated broker timestamps. Cleanup rejects malformed/undated receipts. See CLOUD_AUDIT_2026-10-20.md for remaining limits.
