# Operator runbook

Run `python -m volatility_lab --health` for local JSON diagnostics. Exit 0 means every check passed, 1 means missing, partial or stale inputs, and 2 means an invalid input, unreadable database or invalid configuration. The command inspects the configured store without creating it. Snapshot age describes the most recent saved capture and can exceed two minutes between sessions. Check the public capture timestamp on volatilitylab.in separately.

| Condition | Action |
| --- | --- |
| New workstation reports missing data | Run `python -c "from volatility_lab.storage.references import initialize_references; from volatility_lab.runtime.settings import data_dir; initialize_references(data_dir())"` to initialize dated reference files. Select an existing archive with `VOL_LAB_ARCHIVE_DIR` to import recorded captures. |
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

The supported variables and defaults are in [.env.example](../.env.example). Export variables or use `VOL_LAB_ENV_FILE`. An optional analytics token enables broker reads. On Windows, the local credential store uses DPAPI. On other systems use environment variables. `VOL_LAB_OFFLINE=true` disables direct broker and pricing refreshes. Health checks make no network requests.

## Reproducible validation

Install the recorded uv version and run `uv sync --locked --extra dev`. For a dependency update, regenerate `uv.lock`, inspect the changes and run the Python matrix, dependency audit and wheel checks. The combined branch/statement coverage floor is 35%, raised from the original 31% baseline after startup, replay and property tests were added. CI publishes coverage XML for each Python version.

Python 3.11 is the minimum supported runtime. Python 3.10 resolves to vulnerable versions of Pillow and filelock whose available fixes require a newer interpreter. The CI matrix covers Python 3.11, 3.12 and 3.13.

## NSE derivatives calendar

The collector uses confirmed F&O special-session circulars ahead of the weekend rule. Its bundled 2026 holiday fallback is based on NSE/FAOP/71777 and the amendment NSE/FAOP/72262. The daily Upstox refresh supplies additional closures and special-session hours. A temporary outage preserves announced dates already in the same-year cache, and a failed refresh retries after five minutes. On an unverified weekend, the scheduler continues looking for a published session and surfaces the unavailable calendar without assuming normal hours. Add each year's official F&O holiday circular to `NSE_FO_HOLIDAYS` and use exact hours from the separate circular for each special session in `SPECIAL_SESSIONS`. The operational override is `SPECIAL_SESSIONS_JSON`. [Annual NSE circular](https://nsearchives.nseindia.com/content/circulars/FAOP71777.pdf) · [January amendment](https://nsearchives.nseindia.com/content/circulars/FAOP72262.pdf) · [Muhurat hours](https://archives.nseindia.com/content/circulars/FAOP76940.pdf)

## Collector runtime

Google allows 90 minutes of trigger runtime per user per day for consumer accounts and six hours for eligible Google Workspace accounts. The provider's quota resets 24 hours after the first request and is shared with other triggers owned by the same user. Trial Workspace accounts have additional restrictions. The collector measures its own work in a 24-hour window, so its estimate can differ from Google’s usage. [Google Apps Script quotas](https://developers.google.com/apps-script/guides/services/quotas)

The snapshot-only trigger uses the default 85-minute consumer admission threshold for the entire regular or special session. Both the active rolling window and a lower explicit operator limit survive deployment. Check `capture_profile`, `runtime_policy`, `last_snapshot_slot` and `snapshot_gap` on the status endpoint. The private Drive archive records the actual captures. A full regular session in November 2026 has 386 slots and requires average accounted runtime below 13.2 seconds per slot.

The Lab performs chart, risk, replay and paper calculations after archive creation. Index candle publication runs in the Pages job. Legacy EOD archives remain available for historical reads. [Collector operations](../volatility_lab/cloud/apps_script/SETUP.md)
