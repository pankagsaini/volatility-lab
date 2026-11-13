# Architecture

The three execution surfaces are independent: public static website, optional Streamlit application and Python research/collection package. Apps Script owns cloud collection and the paper ledger. A browser never submits orders or reconstructs that ledger.

| Package | Owns |
| --- | --- |
| core | Calendar, quote/candle validation and history conventions |
| pricing | Black-76/BSM mathematics, model selection, official inputs and surfaces |
| forecasting | Volatility forecasts, term structure and regimes |
| backtest | Strategy simulation, fees, point-in-time signals and paper ledger calculations |
| ingestion | Broker/cloud adapters, downloads, pacing and archive synchronization |
| storage | Atomic persistence, archive coverage and input provenance |
| services | Application-facing market/expiry/futures data and refresh coordination |
| research | Experiments, model validation and relative-value research |
| ui | Presentation, view caches and optional local HTTP adapter |
| runtime | Startup, settings and operational diagnostics |

Root modules are **compatibility aliases only**. Add implementation to its owning package. `__init__`, `_compat`, `__main__` and the old launcher contain only package/bootstrap infrastructure. An AST-based test enforces this boundary.

Old imports (`import option_math`, `volatility_lab.option_math`) and canonical imports (`volatility_lab.pricing.option_math`) resolve to the same module object, preserving caches, monkeypatches and public APIs. New code should use canonical imports. Subpackages load lazily.

The only Streamlit bootstrap is `runtime/bootstrap.py`; it compiles the UI dashboard once per file revision and re-executes it on reruns. All launch routes delegate to it. The packaged launcher works outside a checkout.

Tests are separated into unit, integration and smoke directories. Node regressions execute shipped browser/Apps Script functions against fixtures. Tests must not require production credentials, place trades or overwrite live archives. Calculation changes need numerical/behavioral assertions.
