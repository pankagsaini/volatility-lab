# Dashboard fixes — November 2026

Historical IV comes from saved Upstox `option_greeks.iv` values. ATM IV is the arithmetic mean of the call and put at the recorded strike, with both values required. There is no realized-volatility proxy, generated backfill, interpolation over missing dates, or fallback from the current Black-76 model into the historical series.

The embedded daily series has 17 sourced observations per index (20 October–12 November 2026); earlier dates retain price-derived realized volatility but no IV. The daily contract is the nearest unexpired contract at the capture timestamp. Expiry-day broker spikes that occur before expiry remain visible. Each observation identifies its expiry, strike, timestamp and archive filename. Intraday points use a fixed strike within each expiry and their actual capture times. Missing prices and IV remain missing. No VWAP is inferred from sparse captures.

The embedded expiry catalogue is dated 12 November 2026, 15:59 IST. Its IVs use the broker fields in that archived capture. Live pricing analytics may separately display a labelled Black-76 model IV when validated quote inputs are available. Model outputs are never persisted as historical broker observations.

Other fixes include true calendar spacing on term and strike charts, unavailable first-interval forward variance, observed-only statistics, leg-by-leg expiry payoffs, and a clearly labelled one-day Black-76 stress estimate before fees. Missing ledger data is displayed as unavailable, not zero performance. Shorter tabs, larger controls, horizontal table scrolling and a bounded strike table improve readability.

The backtest workspace ships only archive rows that were actually recorded: 72 trades across 6 sessions (2–9 November 2026), 36 NIFTY and 36 BANKNIFTY, six structures per index per session. Nothing in that table is template-replicated, interpolated or re-priced from missing quotes; a session appears only when its option quotes were captured. The archive is research data, not execution evidence, so the workspace has two explicitly labelled display modes. **Research archive** is the default and renders every recorded trade behind a "not execution-verified" notice. **Verified only** keeps the strict `OptionsCharts.auditedTrade()` gate (event-time provenance, `decision_at`/`available_at` ordering and entry/exit capture sources) and stays empty until such evidence exists. The preset selector also offers **CX · Custom Criteria Builder**, which screens the archive with inclusive bounds, AND/OR logic and an optional single-structure filter. Exported CSV rows carry the exit reason and a `research_archive` / `execution_verified` provenance column.

The builder screens only metrics that are recorded or computable from recorded data: VRP, HV30, HV10, IV − HV10 and days to expiry for every row, plus RR25 where S4 recorded it and BF25 where S6 recorded it. HV10/HV30 are recomputed from the archived daily closes through the prior session (sample standard deviation of log returns, 245-session annualisation), so the entry screen never uses the session's own close. VRP and IV − HV10 follow from the recorded entry IV. A row without a metric fails the criterion instead of defaulting to zero, and the panel states this. 38 of the 72 recorded trades do not satisfy their current structure rule on the restated inputs; they stay listed as recorded and are flagged in the ledger, and the archive panel shows the count.

The builder deliberately excludes model-forecast levels (EWMA or EGARCH). The archive stores no point-in-time forecast per session, and the published term-structure forecasts are trained through 13 November 2026 — later than every archived trade — so screening with them would be look-ahead. A model criterion belongs in the builder only once the archive carries a per-session forecast trained through the day before each decision, together with the derived `IV − GARCH` gap the S1/S2 rules actually use.

Validation:

- `node scripts/test_frontend.cjs`
- `node scripts/test_live_connection.cjs`
- `python scripts/verify_dashboard_iv.py PATH_TO_PRIVATE_ARCHIVE_DIRECTORY` verifies 94 embedded observations and all embedded option-chain IVs against the original files. It does not modify the archives.
- Headless Chrome checks both indices, all workspaces, expiry selectors and scenario controls at desktop and mobile widths. The local test intercepts the cloud request to exercise the disconnected state without changing any cloud data.

## Recent-change review — 19 November 2026

Reviewed changes through `f0a3464` (PRs 24–37): pure-capture collector, EOD recovery, recorded archive replay, HV inputs, lot sizes, expiry clock and the new 0DTE desk. Existing archive, collector, pricing-policy and daily-forecast tests were retained. The deployed forecast artifact was independently checked: both indices were trained through 18 November. Earlier counts and dates above describe earlier releases, not the current archive.

Confirmed defects repaired:

- Cached `dual_straddle` prices were being copied to the current capture timestamp. The browser now retains the source mark timestamp and rejects future/other-session marks. No new history is inferred.
- Rolling and fixed-anchor percentage curves shared a denominator. Each now starts at its own first observed premium. A late-start benchmark uses the ratio of remaining premiums, with no backward extrapolation.
- The published OIData curve was extrapolated to an invented -100% close and applied to both indices/non-expiry days. It now stops at the last precisely timed published point (15:00), applies only to ordinary NIFTY Tuesday expiry sessions, and links the original 13-expiry study. The square-root curve is separately labelled ideal ATM time value.
- The cash straddle breakeven band was centred on spot rather than the recorded option strike. It now uses the strike of the displayed straddle.
- GEX multiplied Upstox OI quantity by lot size a second time and averaged call/put gammas before weighting. It now uses each leg's gamma and quantity once. Missing/negative OI and invalid spot cannot produce an exposure. Display rounding occurs after aggregation.
- Aggregate call-minus-put OI was described as known dealer positioning, and a cumulative strike crossing as a gamma flip. The UI now labels the sign convention and cumulative strike balance explicitly; neither is a dealer-inventory estimate or pinning signal.

Sources: [Upstox option-chain schema](https://upstox.com/developer/api-documentation/get-pc-option-chain/) and [original OIData study](https://www.oidata.in/blog/theta-decay-how-option-premium-melts-toward-expiry). Broker OI is ingested as `oi / 1000`; it is not converted into lot counts before GEX.

Roadmap boundaries: the supplied 0DTE research PDF is design context, not a replacement for source data. The previously removed wings table stays removed. Sparse spot prints remain a labelled running mean, not an official settlement VWAP. Historical IV remains broker-sourced; EGARCH is a separate dated forecast and EWMA stays removed.

Regression coverage exercises unequal leg gammas, missing OI/spot, unequal straddle bases, late join, source timestamps, expiry/session isolation, missing benchmark endpoints, index scope and strike-centred breakevens. Desktop/mobile browser checks cover all tabs and archive screening.

Cloud verification found additional deployment drift: Apps Script v16 had lost the `\d` escapes in all three pending-EOD property-key patterns. The unmodified downloaded v16 source failed the existing backlog recovery test. Restoring just these patterns in the downloaded source makes both collector audit and live-connection suites pass; the source in GitHub was already correct. Both suites now accept `COLLECTOR_SOURCE_PATH` so deployed code can be tested directly before release. No broker history or Drive archive was rewritten by this repair.
