# Volatility Lab — Apps Script Setup & Quota Recovery

## Problem: Quota Exhaustion Causing Missed Capture Days

The previous `Code.gs` had these quota-killing issues:

| Issue | Impact | Fix Applied |
|-------|--------|-------------|
| Daily runtime budget capped at 75 min | Hits Google's ~90 min/day free-tier limit before market close | Raised to 85 min consumer default, 5 min EOD reserve |
| No `PURE_SNAPSHOT_CAPTURE_ONLY` mode | Every capture ran heavy forward-test enrichment (~15s/execution) | Added mode flag; pure capture runs ~2s/execution |
| `doGet` read from Drive every call (no cache) | Repeated Drive/Gzip reads burned quota on website polls | Added 55-second CacheService layer |
| `EOD_PENDING` grew unbounded | ScriptProperties 9 KB/key, 500 KB total exhausted | Added 30-day cap with overflow archival |
| `+1000ms` padding per execution | Burned ~7 min/day on padding alone | Removed |
| 401/403 triggered sequential retry | Doubled failed calls when token expired | Skip retry on auth errors |
| `weeklyCleanup` trigger referenced but function missing | Trigger install errors | Removed reference |
| `doGet` scanned first 60 files unsorted | Returned stale snapshots, wasted Drive API calls | Uses `LATEST_SNAPSHOT_FILE_ID` + sorted scan fallback |

---

## Step 1: Set Script Properties (Required)

Open your Google Apps Script project at:
https://script.google.com/d/1_nNJrwJaGMmtctFJYhZudef3rCEtRiZRM9fZqn8BHnI-fnAMupLNqk-A/edit

Go to **Project Settings** (gear icon) → **Script Properties** and add:

| Property | Value | Notes |
|----------|-------|-------|
| `UPSTOX_TOKEN` | Your Upstox access token | Required for API access |
| `WEBHOOK_KEY` | A secret string of your choice | Protects mutation actions (manual_download, backfill_eod) |
| `COLLECTOR_QUOTA_TIER` | `consumer_default` (or `verified_workspace`) | `verified_workspace` raises budget to 355 min |
| `COLLECTOR_RUNTIME_MINUTES` | `85` | Daily budget in minutes (max 85 consumer, 355 workspace) |
| `PURE_SNAPSHOT_CAPTURE_ONLY` | `true` | **Strongly recommended** — defers forward-test pricing to post-capture, cuts execution to ~2s |

### Recommended minimal setup for quota recovery:

```
UPSTOX_TOKEN = <your-token>
WEBHOOK_KEY = <generate-a-random-secret>
COLLECTOR_QUOTA_TIER = consumer_default
PURE_SNAPSHOT_CAPTURE_ONLY = true
```

---

## Step 2: Deploy the Updated Code

1. Copy the entire contents of `Code.gs` into your Apps Script project, replacing the old code.
2. Copy `appsscript.json` (access changed from `ANYONE_ANONYMOUS` to `MYSELF` — read-only snapshot still works via the deployed web app URL).
3. Click **Deploy** → **New deployment**.
4. Select **Web app** as the deployment type.
5. Set **Execute as**: `Me` (USER_DEPLOYING).
6. Set **Who has access**: `Only myself` (matches `MYSELF` in appsscript.json).
7. Click **Deploy**.
8. Copy the deployed web app URL — update your `sync_config.json` `web_app_url` if needed.

---

## Step 3: Install the Trigger

Run `installIntradaySchedule()` from the Apps Script editor (after a successful `testCapture()`).

This installs a 1-minute time-driven trigger for `scheduledCapture`.

---

## Step 4: Backfill Missed Data

You mentioned missing 2 days + yesterday's data. Run `backfillAllMissingEod_2026_11_04_to_08()` to backfill Nov 4-8 (excluding weekends). For any other missing day, call the web app with:

```
GET <your-web-app-url>?action=backfill_eod&key=<WEBHOOK_KEY>
```

Or run `capture_('eod', { target_day: 'YYYY-MM-DD' })` from the editor for a specific day.

---

## Step 5: Verify

Run `auditStatus()` from the editor to check:
- `COLLECTOR_STATUS` — should show `snapshot complete` or `eod complete`
- `RUNTIME_BUDGET` — should be well under the daily limit
- `SUCCESS_SNAPSHOT` / `SUCCESS_EOD` — should show today's date

---

## Step 6: Monitor Daily

Add a daily reminder to check `auditStatus()` output. If `RUNTIME_BUDGET.ms` approaches the limit, the collector will pause captures for the rest of the day (EOD reserve preserved).

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `Broker HTTP 401` | UPSTOX_TOKEN expired | Refresh token in Script Properties |
| `Daily runtime budget reached` | Too many captures in one day | Wait until tomorrow; budget resets on rolling 24h window |
| `No snapshot files found` | No archives saved yet | Run `testCapture()` first |
| `Unauthorized` on web app | Missing/wrong `WEBHOOK_KEY` | Add key to Script Properties and pass `?key=<value>` |
