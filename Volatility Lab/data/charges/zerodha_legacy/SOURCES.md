# Zerodha NSE backtest charges

Verified 21 October 2026. Standard resident retail orders, each leg treated as one order per fill.

- Current published charges: https://zerodha.com/charges/
- STT and transaction charges effective 1 April 2023: https://zerodha.com/marketintel/bulletin/347967/revision-in-transaction-charges-and-stt-from-1st-april-2023
- Transaction charges effective 1 April 2024: https://zerodha.com/marketintel/bulletin/373933/revision-in-nse-transaction-charges-from-april-1-2024
- STT and charges effective 1 October 2024: https://zerodha.com/z-connect/business-updates/revision-in-exchange-transaction-charges-and-securities-transaction-tax-from-october-1-2024
- Exchange/IPFT split effective 1 March 2026: https://nsearchives.nseindia.com/content/circulars/FA73061.pdf
- STT effective 1 April 2026: https://zerodha.com/marketintel/bulletin/445377/revision-in-stt-securities-transaction-tax-from-1st-april-2026

The pre-April-2023 transaction rate is the old-rate baseline in the 2023 bulletin, not a verified full reconstruction of every earlier revision. Historical totals are estimates, not contract-note reconciliation. GST includes exchange/IPFT; the current rounded displayed exchange rate is used as a combined estimate to avoid double counting IPFT. Exercise/assignment and expiry settlement are excluded. Financing, collateral/margin costs, special account tariffs, auto square-off fees and order splitting are not included. Historical lot-size compliance remains unverified.

Daily Windows task: Volatility Lab Charge Rates, 08:00 local time, StartWhenAvailable, signed-in user. Backtest page also checks daily (one-hour UI cache). Laptop must be on for checks. Failed checks preserve saved rates and show a warning. Published changes are snapshotted and applied from their observation date, never retroactively guessed; an effective date not stated by the current charges page still requires verification. Automated HTML parsing cannot guarantee all future page/schema changes; failures retain last-known rates.
