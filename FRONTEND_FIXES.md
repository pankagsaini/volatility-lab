# Dashboard fixes — November 2026

Historical IV comes from saved Upstox `option_greeks.iv` values. ATM IV is the arithmetic mean of the call and put at the recorded strike, with both values required. There is no realized-volatility proxy, generated backfill, interpolation over missing dates, or fallback from the current Black-76 model into the historical series.

The embedded daily series has 17 sourced observations per index (20 October–12 November 2026); earlier dates retain price-derived realized volatility but no IV. The daily contract is the nearest unexpired contract at the capture timestamp. Expiry-day broker spikes that occur before expiry remain visible. Each observation identifies its expiry, strike, timestamp and archive filename. Intraday points use a fixed strike within each expiry and their actual capture times. Missing prices and IV remain missing. No VWAP is inferred from sparse captures.

The embedded expiry catalogue is dated 12 November 2026, 15:59 IST. Its IVs use the broker fields in that archived capture. Live pricing analytics may separately display a labelled Black-76 model IV when validated quote inputs are available. Model outputs are never persisted as historical broker observations.

Other fixes include true calendar spacing on term and strike charts, unavailable first-interval forward variance, observed-only statistics, leg-by-leg expiry payoffs, and a clearly labelled one-day Black-76 stress estimate before fees. Missing ledger data is displayed as unavailable, not zero performance. Shorter tabs, larger controls, horizontal table scrolling and a bounded strike table improve readability.

Validation:

- `node scripts/test_frontend.cjs`
- `node scripts/test_live_connection.cjs`
- `python scripts/verify_dashboard_iv.py PATH_TO_PRIVATE_ARCHIVE_DIRECTORY` verifies 94 embedded observations and all embedded option-chain IVs against the original files. It does not modify the archives.
- Headless Chrome checks both indices, all workspaces, expiry selectors and scenario controls at desktop and mobile widths. The local test intercepts the cloud request to exercise the disconnected state without changing any cloud data.
