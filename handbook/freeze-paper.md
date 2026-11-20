# Forward paper engine and reconciliation

Audit base: `13ff5a86f84d8579b9a7283eff46e5b105e17184` on `master`. File references for the fixes refer to the accompanying changes. All amounts are INR. This is a paper execution audit.

## The two historical ledgers disagree

The bundled `volatility_lab/data/forward_test_ledger.json` (header at line 1) had 20 open and 73 closed positions and was dated 17 Nov. The static public fallback in `docs/live_state.json:1` had 20 open and 73 closed records in the audited checkout. The live Apps Script ledger read through the dedicated `action=forward_ledger` endpoint was dated **20 Nov 2026 11:26:48.242 IST**. That response yielded 36 open (8 marked as event-quote validated, 28 legacy), 36 closed (10 event-quote validated, 26 legacy) and 1,160 minute portfolio points. These are counts at that instant. An unverified record must remain in the audit trail and outside the performance series.

| ID comparison | Bundled | Cloud | Common | Bundled only | Cloud only |
|---|---:|---:|---:|---:|---:|
| Open | 20 | 36 | 0 | 20 | 36 |
| Closed | 73 | 36 | 29 | 44 | 7 |

Replacing the OPEN prefix with FWD did not match any of the 20 bundled open positions to cloud closes. A newer timestamp is insufficient to reconcile the two books. The replica refused to replace a copy of the bundled ledger (CLI status 1). Its SHA-256 stayed `dfeeb8bf32c9d770dae198ff15012eca30fc51c74c5d0d7a9e5a6bb955cee641`. The independently created cloud replica had SHA-256 `9ceefe82ed30bd780a243adff9b27aa16cf1fa152d00cfcb01b2093df3863ab0`. Historical union, P&L aggregation across these two books, and reclassification of their common IDs require statement-level adjudication. The audit did not rewrite either book.

## Ten independently matched closes under the prior policy

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

- The Python entry point runs a read-only cloud request and an atomic local sync (`volatility_lab/backtest/forward_test.py:1475`, compare-and-swap at `:573`). The pre-patch implementation ended after returning its scorecard and executing the file did nothing. A source capture can be passed explicitly when the relay is unavailable. Equal inputs cause no write. A rollback, conflicting close, duplicate, lost position or competing writer fails without changing the previous mirror. A successful sync does not publish a new website or replace the cloud ledger.
- Entry requires the strategy's specified report minute, the same minute on the enriched chain, and a prior completed HV observation where required (`volatility_lab/backtest/forward_test.py:72`, `:104`, `:1117`, `:1131`, `:1175`). The validator rejects negative quote ages and enforces positive, uncrossed two-sided books and side-specific quantity (`:191`). Nearest listed expiry is selected before rule evaluation. A failed front rule does not open a later expiry. Held contracts continue to be marked by their original expiry. The policy version, available-at timestamp, entry legs and scheduled exit are persisted at entry (`:1183`).
- Research-v1 S1/S4 can carry until the previous exchange session's 15:15 deadline. S2/S5 close on the entry session. S3/S6 observe only. Short exchange sessions use an earlier deadline when necessary (`volatility_lab/core/freeze_policy.py:9`, `volatility_lab/backtest/forward_test.py:801`). SL and TP check the full executable liquidation value including any gap through the threshold. A stale mark defers exit. Expired contracts with no executable close go to reconciliation. The next fresh quote can close an overdue position even if no capture ran at its original deadline (`:815`, `:869`, `:939`). A new policy cannot change the entry date, expiry or fills of a legacy trade.
- Replay tracks the actual quote times and prices at each fresh mark (`:930`), the session and timestamp of exit (`:969`), and suppresses repeated or out-of-order snapshots (`:1034`). Regulatory rates for the exit leg use its actual date. Stored fill attribution is point-in-time. Daily aggregate curves containing a carried position represent lifetime open MTM unless explicitly rebased to the prior close. They must not be marketed as daily fund returns.
- Fixture regressions exercise SL, TP, stale depth, dated HV, idempotence, carry and missed close (`tests/integration/test_freeze_paper.py:9`, `:39`, `tests/integration/test_execution_safety_fixes.py:103`).

## Isolated replay from the private archive

Processing 295 complete 19 Nov snapshots, starting with `2026-11-19_snapshot_091544_complete.json.gz` and ending at `2026-11-19_snapshot_141043_complete.json.gz`, produced a single NIFTY S2 in the 23 Nov expiry. It entered at **09:25:44.818 IST**, hit its target at **14:10:47.215 IST**, and recorded **285** liquidation marks. Gross **6,344.00**, fees **152.66**, net **6,191.34**. This is a retrospective pipeline check in memory. It is not one of the ten pre-change paper closes above, and provides no independent out-of-sample evidence for research-v1.

## Daily mirror and publication

From the repository root, set `VOL_LAB_DATA_DIR` to an **absolute dedicated empty directory** on first adoption (keep the bundled archive separately) and run:

```powershell
python -m volatility_lab.forward_test --sync
```

Alternatively pass `--data-dir D:\paper-research-mirror`. The compatibility invocation `python volatility_lab/forward_test.py --sync` uses the same implementation. For an offline read use `--source <private ledger.json or complete snapshot.json.gz>`. In the browser match the cloud timestamp, select **All expiries** and **All strategies**, and compare verified closed IDs, entry/exit legs and net values to this mirror. Unknown historical front-scope flags are excluded under Front and remain visible under All. Retain unresolved and legacy counts separately.

The website receives cloud updates independently. Its static fallback is built from the bundled ledger and deliberately labelled dated archive (`scripts/publish_live_state.py:28`); the builder writes both HTML entries and `docs/live_state.json` together (`:87`). Its forecast refresh also stages a bundled ledger (`scripts/refresh_forecasts.py:70`). Never publish the confidential raw mirror into the public tree or resolve differing IDs by replacing one historical file. Keep verified prospective evidence in the dedicated research mirror, scoped by policy version.
