# Cloud capture audit — 20 October 2026

Verdict: cloud transport and local ingestion verified; not yet qualified by a full live trading day. The system is suitable for building a research archive, not an assurance of uninterrupted live data.

## Evidence
- 79 archived files audited: 69 scheduled snapshots, two manual tests, eight partial EOD downloads.
- All 79 permanent local copies matched their recorded SHA-256 checksums. Total compressed size: 7,742,300 bytes.
- Scheduled snapshots span 12:33–15:57 IST at three-minute slots. This was a partial-day commissioning run, not a full 09:00–16:00 trial.
- Snapshot fetch times: minimum 5.542 s, median 6.832 s, maximum 128.191 s. These exclude final Drive-write overhead. This supports retaining three minutes rather than promising one minute.
- All eight EOD attempts had no API transport errors, but lacked a candle dated 20 October. Upstox confirms Dussehra: NSE/NFO closed. Earlier session candles were preserved; no new EOD session should be invented.
- Google diagnostic run confirmed PRIVATE folder access, exactly scheduledCapture and weeklyCleanup handlers, interval 3, and closed_today=true.
- Windows task completed with result 0; StartWhenAvailable=true, WakeToRun=false, battery operation allowed, one-hour execution limit. It needs the signed-in Windows session and connected Drive desktop.
- Six Python regression tests passed. Eight JavaScript assertions passed for closed/trading days, duplicate slots, next slot, runtime limit, malformed receipts, checksum mismatch and verified cleanup. Cleanup tests used mocks; no real 14-day-old files existed to test a deletion lifecycle.

## Issues corrected
1. Cloud capture ignored exchange holidays and repeatedly requested nonexistent EOD candles. Added authenticated Upstox NFO holiday calendar with daily caching and bounded fallback. Normal weekday 09:00–16:00 window is retained.
2. Successful JSON transport could be labelled complete with missing quote instruments or a mismatched option expiry. Added exact requested-instrument coverage and expiry checks. Complete still means delivery coverage; it does not certify every broker Greek.
3. Runtime guard did not leave enough explicit headroom for idle triggers and cleanup. Reduced measured capture allowance from 70 to 55 minutes/day. This is a local safety budget, not an account-wide quota meter.
4. Missing/truncated permanent local copies could be skipped because remote metadata was unchanged. Sync now re-downloads them. Outer sync exceptions now write a visible partial status and fail the Windows task.
5. Invalid receipt dates could pass JavaScript NaN comparisons. Cleanup now requires a finite, recent date, a files object, a positive integer size and a SHA-256-format digest before comparing cloud bytes.
6. Repeated underlying timestamps could leave first intraday values paired with a later overwritten smile. Import preserves the existing valid observation consistently. Original raw cloud captures remain available.
7. Earlier integration testing corrected holiday quote dating in options/futures; old session prices are not a new trading session merely because the feed packet was retrieved today.

## Operating contract and limits
- Google captures without the laptop. Local chart updates require the laptop on, user signed in, Drive running and internet available. Backlogs remain on Drive while it is off.
- No public web endpoint or order APIs. The read-only token is in private Script Properties, which project editors can read; it is not a separate encrypted secret vault. No token is included in data archives.
- Daily schedule uses a one-minute Google timer and three-minute slot gating. Google timers are approximate; delays, network stalls, broker failures, quota exhaustion and token expiry can cause missed snapshots. Missing historical IV snapshots cannot be recreated from OHLC candles.
- Special weekend sessions and sessions outside 09:00–16:00 are not covered by this regular weekday schedule. The holiday check does not expand the requested operating window.
- Calendar availability is itself a dependency. Its failure is visible; a cached calendar older than seven days is not silently accepted.
- Google consumer quotas currently include 90 minutes/day trigger runtime and 20,000 URL fetches/day; other scripts on the account share quotas. The three-minute workload is roughly 1,540 quote/chain requests for 140 captures with the current ten expiries, plus catalogue and EOD work. Expiry counts and payload sizes change.
- Weekly cleanup runs only when due and only after a recent laptop receipt plus matching file size and SHA-256. Large batches resume on following evenings. A 14-day age threshold with weekly processing means active copies may remain about 14–21 days; Google Trash then retains them for 30 more days. Disk backups after cloud deletion become the user's durable responsibility.
- Cleanup intentionally retains data if laptop verification is missing. Thus this collector cannot guarantee that Drive never fills if the laptop remains offline indefinitely or unrelated Gmail/Photos/Drive usage grows.
- A timestamped receipt proves a verified local copy at that time, not that a laptop disk can never fail afterwards. No second independent permanent backup has been set up.
- EOD candle publication on an actual trading day, a full-day runtime budget, and real retention beyond 14 days remain operational tests, not completed claims.

Official references:
- https://developers.google.com/apps-script/guides/services/quotas
- https://upstox.com/developer/api-documentation/get-market-holidays/
- https://support.google.com/drive/answer/2375102
