# Dashboard fixes — November 2026

Historical IV comes from saved Upstox `option_greeks.iv` values. ATM IV is the arithmetic mean of the call and put at the recorded strike, with both values required. There is no realized-volatility proxy, generated backfill, interpolation over missing dates, or fallback from the current Black-76 model into the historical series.

The embedded daily series has 17 sourced observations per index (20 October–12 November 2026); earlier dates retain price-derived realized volatility but no IV. The daily contract is the nearest unexpired contract at the capture timestamp. Expiry-day broker spikes that occur before expiry remain visible. Each observation identifies its expiry, strike, timestamp and archive filename. Intraday points use a fixed strike within each expiry and their actual capture times. Missing prices and IV remain missing. No VWAP is inferred from sparse captures.

The embedded expiry catalogue is dated 12 November 2026, 15:59 IST. Its IVs use the broker fields in that archived capture. Live pricing analytics may separately display a labelled Black-76 model IV when validated quote inputs are available. Model outputs are never persisted as historical broker observations.

Other fixes include true calendar spacing on term and strike charts, unavailable first-interval forward variance, observed-only statistics, leg-by-leg expiry payoffs, and a clearly labelled one-day Black-76 stress estimate before fees. Missing ledger data is displayed as unavailable, not zero performance. Shorter tabs, larger controls, horizontal table scrolling and a bounded strike table improve readability.

The backtest workspace ships only archive rows that were actually recorded: 72 trades across 6 sessions (2–9 November 2026), 36 NIFTY and 36 BANKNIFTY, six structures per index per session. Nothing in that table is template-replicated, interpolated or re-priced from missing quotes; a session appears only when its option quotes were captured. The archive is research data, not execution evidence, so the workspace has two explicitly labelled display modes. **Research archive** is the default and renders every recorded trade behind a "not execution-verified" notice. **Verified only** keeps the strict `OptionsCharts.auditedTrade()` gate (event-time provenance, `decision_at`/`available_at` ordering and entry/exit capture sources) and stays empty until such evidence exists. The preset selector also offers **CX · Custom Criteria Builder**, which screens the archive with inclusive bounds, AND/OR logic and an optional single-structure filter. Exported CSV rows carry the exit reason and a `research_archive` / `execution_verified` provenance column.

The builder offers only fields the archive actually stores, and the panel states which structures record each one: VRP (S1, S4), HV30 (S1), HV10 (S2), IV − HV10 (S2, S5), RR25 (S4), BF25 (S6) and days to expiry (all structures). A row that does not record a criterion fails that criterion. No value is imputed, defaulted to zero or estimated from another field, so screening HV30 without S1, for example, returns no matches instead of inventing a volatility reading.

The builder deliberately excludes model-forecast levels (EWMA or EGARCH). The archive stores no point-in-time forecast per session, and the published term-structure forecasts are trained through 13 November 2026 — later than every archived trade — so screening with them would be look-ahead. A model criterion belongs in the builder only once the archive carries a per-session forecast trained through the day before each decision, together with the derived `IV − GARCH` gap the S1/S2 rules actually use.

Validation:

- `node scripts/test_frontend.cjs`
- `node scripts/test_live_connection.cjs`
- `python scripts/verify_dashboard_iv.py PATH_TO_PRIVATE_ARCHIVE_DIRECTORY` verifies 94 embedded observations and all embedded option-chain IVs against the original files. It does not modify the archives.
- Headless Chrome checks both indices, all workspaces, expiry selectors and scenario controls at desktop and mobile widths. The local test intercepts the cloud request to exercise the disconnected state without changing any cloud data.
