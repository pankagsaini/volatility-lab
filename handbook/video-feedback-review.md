# Video feedback review — 15 November 2026

Reviewed the 8m34s recording and locally transcribed its spoken feedback.

## Presentation changes

- Keep only Refresh beside India VIX; remove Share, Star and Edit.
- Shorten synthetic-futures text; identify the displayed future as expiry-matched.
- Keep strike under ATM IV; remove repeated model/broker labels.
- Keep only HV10/HV30 in their card; retain other measures in the realized-volatility table.
- Remove the redundant above/below message from VRP; retain IV minus HV10.
- Give Trade setups the same styling as other research tabs, with an outlined active state.
- Use unfilled history lines. Remove closing-estimate curves from the display, retaining the underlying records separately from captured IV.
- Shorten chart legends; remove below-chart explanatory text and the date appended to the Yang–Zhang table row. Source dates remain available in forecast tooltips.
- Stack the companion tables at the same width; show only IV at term-structure points since the axis already supplies dates.
- Leave smile and straddle behaviour unchanged, as requested.

## Data checks

The NIFTY 6 November point is 19.01%: the original complete broker capture contains call IV 17.72% and put IV 20.30%. Its expiry is 9 November and strike 25,050. The source file is `2026-11-06_snapshot_153042_complete.json.gz`. Agreement establishes source fidelity, not execution quality. The spike is retained rather than smoothed.

Weekly options have no automatically interchangeable monthly future. The displayed future requires the selected option expiry and a valid captured book. A blank is preserved when those conditions fail, with a hover explanation.

Published forecasts were recomputed from the stored one-year OHLC window ending 11 November and reconciled against the conditional variance path, summed/averaged over sessions through each expiry. NIFTY GARCH is 17.889989% for 17 November (4 sessions), 17.6622469% for 15 December (23 sessions), and 17.4111023% for 25 January (51 sessions). EWMA is 16.7091844% at every horizon under its constant expected future variance recursion. BANKNIFTY GARCH remains unavailable because the fit is invalid; EWMA is 19.2778058%.

These are realized-volatility forecasts, not predicted option IV. Their older training date also means comparison with 13 November intraday IV is reference context, not a synchronized trade signal. Future 2027 holidays are provisional. No recalibration was made merely to move a forecast toward observed IV.

Trade setup blanks result from the executable-quote/input gates. Historical backtest and paper P&L blanks result from missing verified execution provenance in legacy records. Raw records are retained; no prices or performance are fabricated to populate those cells. Fresh collector operation still requires a market-session check.
