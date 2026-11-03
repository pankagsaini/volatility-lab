# My First Algo

## Everyday use

- **Open Dashboard.cmd** — opens Volatility Lab.
- **Turn Off Dashboard.cmd** — closes the dashboard. The historical downloader has its own scheduled task.
- **Documentation/** — your new-chat handover, inventory, upload ZIP and screenshots.
- **Volatility Lab/** — the working application and all market data. Keep this folder at its current location.

## If you need help

Latest audit and future priorities: `Documentation/DEVELOPMENT_PLAN_2026-10-23.md`.

Upload `Documentation/VOLATILITY_LAB_NEW_CHAT.zip` in a new chat. It contains project context and an inventory, not the actual market archive or application. Keep the entire `Volatility Lab` folder.

For cloud sync problems, open `Volatility Lab/CLOUD HELP.html`. The repair launcher is `Volatility Lab/REPAIR CLOUD SYNC.cmd`.

The laptop daily collector automatically retries failures up to three times at five-minute intervals. Its latest status is `Volatility Lab/data/daily_collector/status.json`; run history is `runs.jsonl` in the same folder. This is separate from Google cloud capture. Catalogue failure diagnostics are in `Volatility Lab/data/upstox/catalogue_refresh_events.jsonl`.

The incomplete one-minute download resumes through the existing Windows scheduled task after you sign in to Windows and internet is available. Its latest progress is in `Volatility Lab/data/backtest_1minute/status.json`. Do not delete its queue, raw files or lock files while it runs.

## Inside Volatility Lab

| Location | Purpose |
| --- | --- |
| `dashboard.py`, other `.py` files | Application source; these files work together and must stay in place |
| `data/` | Market archives, calculations, download progress and saved backtests |
| `cloud/apps_script/` | Google cloud capture and retention setup |
| `logs/` | Diagnostic logs |
| `.runtime/`, `.dependencies/` | Bundled Python and installed libraries |
| `.auth/` | Encrypted Upstox token for this Windows account; never share |
| `.streamlit/` | Dashboard configuration |
| `test_*.py` | Regression checks used when changing the lab |

The folders beginning with a dot and the Python files are application internals. Use the two dashboard launchers at the top level for normal use.

## Where the data lives

| Data folder | Contents |
| --- | --- |
| `backtest_1minute/` | Ongoing historical options/underlying minute download and durable queue |
| `backtest_futures/` | Futures backtesting history |
| `backtest_runs/` | Saved strategy results |
| `google_archive/` | Permanent local copies of Google captures and sync receipts |
| `upstox/` | Upstox catalogues and history |
| `futures/` | Futures chart inputs and saved history |
| `underlying_history/` | Dated index price snapshots |
| `quote_archive/`, `quote_quality/` | Recorded option quotes and quality evidence |
| `charges/`, `historical_inputs/`, `input_history/` | Cost and pricing-input records |
| `audit/` | Maintenance records |

## Organisation completed 22 October 2026

Handover documents are grouped under Documentation; presentation screenshots are under Documentation/Screenshots. No application paths, scheduled tasks or unique market observations were removed.

Thirty additional byte-identical historical CSV copies now share storage through Windows hard links. Their filenames remain because the lab uses dated snapshots. Sixty-one copies already shared storage. This removes about 666 KiB of redundant file content, not a large disk-space gain. The large archives are retained deliberately.

Current quote files, charge records, broker catalogues and dividend source files can have identical content but different roles. They are retained. The actively growing minute archive was excluded from deduplication. Details are in `Volatility Lab/data/audit/folder_dedup_completed.json`.
