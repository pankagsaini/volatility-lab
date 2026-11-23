# Google Apps Script capture

Active project: https://script.google.com/home/projects/1_nNJrwJaGMmtctFJYhZudef3rCEtRiZRM9fZqn8BHnI-fnAMupLNqk-A/edit
Private archive: https://drive.google.com/drive/folders/1SCNVxgK3WAbEyQDRzbb-t0PsPaq477i1

## Minute snapshot collector

The single one-minute `scheduledCapture` trigger records the configured NIFTY and BANKNIFTY option chains, broker IV and Greeks, spot and futures quotes, and selected timestamped option books. Each minute is stored as a private gzip archive. The Lab reads these files for paper trading and replay. The website reads the most recent complete snapshot using the existing web app URL.

Save `Code.gs` in the existing project to update the trigger code. Edit the existing web app deployment and choose a new version to retain its URL. Check `?action=status` for `capture_profile: minute_snapshots_only`, `interval: 1`, and the `scheduledCapture` trigger. An existing minute trigger can be reused. Run `installIntradaySchedule` to replace duplicate or legacy collector triggers when needed.

Keep the Upstox token in the existing private script properties. Calendar and catalogue reads support regular and special sessions. Capture skips overlapping executions. `LAST_SNAPSHOT_SLOT` and `snapshot_gap` track collector slots; Drive filenames and broker timestamps remain the source for historical verification.

The default 85-minute rolling allowance funds one-minute Upstox captures. The scheduler protects 90 seconds for the scheduled exit and another 90 seconds for the final minute. With the default allowance, ordinary captures pause at 82 minutes of recorded usage. At 15:15 IST they can resume up to 83.5 minutes, and at the final scheduled minute they can use the full 85 minutes. An earlier close moves the first release to 15 minutes before close, bounded by the opening minute. Each reserve is capped at a quarter of any smaller configured budget.

At 386 scheduled slots, 85 minutes averages 13.2 seconds per slot before other overhead. A reserve gives the regular trigger an opportunity to collect a fresh book. Trigger delivery, broker availability, and time spent waiting on Drive can still affect coverage. Verify the exit and closing minutes against archived timestamps and leg quotes. A delayed capture keeps its actual timestamp.

The Lab derives HV from dated daily closes and rebuilds chart history from saved captures with `scripts/publish_snapshot_history.py`. The Pages publisher fetches completed index candles directly from Upstox. `scripts/run_paper_service.py` consumes the private archive on the Lab host. Missing IV and missing books remain gaps.
