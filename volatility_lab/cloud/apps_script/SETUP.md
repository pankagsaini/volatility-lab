# Google Apps Script capture

Active project: https://script.google.com/home/projects/1_nNJrwJaGMmtctFJYhZudef3rCEtRiZRM9fZqn8BHnI-fnAMupLNqk-A/edit
Private archive: https://drive.google.com/drive/folders/1SCNVxgK3WAbEyQDRzbb-t0PsPaq477i1

## Minute snapshot collector

The single one-minute `scheduledCapture` trigger records the configured NIFTY and BANKNIFTY option chains, broker IV and Greeks, spot and futures quotes, and selected timestamped option books. Each minute is stored as a private gzip archive. The Lab reads these files for paper trading and replay. The website reads the most recent complete snapshot using the existing web app URL.

Save `Code.gs` in the existing project to update the trigger code. Edit the existing web app deployment and choose a new version to retain its URL. Check `?action=status` for `capture_profile: minute_snapshots_only`, `interval: 1`, and the `scheduledCapture` trigger. An existing minute trigger can be reused. Run `installIntradaySchedule` to replace duplicate or legacy collector triggers when needed.

Keep the Upstox token in the existing private script properties. Calendar and catalogue reads support regular and special sessions. Capture skips overlapping executions. `LAST_SNAPSHOT_SLOT` and `snapshot_gap` track collector slots; Drive filenames and broker timestamps remain the source for historical verification.

The entire default 85-minute rolling admission allowance is available to minute captures. At 386 scheduled slots that is 13.2 seconds per slot on average before other overhead. Google accounts for actual trigger time. Measure scheduled slots, complete archive files and execution durations over a full market session.

The Lab derives HV from dated daily closes and rebuilds chart history from saved captures with `scripts/publish_snapshot_history.py`. The Pages publisher fetches completed index candles directly from Upstox. `scripts/run_paper_service.py` consumes the private archive on the Lab host. Missing IV and missing books remain gaps.
