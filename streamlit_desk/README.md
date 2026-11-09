# Volatility Lab — Streamlit Quant Desk Package

This folder encapsulates the **Streamlit Interactive Quant Desk** for Volatility Lab.

While the primary public website is hosted on **[volatilitylab.in](https://volatilitylab.in)** (a zero-dependency, ultra-fast static portal with live 60-second market streaming), this package provides the full interactive Python/Streamlit workstation for:
- ⚡ **Options Quant Desk**: Real-time smile fitting, SVI calibrations, Volatility Risk Premium (VRP) harvesting, and P&L stress testing.
- 🧪 **Strategy Backtest Studio**: Multi-strategy historical simulation across NIFTY & BANKNIFTY with actual Upstox execution slippage and brokerage charge accounting.
- 📈 **3D Volatility Surface**: Interactive Plotly volatility surfaces across strikes and expiries.

---

## Quick Start (Run Locally)

From the repository root, run:

```bash
python streamlit_desk/run.py
```

Or directly via Streamlit:

```bash
streamlit run streamlit_desk/app.py
```

Access URLs:
- **Local PC**: `http://localhost:8501`
- **Wi-Fi LAN (Phone/Tablet)**: `http://<your-lan-ip>:8501`

---

## How to Switch Back to Streamlit Cloud in the Future

If you want to host this on **Streamlit Cloud** again later:
1. Log in to [share.streamlit.io](https://share.streamlit.io).
2. Connect your GitHub repository: `pankagsaini/volatility-lab`.
3. Set the **Main file path** to:
   - `streamlit_app.py` *(the root forwarder)*, or
   - `streamlit_desk/app.py`
4. Set the Python version to `3.11` or higher.
5. In App Settings -> Secrets, add your `UPSTOX_TOKEN` (optional for local/cloud hybrid sync).
6. Click **Deploy**.

---

## Architecture & Integration

- **`streamlit_desk/app.py`**: High-performance entrypoint with memory trim (`MALLOC_TRIM_THRESHOLD_`) and thread bounding.
- **`streamlit_desk/run.py`**: Local network launcher with automatic LAN IP resolution for mobile/tablet cross-device testing.
- **`streamlit_desk/.streamlit/config.toml`**: Dark quant theme and headless server configuration.
- **Core Engine Delegation**: All math, data caches, and models are imported directly from `Volatility Lab/` without code duplication.
