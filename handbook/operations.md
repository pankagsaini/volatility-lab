# Operator runbook

Run `python -m volatility_lab --health` for local JSON diagnostics. Exit 0 means every check passed, 1 means missing, partial or stale inputs, and 2 means an invalid input, unreadable database or invalid configuration. The command inspects the configured store without creating it. Snapshot age describes the most recent saved capture and can exceed two minutes between sessions. Check the public capture timestamp on volatilitylab.in separately.

| Condition | Action |
| --- | --- |
| New workstation reports missing data | Start the desk once to initialize dated reference files. Select an existing archive with `VOL_LAB_ARCHIVE_DIR` to import recorded captures. |
| Earlier capture gap | Keep the missing interval blank. Check that subsequent complete snapshots arrive and the broker timestamps advance. |
| Repeated failed reads | Check broker authentication and upstream availability. Transient failures are retried up to three times. After three failed read operations the source pauses for 60 seconds before a probe. Long Retry-After intervals are respected. |
| Partial archive sync | Inspect the latest sync status and confirm Drive has finished syncing. Retry with the original source archive. |
| SQLite error or legacy seed schema | Stop local writers. Preserve the old store and use a fresh absolute `VOL_LAB_DATA_DIR` to replay verified captures. Do not copy an active SQLite database file without its WAL. |
| Official input is stale | Check the Pages refresh step and the RBI/NSE source links. A successful endpoint response must also pass parser validation. |
| Reference checksum fails | Use the reference files and manifest from the same reviewed release. Retain existing runtime data until the reference bundle is verified. |

## Storage and replay

New local, test, staging and production stores default to per-profile directories in the user's application state directory. An existing local installation with a recognized database or archive manifest continues using its legacy storage. Set an absolute `VOL_LAB_DATA_DIR` to choose the destination explicitly. Stop writers before switching an existing workstation. Private Drive captures remain at their original location.

The reference manifest records SHA-256 of each bundled text artifact with LF-normalized line endings. Startup checks the bundle and copies only missing files. Reference initialization preserves existing runtime files. Explicit archive replay rebuilds derived observations from their captured broker values. Update the manifest together with an intentional reference release. Daily official-input deployment writes to a separate Pages artifact.

Run `python scripts/fetch_market_data.py --status` to inspect. Run `--seed` explicitly to initialize the observation schema and replay validated complete captures in filename order. The command reports the number of archives examined, invalid archives and processed rows. Replay accepts earlier captures without a session flag only within a verified regular session and checks the underlying quote timestamp. Current web captures require the collector session flag. Summary JSON containing derived IV does not qualify as an IV archive. Sync receipts record the importer version so older imports are reprocessed in batches when provenance logic changes.

## Configuration and credentials

The supported variables and defaults are in [.env.example](../.env.example). Export variables or use `VOL_LAB_ENV_FILE`. An optional analytics token enables broker reads. On Windows, the local credential store uses DPAPI. On other systems use environment or Streamlit secrets. `VOL_LAB_OFFLINE=true` disables direct broker and pricing refreshes and the desk's background cloud sync. Health checks make no network requests.

## Reproducible validation

Install the recorded uv version and run `uv sync --locked --extra dev`. For a dependency update, regenerate `uv.lock`, inspect the changes and run the Python matrix, dependency audit and wheel checks. The combined branch/statement coverage floor is 35%, raised from the original 31% baseline after startup, replay and property tests were added. CI publishes coverage XML for each Python version.

Python 3.11 is the minimum supported runtime. Python 3.10 resolves to vulnerable versions of Pillow and filelock whose available fixes require a newer interpreter. The CI matrix covers Python 3.11, 3.12 and 3.13.

## NSE derivatives calendar

The collector uses confirmed F&O special-session circulars ahead of the weekend rule. Its bundled 2026 holiday fallback is based on NSE/FAOP/71777 and the amendment NSE/FAOP/72262. The daily Upstox refresh supplies additional closures and special-session hours. A temporary outage preserves announced dates already in the same-year cache, and a failed refresh retries after five minutes. On an unverified weekend, the scheduler continues looking for a published session and surfaces the unavailable calendar without assuming normal hours. Add each year's official F&O holiday circular to `NSE_FO_HOLIDAYS` and use exact hours from the separate circular for each special session in `SPECIAL_SESSIONS`. The operational override is `SPECIAL_SESSIONS_JSON`. [Annual NSE circular](https://nsearchives.nseindia.com/content/circulars/FAOP71777.pdf) · [January amendment](https://nsearchives.nseindia.com/content/circulars/FAOP72262.pdf) · [Muhurat hours](https://archives.nseindia.com/content/circulars/FAOP76940.pdf)

## Collector runtime

Google allows 90 minutes of trigger runtime per user per day for consumer accounts and six hours for eligible Google Workspace accounts. The provider's quota resets 24 hours after the first request and is shared with other triggers owned by the same user. Trial Workspace accounts have additional restrictions. The collector measures its own work in a 24-hour window, so its estimate can differ from Google’s usage. [Google Apps Script quotas](https://developers.google.com/apps-script/guides/services/quotas)

By default the collector admits new snapshots below 80 minutes of recorded work and admits scheduled EOD attempts below 85 minutes. The five-minute difference reserves admission capacity for EOD. The additional five minutes below Google's allowance provides headroom for an in-flight run and other trigger overhead. These are start thresholds: a running capture can finish above its threshold. Reconciliation keeps its eight-minute sub-budget and also stops when total recorded usage reaches the smaller of 55 minutes and the snapshot threshold.

A regular session has 386 scheduled minute slots from 09:15 through 15:40 IST inclusive in November 2026. An 80-minute snapshot allowance supports at most 12.44 seconds per slot on average before other recorded work. Check the budget window, status field `runtime_policy`, and actual trigger execution durations when assessing coverage. The former 75-minute guard paused on 13 November 2026 at 13:14 IST; changing admission thresholds preserves recorded usage and the original window end.

The first closing-candle attempt starts five minutes after the derivatives close (15:45 IST under the November 2026 regular schedule). If the broker has not published the close, retries run five minutes apart for the first half hour, then every 30 minutes. The two underlying index requests include enough daily closes for HV30. The website shows closing HV separately from the prior-close inputs used by intraday paper strategies.

Set Script Property `COLLECTOR_RUNTIME_MINUTES` to a positive whole number to select a total admission threshold. Values above the tier maximum are capped, and a missing or invalid value selects the tier maximum. If the total is five minutes or less the entire budget is reserved for EOD. Only after confirming the trigger owner's eligibility for the six-hour allowance, set `COLLECTOR_QUOTA_TIER=verified_workspace` to select the Workspace maximum of 355 minutes (350 minutes for snapshots). Any other tier value selects the consumer maximum of 85 minutes.

Quote enrichment uses groups of up to 40 requests with at least one second between group starts. Each request respects the Apps Script URL limit and Upstox's 500-key limit. The same evaluation passes its validated ledger contents into the save operation, which writes those exact previous contents to the backup. This reduces repeated Drive reads and folder searches.

The next captured status reports fetch, evaluation and archive seconds. Measure a complete session before concluding that collection fits within the budget. Existing missing historical IV stays unavailable.

Provider limits are documented by [Google Apps Script](https://developers.google.com/apps-script/guides/services/quotas) and [Upstox](https://upstox.com/developer/api-documentation/rate-limiting/).

The regular F&O close moved from 15:30 to 15:40 on 3 August 2026. From 7 December the segment closes at 15:45, with expiring index contracts ceasing trading at 15:30. Muhurat on 8 November ran from 14:20 through 15:40. The strategy's 15:15 exit policy is independent of exchange close. [NSE August change](https://archives.nseindia.com/content/circulars/FAOP74467.pdf), [December NSE FAQ](https://nsearchives.nseindia.com/web/mediaattachment/2026-11/FAQs_for_CM_and_FO_FOR_CAS_2.0_20261109152618.pdf), [Muhurat circular](https://archives.nseindia.com/content/circulars/FAOP76940.pdf)

Every snapshot archives the full broker chain, including all reported Greeks and original untimestamped market data. By default the collector requests additional timestamped full quotes around ATM, at strategy and open-position strikes, and at strikes bracketing 10 and 25 delta. Set Script Property `OPTION_QUOTE_DEPTH=full` to request those quotes for every leg. Each accepted symbol/expiry chain pins its first session ATM strike. Both legs remain in the quote request after spot moves, and each archive record identifies the strike in `straddle_anchor_strike`. Missing broker quotes can still leave a gap. Capture status describes the market archive; `enrichment_status` and `forward_test_status` describe the independent pricing and ledger work. The live endpoint accepts `since` equal to the last complete snapshot's exact `retrieved_at` to return a compact freshness response when the file still matches.

Complete snapshot archives include up to 400 observations per pinned expiry in `straddle_history`. Each tuple contains the Greek capture time, snapshot time, mean broker IV (%), paired book midpoint sum (points), call quote time and put quote time. Timestamps are Unix milliseconds. Missing or stale inputs leave nulls. A six-hour session cache carries earlier archived points into later captures; eviction or a failed cache update can shorten the visible series. Each retained archive preserves its own history.

The live payload's `daily_history` retains the latest valid same-day broker call/put ATM IV average for the nearest listed expiry strictly after the session. A missing ATM leg, stale timestamp or partial capture preserves the prior valid observation. Each row identifies the actual archive basename, strike, expiry, timestamp and risk inputs. Current rows share the existing snapshot-status write and roll into half-month partitions on the next session. The response includes up to one year of retained observations; earlier unindexed archives require explicit replay.
