# S1–S6 expectancy and capacity review

**None of the six has demonstrated investable expectancy.** The assertion that all six lose money is also inaccurate. For example, the unconditional long straddle gained money at recorded last prices, but the original entry filters selected zero of those trades. Conditional S4 won three times, with no qualifying timestamped two-sided round trip among those wins. The observation unit for risk is a trading day. Both indices can move together.

**Audited origin.** GitHub default branch master at `13ff5a86f84d8579b9a7283eff46e5b105e17184`, original embedded trade rows at `docs/index.html:3291` on that SHA. The 216 one-lot counterfactuals cover 18 dates (21 Oct–18 Nov 2026), six structures and two indices. Each entry and exit was matched to its full compressed Upstox source via the public replay capture filename. All 216 original gross P&Ls and original fees reconciled to within one paisa at the originally assumed quantity. The 72 source fingerprints are in `handbook/freeze-source-hashes.csv:2`; the reconciliation guard is in `scripts/freeze_expectancy.py:147`.

**Lot-size correction.** 108 original NIFTY rows implied 75 units per lot although the archive header said 65. All 385 distinct option contracts in the 216 rows matched on symbol, contract expiry, strike and side in the union of the two official NSE contract masters. The exchange's `NewBrdLotQty` and `MinLot` are 65 NIFTY and 30 BANKNIFTY. Decode `XpryDt` as seconds from 1980-01-01 and `StrkPric` in paise. Gross, fee turnover, quantity and capital for the NIFTY archive were restated from raw leg marks. Example: 2 Nov NIFTY S1 gross ₹90 → ₹78; capital proxy ₹26,174 → ₹22,684. Fees were recomputed per executed leg, including flat brokerage. [21 October contract master](https://nsearchives.nseindia.com/content/fo/NSE_FO_contract_21102026.csv.gz) · [18 November contract master](https://nsearchives.nseindia.com/content/fo/NSE_FO_contract_18112026.csv.gz).

## What happened to the six historical structures

One lot per index, no additional slippage here. “Baseline” uses all 36 rows per strategy; “match” is the subset passing the archive's recorded entry filters. Amounts in rupees. Win rate and average winner/loser use net match P&L. Full precision, per-index breakdowns and distinct-day counts are in the referenced CSV lines.

| Structure | Baseline gross / fees / net | Matches | Matched gross / fees / net | Win % | Avg win / loss | CSV lines (baseline, match) |
|---|---:|---:|---:|---:|---:|---|
| S1 | -5,587.50 / 10,091.89 / -15,679.39 | 8 | 2,037.50 / 1,935.17 / 102.33 | 62.50 | 652.10 / -1,052.73 | 2, 218 |
| S2 | 20,023.50 / 6,435.65 / 13,587.85 | 0 | — / — / — | — | — / — | 38, 254 |
| S3 | -565.00 / 11,377.65 / -11,942.65 | 2 | 358.50 / 544.37 / -185.87 | 50.00 | 51.34 / -237.21 | 74, 290 |
| S4 | 14,521.75 / 11,673.03 / 2,848.72 | 3 | 5,846.75 / 789.67 / 5,057.08 | 100.00 | 1,685.69 / — | 110, 326 |
| S5 | 2,449.50 / 11,732.63 / -9,283.13 | 11 | 320.25 / 3,072.22 / -2,751.97 | 45.45 | 259.51 / -674.92 | 146, 362 |
| S6 | -27,980.50 / 12,160.23 / -40,140.73 | 4 | 538.00 / 1,248.23 / -710.23 | 25.00 | 2,057.92 / -922.72 | 182, 398 |

**Costs and execution.** The six geometries and exact original filters are at `scripts/rebuild_backtest_archive.py:55` and `:73`. The archive picks the first sufficiently complete entry up to ten minutes after the nominal clock and the last usable mark from 15:10 to 15:15:59. It values fixed strikes at LTP; it does not execute the measured path excursions as stops or targets (`:94`, `:420`, `:451`, `:506`). A joint trade at bid/ask is a different economic proposition. “Qualified L1” re-prices the same archived endpoints only when every leg has source-identified, nonfuture, ≤120-second quotes, ordered nonzero books and available size for each order. Buys cross the ask and sells cross the bid; no partial-leg fills. A captured book is an execution simulation and cannot establish queue position, simultaneity or an actual fill. The archive passed 28 of 216 recorded filter checks; **zero of those 28 had qualified L1 at both endpoints**. The two-sided subset of the unconditional baseline comprised 17 of 216. Filtered L1 performance is unmeasurable, including at zero added ticks (`scripts/freeze_expectancy.py:41`; `handbook/freeze-stress.csv:650` onward).

Options brokerage uses ₹20 per leg order; turnover-based sell STT 0.15%, buy stamp 0.003%, NSE fees including IPFT 0.03553%, SEBI ₹10/crore and 18% GST on brokerage, exchange and IPFT. These are simulated trades under the standard Upstox tariff. Reconciliation to a broker-issued contract note requires actual trades. Each additional tick adds ₹0.05 of adverse price per unit on both entry and exit; fees are recomputed on the slipped prices. `volatility_lab/backtest/broker_charges.py:18`, `:36`; `scripts/freeze_expectancy.py:62`. [Upstox published tariff](https://upstox.com/brokerage-charges/).

## Slippage, risk and uncertainty

Mean net rupees per archived filter match; final column gives the net P&L of the early twelve and later six dates at zero additional ticks. The split is chronological, descriptive and retrospective. A dash means there was no eligible observation.

| Structure | 0 ticks | 1 tick | 2 ticks | 5 ticks | Early / later net ₹ |
|---|---:|---:|---:|---:|---:|
| S1 | 12.79 | -7.94 | -28.68 | -90.88 | 102.33 / — |
| S2 | — | — | — | — | — / — |
| S3 | -92.94 | -104.92 | -116.92 | -152.89 | -185.87 / — |
| S4 | 1,685.69 | 1,646.72 | 1,607.75 | 1,490.84 | 3,667.03 / 1,390.05 |
| S5 | -250.18 | -272.34 | -294.51 | -361.01 | -446.70 / -2,305.27 |
| S6 | -177.56 | -211.28 | -245.01 | -346.19 | -1,030.47 / 320.24 |

All four tick levels include expectancy, profit factor, closing-equity max drawdown (₹), total net ROC (%), daily Sharpe and Sortino, separately for each index and pooled. See [historical stress grid](freeze-stress.csv) and [revised policy grid](freeze-policy-stress.csv). The fixed starting capital denominator for ROC is the maximum daily total of the one-lot proxies, aggregated across the two indices when both traded. The historical proxy is scenario-based (`scripts/rebuild_backtest_archive.py:136`); the production engine uses its own strategy allocation. Neither is broker span margin. Drawdown measures end-of-day equity and excludes intraday excursions. Sharpe uses mean / sample SD; Sortino uses mean / RMS of negative returns including zero downside on positive days. Both use active-date P&L on the same fixed capital, zero hurdle, no annualization. Undefined denominators and empty samples stay blank. These are conditional summaries, not full-calendar portfolio performance (`scripts/freeze_expectancy.py:73`). Selection by complete exit censors missing exits; the retrospective results cannot measure outage losses or overnight liquidation performance.

## Strategy disposition for frozen research rules

The rule ranges at `volatility_lab/data/freeze_policy.json:2` preserve the archived hypotheses. Entry selection uses `volatility_lab/core/freeze_policy.py:9`. Calendar DTE must be at least two for every enabled structure. Same-contract exit scheduling is in `volatility_lab/backtest/forward_test.py` (`scheduled_exit_at`); stops and targets run on executable captured basket marks at `exit_thresholds`. Slippage and delays can carry a realized loss beyond the threshold.

| Strategy | Economic diagnosis and disposition |
|---|---|
| **S1 VRP iron condor** | **Retain for positional paper research.** The gross premium among eight matches nearly disappears after charges; one added tick can erase it. Daily IV above realized volatility is a premium hypothesis over time. Flattening the spread the same afternoon measures only that day's underlying move and theta. Hold eligible defined-risk wings to the prior trading session before expiry or the predeclared risk exit. Overnight gaps remain material. |
| **S2 long ATM straddle** | **Retain for intraday paper research.** The baseline bought convexity profitably but the sufficiently cheap-volatility filter produced no historical archive matches. The trade has to earn back both premiums and crossing costs within the session; it can also lose through IV contraction. Keep the existing cheap-IV condition and defined debit risk. Do not extrapolate a baseline win to a signal. |
| **S3 post-event fly** | **Replace the automated trigger with observation only.** The existing VRP gate produced two last-price matches, whose aggregate gross failed to cover costs. Clock and high IV do not identify an event or its resolution. A potential replacement is a dated, ex-ante event-release IV-crush fly. Resume evaluation only with historical announcement time, executable pre/post books and an untouched forward sample. |
| **S4 skew-weighted condor** | **Retain for positional paper research.** The structure has a two-lot hedged put spread plus a one-lot hedged call spread. Three winners cannot estimate downside; the rule sells more exposure to downside gaps when put skew is high. Keep the skew + VRP test, measured against fixed strikes through scheduled exit. Do not describe it as naked ratio convexity. |
| **S5 intraday theta fly** | **Retain as an unproven intraday control.** Eleven matched observations generated ₹320.25 gross against ₹3,072.22 fees. The recorded average loser exceeded the average winner. Fixed wings cap the expiry loss, while short intraday gamma can overwhelm time decay. Keep the 15:15 horizon and executable risk exits; do not select a new width or delta on this sample. |
| **S6 basis/skew condor** | **Replace automated basis signals with observation only.** Four matches earned insufficient gross to cover fees; the fixed weighted call/put verticals do not lock a parity dislocation. Testing an actual basis strategy requires synchronized executable futures and same-expiry option-leg books, carry and hedge costs. The original archive only filtered VRP and RR25. |

### Revised-policy walk through the raw tape

The same Python state machine consumed the exact decision-minute raw reports, then received every subsequent indexed capture while a position was open. It started with an empty in-memory ledger and never appended these post-hoc trades to forward performance. The experiment covers the seven published sessions from **11–19 Nov**, the period after the existing engine's date gate. The replay index yielded 2,557 distinct capture filenames and 14 decision-minute reports. Four processed reports were session-gate blocked. This validates only the published subset. `scripts/freeze_policy_walk.py:17`, `:51`; raw source file names can be matched by timestamp in `docs/replay/manifest.json:1`.

Only **one NIFTY S2 trade** completed at each of 0/1/2/5 extra ticks: 19 Nov at 09:25:44 IST → 14:10:47 IST, profit target. The simulation crossed L1, applied the existing 50%-of-max-loss stop and 30%-of-debit S2 target, and re-evaluated the entire event path separately at each slippage level. Net ₹6,191.34 / ₹6,178.35 / ₹6,165.36 / ₹6,126.39. No other strategy produced a trade in this walk; S3 and S6 are intentionally inactive. With one outcome and no losses, profit factor, sample Sharpe and Sortino cannot be estimated. It is particularly unsafe to interpret the single positive ROC as expected return. [Per-run fills](freeze-policy-closed.csv) · [Risk metrics](freeze-policy-stress.csv).

**Statistical decision.** The dates, rules and conduct of this review were visible to the researcher. Neither the later block nor the seven-session walk is an independent forward sample. Market exposures cluster by underlying, date and expiry; overlapping structures do not supply independent evidence. The sample cannot establish a positive risk-adjusted edge or a statistically significant ranking for any of the six. Freeze the tested assumptions, record all missed opportunities as well as fills, and reserve all future post-adoption sessions for evaluation.

### Reproduction

From the repo root with its Python dependencies installed, pass the private archive directory to both commands. No broker credentials are required. The scratch output can be compared with the published CSV files. Optional `--restate-html` exists only for the original lot correction.

```text
python scripts/freeze_expectancy.py --archive "<private archive>" --output scratch/freeze-recheck
python scripts/freeze_policy_walk.py --archive "<private archive>" --output scratch/freeze-recheck
```
