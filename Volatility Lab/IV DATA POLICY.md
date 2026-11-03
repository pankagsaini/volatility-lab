# Options IV data policy

- IV history accepts only Upstox option-chain IV saved from a Google Drive / Google Cloud capture.
- The accepted history starts at the first retained Cloud capture on 19 October 2026. Earlier IV values were removed from the local history and from pre-cutoff quote snapshots.
- Missing Cloud captures stay blank. The lab does not derive IV from option premiums, OHLC candles, or a pricing model.
- Forecasts remain separate research outputs. They do not fill or replace observed IV.
- A manually refreshed broker quote may appear in the current snapshot, but it is not added to historical IV unless the Cloud collector captures it.
