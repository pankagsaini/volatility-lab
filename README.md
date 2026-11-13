# Volatility Lab

[![CI](https://github.com/pankagsaini/volatility-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/pankagsaini/volatility-lab/actions/workflows/ci.yml)

NIFTY and BANKNIFTY options research, broker-data monitoring and historical/paper strategy evaluation.

The public website is **[volatilitylab.in](https://volatilitylab.in)**: a static GitHub Pages application backed by read-only Apps Script snapshots. The optional Streamlit desk uses the Python research package; it is not the website's server.

## Local development

Python **3.11–3.13** is tested. Python 3.11 is the default development version.

```sh
python -m venv .venv
# Activate .venv using your shell's activation command.
python -m pip install uv==0.13.8
uv sync --locked --extra dev
python -m volatility_lab
```

The desk binds to `127.0.0.1:8501`. Use `--port 8502` to change the port or explicitly add `--lan` for other devices on your network. For Streamlit Community Cloud, use **`streamlit_app.py`**. Older launchers remain compatibility wrappers around one bootstrap.

Do not commit credentials. See [configuration and deployment](handbook/deployment.md) before connecting a broker.

## Validation

```sh
python -m pytest
ruff check volatility_lab/ scripts/ pyproject.toml
node scripts/test_live_connection.cjs
node scripts/test_frontend.cjs
node scripts/test_pricing_policy.cjs
```

Tests live under `tests/unit`, `tests/integration` and `tests/smoke`. CI builds a wheel, installs it in a clean environment, checks browser calculations and enforces root/`docs` HTML parity.

## Data and pricing

- Black-76 prefers a fresh future with the **exact option expiry**; otherwise synchronized ATM call/put books may supply a synthetic forward.
- Synthetic pricing requires both spreads **strictly below 3% of midpoint**. The alternative is dividend-aware BSM with verified inputs.
- Rates come from RBI's 91-day T-bill auction yield; dividends come from dated NSE index factsheets. Missing inputs receive no invented defaults.
- Model IV remains separate from broker-captured historical IV. Missing observations remain missing.
- A past capture gap does not disable later complete, fresh snapshots.

## Documentation

Start with the **[documentation index](handbook/README.md)** for architecture, deployment, data flow, operations and audit status. `docs/` contains the published website artifact.

The software is for research and paper evaluation. Model results are estimates and do not guarantee trading outcomes.
