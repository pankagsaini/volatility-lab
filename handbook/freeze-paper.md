# Forward paper engine and reconciliation

Audit base: `13ff5a86f84d8579b9a7283eff46e5b105e17184` on `master`. File references for the fixes refer to the accompanying changes. All amounts are INR. This is a paper execution audit.

## The two historical ledgers disagree

The bundled `volatility_lab/data/forward_test_ledger.json` (header at line 1) had 20 open and 73 closed positions and was dated 17 Nov. The static public fallback in `docs/live_state.json:1` had 20 open and 73 closed records in the audited checkout. Before collector cutover, the legacy Apps Script ledger read through the dedicated `action=forward_ledger` endpoint was dated **20 Nov 2026 11:26:48.242 IST**. That response yielded 36 open (8 marked as event-quote validated, 28 legacy), 36 closed (10 event-quote validated, 26 legacy) and 1,160 minute portfolio points. These are counts at that instant. An unverified record must remain in the audit trail and outside the performance series.

| ID comparison | Bundled | Cloud | Common | Bundled only | Cloud only |
|---|---:|---:|---:|---:|---:|
| Open | 20 | 36 | 0 | 20 | 36 |
| Closed | 73 | 36 | 29 | 44 | 7 |

Replacing the OPEN prefix with FWD did not match any of the 20 bundled open positions to cloud closes. A newer timestamp is insufficient to reconcile the two books. The replica refused to replace a copy of the bundled ledger (CLI status 1). Its SHA-256 stayed `dfeeb8bf32c9d770dae198ff15012eca30fc51c74c5d0d7a9e5a6bb955cee641`. The independently created cloud replica had SHA-256 `9ceefe82ed30bd780a243adff9b27aa16cf1fa152d00cfcb01b2093df3863ab0`. Historical union, P&L aggregation across these two books, and reclassification of their common IDs require statement-level adjudication. The audit did not rewrite either book.

## Ten independently matched closes under the prior policy

On 23 November both preserved exports still matched the SHA-256 values above. A second comparison used trade ID, instrument, strategy, date, leg contract and side, multiplier, entry and exit prices, gross, fees and net. Of the 29 shared closed IDs, 26 had matching recorded terms and were labeled legacy-unverified in the cloud export. Three IDs had conflicting terms. Their net paper amounts were:

| Closed ID | Bundled net ₹ | Cloud net ₹ |
|---|---:|---:|
| FWD-20261116-BAN-0125-S4 | −2,562.92 | −20,732.79 |
| FWD-20261116-NIF-1117-S3 | +1,414.05 | +1,375.09 |
| FWD-20261117-BAN-0125-S4 | +12,221.47 | −15,747.45 |

The raw-book checks below support the cloud economics for these three under the earlier policy. The bundled alternatives and the 51 single-source closes remain preserved for historical investigation. All historical rows stay outside the prospective series. To reproduce the classification locally, use `python scripts/compare_paper_archives.py BUNDLED.json CLOUD.json --show-ids`.

The check matched each leg's contract/expiry, side-specific bid or ask, recorded quote timestamp and displayed executable depth for the full signed quantity on both entry and exit. Gross was recomputed as the sum of signed price changes × leg multiplier × lot size. The engine's dated charges recomputation uses each side and premium (implementation `volatility_lab/backtest/forward_test.py:419`, `volatility_lab/backtest/broker_charges.py:31`). Each close tied to the ledger within ₹0.01. Across all ten records, gross was **−69,899.50**, charges **3,779.27** and net **−73,678.77**. The validation confirms executable displayed books, not exchange fills, a guaranteed simultaneous basket fill, or a realized broker account statement. These trades predate research-v1.

Private file references below use the stem `YYYY-MM-DD_snapshot_HHMMSS_complete.json.gz`. The hexadecimal suffix is the first 12 characters of the SHA-256 of the compressed file. Leg records and raw market payloads remain private.

| Trade ID | Entry capture (date/time, hash) | Exit capture (date/time, hash) |
|---|---|---|
| FWD-20261119-NIF-1222-S6 | 19 Nov 145744 · 9f58cd800224 | 19 Nov 152742 · 90155e81b824 |
| FWD-20261119-NIF-1222-S4 | 19 Nov 150643 · dc95e15cb5cb | 19 Nov 151643 · 411fa4572087 |
| FWD-20261119-NIF-1222-S2 | 19 Nov 145243 · 8cac1313d66d | 19 Nov 151542 · b52e0f2b9179 |
| FWD-20261117-BAN-1123-S4 | 17 Nov 093042 · 11f0ad1110a0 | 19 Nov 151542 · b52e0f2b9179 |
| FWD-20261117-BAN-0125-S4 | 17 Nov 101645 · b6bde8cc3c20 | 17 Nov 110843 · a51d7ab74251 |
| FWD-20261116-NIF-1201-S4 | 16 Nov 093642 · ae9bc013efbb | 16 Nov 151942 · 1f6303f3640f |
| FWD-20261116-BAN-1123-S4 | 16 Nov 133243 · 0d21f97028c2 | 16 Nov 151743 · 03d939a27981 |
| FWD-20261116-NIF-1117-S3 | 16 Nov 092042 · 877c860ace28 | 16 Nov 151543 · 6ec84d5ca385 |
| FWD-20261116-NIF-1117-S1 | 16 Nov 092042 · 877c860ace28 | 16 Nov 151543 · 6ec84d5ca385 |
| FWD-20261116-BAN-0125-S4 | 16 Nov 102942 · 5405eb5652d8 | 16 Nov 113042 · b7526104e055 |

One BANKNIFTY S4 booked under the prior intraday policy entered on 17 Nov and closed on 19 Nov. Matching prices proves the economics of those two observations. It does not prove a timely scheduled exit on 17 Nov. The bundled split and this delay are independent reasons to avoid presenting an uninterrupted historical forward track record.

## What the patch enforces

- The pre-patch Python module ended after returning its scorecard, so executing the file did nothing. The prospective worker now owns its own ledger, reads existing timestamped captures on the private Drive mirror and never calls the relay or broker. See `volatility_lab/backtest/paper_worker.py:82`, `:117` and `:163`. The separate historical comparison import accepts only an explicit local ledger/snapshot file (`volatility_lab/backtest/forward_test.py:1480`). It refuses rollback, changed closes, duplicates and missing trades. A successful historical import cannot publish a new cohort.
- Entry requires the strategy's specified report minute, the same minute on the enriched chain, and a prior completed HV observation where required (`volatility_lab/backtest/forward_test.py:72`, `:104`, `:1117`, `:1131`, `:1175`). The validator rejects negative quote ages and enforces positive, uncrossed two-sided books and side-specific quantity (`:191`). Nearest listed expiry is selected before rule evaluation. A failed front rule does not open a later expiry. Held contracts continue to be marked by their original expiry. The policy version, available-at timestamp, entry legs and scheduled exit are persisted at entry (`:1183`).
- Research-v1 S1/S4 can carry until the previous exchange session's 15:15 deadline. S2/S5 close on the entry session. S3/S6 observe only. Short exchange sessions use an earlier deadline when necessary (`volatility_lab/core/freeze_policy.py:9`, `volatility_lab/backtest/forward_test.py:801`). SL and TP check the full executable liquidation value including any gap through the threshold. A stale mark defers exit. Expired contracts with no executable close go to reconciliation. The next fresh quote can close an overdue position even if no capture ran at its original deadline (`:815`, `:869`, `:939`). A new policy cannot change the entry date, expiry or fills of a legacy trade.
- Replay tracks the actual quote times and prices at each fresh mark (`:930`), the session and timestamp of exit (`:969`), and suppresses repeated or out-of-order snapshots (`:1034`). Regulatory rates for the exit leg use its actual date. Stored fill attribution is point-in-time. Daily aggregate curves containing a carried position represent lifetime open MTM unless explicitly rebased to the prior close. They must not be marketed as daily fund returns.
- Fixture regressions exercise SL, TP, stale depth, dated HV, idempotence, carry and missed close (`tests/integration/test_freeze_paper.py:10`, `:60`, `tests/integration/test_execution_safety_fixes.py:103`).

## Isolated replay from the private archive

Processing 295 complete 19 Nov snapshots, starting with `2026-11-19_snapshot_091544_complete.json.gz` and ending at `2026-11-19_snapshot_141043_complete.json.gz`, produced a single NIFTY S2 in the 23 Nov expiry. It entered at **09:25:44.818 IST**, hit its target at **14:10:47.215 IST**, and recorded **285** liquidation marks. Gross **6,344.00**, fees **152.66**, net **6,191.34**. This is a retrospective pipeline check in memory. It is not one of the ten pre-change paper closes above, and provides no independent out-of-sample evidence for research-v1.

The external worker was also run twice on 15 consecutive archived 19 Nov reports (09:15–09:29 IST), using isolated --replay storage. It consumed 15, quarantined zero and opened OPEN-20261119-NIF-1123-S2 at 09:25:44.818. It recorded five executable marks, the last at 09:29:48.636 with ₹55.25 gross and −₹85.33 net. A second run left the ledger hash unchanged. The public projection included the trade ID, its two legs and their quote times, and omitted source paths. Replay publication was disabled.

## Daily processing and publication

The prospective cohort starts on **23 November 2026**. Apps Script collects the one-minute Upstox snapshots. The standard GitHub-hosted Linux runner builds the dashboard's paper dataset at **10:49, 15:49 and 20:49 IST** every day, including special weekend sessions. GitHub controls when a scheduled run starts. The public timestamp is the last processed capture. A manual dispatch uses the same workflow and checkpoint. [Paper batch runs](https://github.com/pankagsaini/volatility-lab/actions/workflows/paper-batch.yml)

The dedicated Drive reader has Viewer access to the private archive folder. The worker reads complete captures chronologically, reconstructs risk inputs from dated index closes, checks executable quotes and applies each strategy at capture time. A batch handles up to 400 captures. If the runner starts late, it resumes at the saved cursor. If required prior-session closes are missing, it retries at the next run. Missing capture intervals remain visible as exceptions. A fresh quote at a later time cannot establish how a position traded inside a gap.

Each successful advance publishes two files together on the `paper-feed` branch. The public JSON contains paper holdings, dated marks and closes. The encrypted checkpoint retains the private processing state. The worker verifies the prior publication and the new checkpoint before a fast-forward push. An overlapping run cannot replace newer history. An unchanged run leaves the data commit intact. The site reads the published book and can use the copy bundled in its most recent successful Pages build.

The hosted workflow requires the repository secrets `PAPER_DRIVE_FOLDER_ID`, `PAPER_DRIVE_READER_JSON` and `PAPER_CHECKPOINT_KEY`. Preserve the checkpoint key when rotating the Drive credential. To check recovery, compare the batch run summary, public capture timestamp, processed count and closed IDs. The runtime does not need an Upstox token, a Windows task or a mounted Drive. Broker capture remains subject to its own credentials and quota.

For offline analysis, use `python -m volatility_lab.forward_test --sync --replay --start-date YYYY-MM-DD --archive-dir ARCHIVE --data-dir ISOLATED_DIR`. Historical replay uses a separate ledger and cannot publish prospective results. The historical comparison importer accepts `--sync --source FILE --data-dir SEPARATE_DIR`. Keep both historical books for reconciliation.

### Prospective controls and run evidence

New decision receipts are generated by the same call that evaluates the frozen rules. At 09:20 IST this covers S1, S4 and S5 on both indices. At 09:25 IST it covers S2. Each receipt binds the results to the SHA-256 of the uncompressed archive JSON. Earlier accepted minutes without receipts keep their original status. The archive may be replayed independently to investigate them.

**23 November morning check.** An isolated replay of the original 09:15–09:25 captures with the dated 20 November closing history reproduced the empty book. The front contracts for both NIFTY and BANKNIFTY expired on 23 November. All six 09:20 evaluations and both 09:25 evaluations failed their minimum DTE. The uncompressed source hashes were `fb40742e9978b459096d3e2e0f54ffe3968c16e0abcd0d9b9ece6b20f7d968c1` and `db405e689cd28d2cbe4aa203e06e36d119af331fdd205e6f7db44c339f22d18e`, respectively. The original prospective checkpoint remains unchanged for those decisions.

For every new entry and exit the worker independently compares the saved leg prices, side, quote timestamps and displayed quantities with the same archived report. At close it recomputes gross, dated fees and net. An inconsistency blocks publication. A fill after the scheduled exit minute retains its observed time and price and is identified as delayed. A position that expires without an executable exit requires settlement reconciliation, including when the report has no option chains.

The hosted publisher verifies the paper-feed root commit, the SHA-256 of the original empty export and the digest of the frozen entry rules. Restoring and advancing the private checkpoint also checks each position's policy, entry identity and raw capture references. A prior open position must survive as the same open position or as a close with the same entry terms.

Each hosted batch writes two tables to its GitHub Actions summary. Session coverage compares elapsed exchange minutes with complete and partial archive filenames. For the scheduled entry, exit and final minutes, the summary separately reports whether the worker accepted a report when it became available and whether the book recorded any paper entries or exits. This allows a completed broker capture with no qualifying trade to be distinguished from an absent minute.

The outcomes summary groups eligible closed paper P&L by underlying, holding period and the observed sign of entry IV minus prior-session HV30. It reports gross P&L, modeled fees, net P&L and the number of distinct exit sessions. Trades closed on the same day contribute one combined observation to the mean and its standard error. Days without eligible closes do not enter that statistic. Open marks, historical ledgers and gap-affected closes stay outside eligible results. A prospective close with known economics and an incomplete capture path is reported separately. Full-session continuity, overnight carry and the capacity to earn those results with investor capital require separate evidence. [Hosted batch reports](https://github.com/pankagsaini/volatility-lab/actions/workflows/paper-batch.yml)
