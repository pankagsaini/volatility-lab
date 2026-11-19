# Custom backtests

In **Backtest archive**, select **Custom entry & exit rules**, choose one of the six existing structures (or evaluate all six separately), set the session range and IST clocks, then **Run backtest**. Editing any rule invalidates the displayed custom result until it is run again. The preset archive remains a separate fixed-entry/fixed-exit baseline.

Entry conditions include weekday, calendar DTE, captured ATM IV, prior-close HV10/HV30, IV–HV spreads, IV/HV30, captured 25-delta risk reversal/butterfly, spot change from the previous close, absolute structure delta, minimum leg OI and maximum leg bid/ask spread. Enabled numeric conditions can use AND or OR. Missing metrics fail their condition; a spread requires a timestamped fresh book.

Exit conditions include a mandatory clock exit and optional stop loss, profit target, trailing stop, maximum holding time, absolute spot move and ATM IV change. Monetary limits are rupees per strategy lot and use net liquidation P&L after estimated charges and slippage. All six structures retain their existing fixed strike offsets and ratios; there is no custom leg builder.

## Replay conventions

- The first qualifying captured point in the inclusive entry-minute window fixes the legs and strikes. One entry per structure per session; no re-entry or overnight holding.
- The dataset selects the nearest listed expiry with at least two calendar days remaining at the first usable capture. It does not provide expiry-day strategies or a selectable expiry curve.
- Every exit uses the first subsequent complete observed mark that triggers a rule. It does not infer intrabar threshold touches or fill at the stop/target price. A gap can produce overshoot.
- The clock/holding deadline accepts the first complete mark at or after the deadline, up to five minutes late. Otherwise the trade is unresolved and excluded from closed-trade statistics, with a visible count and reason. This exclusion can bias results: inspect coverage before comparing strategies.
- Marks are recorded broker LTP, not synchronized executable fills. Slippage is adverse on both entry and exit, in 0.05-point ticks per leg. Broker/statutory costs use the repository's dated charge model, with brokerage per leg order rather than per lot. Order splitting/freeze limits are not simulated.
- Only captured IV/Greeks supply entry metrics. No historical IV is manufactured. HV uses completed closes strictly before the session. RR25/BF25 interpolate captured delta/IV pairs; they are derived metrics, not additional historical observations.
- Capital is a scenario-margin proxy. Maximum daily allocation conservatively sums all selected structures for that day, including trades that may not overlap. Drawdown follows closed-trade equity, not continuous portfolio marks. Profit factor uses net wins/net losses.

## Publishing captured sessions

From a checkout with access to the private broker snapshot archive:

```sh
python scripts/build_replay_data.py --archive "PATH_TO_CAPTURE_ARCHIVE"
node scripts/test_frontend.cjs
```

The exporter writes compact per-index/day JSON and a SHA-256 manifest to `docs/replay/`, and mirrors them to `replay/` for root previews. Only strikes needed by the six structures across observed ATMs are retained. Browser loading is lazy, limited to the requested index/date/weekday, and refuses partial or hash-mismatched downloads. New days require re-export and publication; the public site has no credentials or direct access to private Drive files.

Review and commit both data directories with the normal website PR. No collector, Apps Script deployment, or broker credentials are changed by a replay publication. Tests cover exit ordering, missing marks, fixed strikes, costs, prior-close inputs, data hashes and browser interactions.
