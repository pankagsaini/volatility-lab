"""Classic & Modern Institutional UI Theme Engine for Volatility Lab.

Provides:
1. Cohesive Obsidian Slate & Sovereign Gold / Emerald / Cyan CSS design system
   with classic editorial masthead typography (Instrument Serif / Plus Jakarta Sans)
   and strict tabular numerals (JetBrains Mono) for all quantitative metrics.
2. Automatic transparent wrapper around `st.plotly_chart` (`install_classic_modern_theme`)
   that upgrades every Plotly figure across `monitor_ui.py`, `restored_lab.py`,
   `vol_surface_3d.py`, `futures_ui.py`, `backtest_ui.py`, and `research_ui.py`
   without modifying any underlying data traces or quantitative calculations.
3. Cold-start fast-render guards so Streamlit Community Cloud flushes the UI shell
   immediately without hanging on skeleton placeholders.
"""
from __future__ import annotations

from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo
import streamlit as st

IST = ZoneInfo("Asia/Kolkata")

# Classic & Modern Institutional Palette for Plotly Traces
TRACE_PALETTE = [
    "#38BDF8",  # Crisp Institutional Cyan (Primary IV / ATM series)
    "#D4AF37",  # Classic Sovereign Gold (GARCH / Benchmark / Forward IV)
    "#10B981",  # Emerald (EWMA / Positive Carry / Bullish Edge)
    "#A78BFA",  # Royal Violet (Secondary Surface / Put Wing)
    "#F43F5E",  # Rose Crimson (Drawdown / Put Skew / Risk Alert)
    "#F59E0B",  # Amber (Warning / Pre-Open / Neutral Band)
]

CLASSIC_MODERN_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=JetBrains+Mono:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');

:root {
    --vl-bg: #090d16;
    --vl-bg-elevated: #0d1320;
    --vl-surface: #111827;
    --vl-surface-hover: #162033;
    --vl-surface-glass: rgba(17, 24, 39, 0.78);
    --vl-border: #1e293b;
    --vl-border-strong: #334155;
    --vl-gold: #d4af37;
    --vl-gold-soft: rgba(212, 175, 55, 0.14);
    --vl-cyan: #38bdf8;
    --vl-cyan-soft: rgba(56, 189, 248, 0.12);
    --vl-emerald: #10b981;
    --vl-emerald-soft: rgba(16, 185, 129, 0.14);
    --vl-rose: #f43f5e;
    --vl-text: #f1f5f9;
    --vl-text-secondary: #94a3b8;
    --vl-text-muted: #64748b;
    --vl-font-sans: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --vl-font-serif: 'Instrument Serif', Georgia, 'Times New Roman', serif;
    --vl-font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
    /* Backwards-compatible aliases used by backtest_ui.py */
    --bg: #090d16;
    --panel: #111827;
    --panel-soft: #162033;
    --border: #1e293b;
    --text: #f1f5f9;
    --muted: #94a3b8;
    --primary: #10b981;
}

/* Global App Canvas with subtle atmospheric radial wash */
.stApp {
    background-color: var(--vl-bg) !important;
    background-image:
        radial-gradient(1100px 420px at 12% -6%, rgba(56, 189, 248, 0.07), transparent 70%),
        radial-gradient(900px 380px at 88% -4%, rgba(212, 175, 55, 0.05), transparent 70%) !important;
    color: var(--vl-text) !important;
    font-family: var(--vl-font-sans) !important;
}

/* Transparent frosted top header */
[data-testid="stHeader"] {
    background: rgba(9, 13, 22, 0.82) !important;
    backdrop-filter: blur(12px) !important;
    border-bottom: 1px solid rgba(30, 41, 59, 0.65) !important;
}

/* Balanced main container spacing */
.block-container {
    max-width: 1520px !important;
    padding-top: 3.75rem !important;
    padding-bottom: 2.5rem !important;
    padding-left: 1.75rem !important;
    padding-right: 1.75rem !important;
}

/* Classic + Modern Typography Hierarchy */
h1 {
    font-family: var(--vl-font-sans) !important;
    font-size: 1.95rem !important;
    font-weight: 700 !important;
    letter-spacing: -0.03em !important;
    color: #f8fafc !important;
    margin-top: 0 !important;
    margin-bottom: 0.35rem !important;
}

h2 {
    font-family: var(--vl-font-sans) !important;
    font-size: 1.32rem !important;
    font-weight: 600 !important;
    letter-spacing: -0.02em !important;
    color: #f1f5f9 !important;
    border-bottom: 1px solid rgba(30, 41, 59, 0.85);
    padding-bottom: 0.45rem;
    margin-top: 1.1rem !important;
}

h3 {
    font-family: var(--vl-font-sans) !important;
    font-size: 1.06rem !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em !important;
    color: #e2e8f0 !important;
}

/* Tabular numerals for all quantitative readouts */
div[data-testid="stMetricValue"],
div[data-testid="stMetricDelta"],
[data-testid="stDataFrame"],
[data-testid="stTable"],
code, pre {
    font-family: var(--vl-font-mono) !important;
    font-variant-numeric: tabular-nums !important;
}

/* Elevated Institutional KPI Metric Cards */
div[data-testid="stMetric"] {
    background: linear-gradient(180deg, rgba(22, 32, 51, 0.72) 0%, rgba(17, 24, 39, 0.92) 100%) !important;
    border: 1px solid var(--vl-border) !important;
    border-top: 2px solid rgba(56, 189, 248, 0.55) !important;
    border-radius: 12px !important;
    padding: 14px 18px !important;
    box-shadow: 0 4px 18px -4px rgba(0, 0, 0, 0.42), inset 0 1px 0 rgba(255, 255, 255, 0.04) !important;
    transition: transform 0.15s ease, border-color 0.15s ease !important;
}

div[data-testid="stMetric"]:hover {
    border-color: var(--vl-border-strong) !important;
    border-top-color: var(--vl-gold) !important;
}

div[data-testid="stMetricLabel"] {
    font-family: var(--vl-font-sans) !important;
    font-size: 0.74rem !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.065em !important;
    color: var(--vl-text-secondary) !important;
}

div[data-testid="stMetricValue"] {
    font-size: 1.42rem !important;
    font-weight: 600 !important;
    color: #f8fafc !important;
    letter-spacing: -0.02em !important;
}

div[data-testid="stCaptionContainer"] {
    color: var(--vl-text-secondary) !important;
    font-size: 0.82rem !important;
}

/* Segmented Control (Primary Workspace Switcher) */
[data-testid="stSegmentedControl"] {
    background: #0d1320 !important;
    padding: 4px !important;
    border-radius: 11px !important;
    border: 1px solid var(--vl-border) !important;
}

[data-testid="stSegmentedControl"] button {
    border-radius: 8px !important;
    font-size: 0.84rem !important;
    font-weight: 500 !important;
    padding: 6px 16px !important;
    transition: all 0.15s ease !important;
}

[data-testid="stSegmentedControl"] button[aria-checked="true"] {
    background: linear-gradient(135deg, rgba(16, 185, 129, 0.22) 0%, rgba(56, 189, 248, 0.18) 100%) !important;
    border: 1px solid rgba(56, 189, 248, 0.55) !important;
    color: #f8fafc !important;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.35) !important;
}

[data-testid="stSegmentedControl"] button[aria-checked="true"] p {
    color: #f8fafc !important;
    font-weight: 600 !important;
}

/* Radio Pill Selectors (Symbol / Horizon / Sub-view switches) */
div[data-testid="stRadio"] {
    padding: 4px 0 10px !important;
}

div[data-testid="stRadio"] [role="radiogroup"] {
    gap: 0.5rem !important;
}

div[data-testid="stRadio"] label {
    border: 1px solid var(--vl-border) !important;
    border-radius: 8px !important;
    padding: 6px 14px !important;
    background: var(--vl-surface) !important;
    font-size: 0.85rem !important;
    font-weight: 500 !important;
    color: var(--vl-text-secondary) !important;
    transition: all 0.15s ease !important;
}

div[data-testid="stRadio"] label:hover {
    border-color: var(--vl-border-strong) !important;
    background: var(--vl-surface-hover) !important;
    color: var(--vl-text) !important;
}

div[data-testid="stRadio"] label:has(input:checked) {
    border-color: rgba(212, 175, 55, 0.65) !important;
    background: linear-gradient(135deg, rgba(212, 175, 55, 0.16) 0%, rgba(16, 185, 129, 0.14) 100%) !important;
    color: #fef08a !important;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3) !important;
}

/* Tabs Styling */
button[data-baseweb="tab"] {
    font-family: var(--vl-font-sans) !important;
    font-size: 0.88rem !important;
    font-weight: 500 !important;
    color: var(--vl-text-secondary) !important;
    padding: 8px 16px !important;
}

button[data-baseweb="tab"][aria-selected="true"] {
    color: #38bdf8 !important;
    font-weight: 600 !important;
}

/* Plotly Chart Containers */
[data-testid="stPlotlyChart"] {
    background: linear-gradient(180deg, rgba(17, 24, 39, 0.86) 0%, rgba(11, 16, 28, 0.94) 100%) !important;
    border: 1px solid var(--vl-border) !important;
    border-radius: 14px !important;
    padding: 10px 12px !important;
    box-shadow: 0 6px 22px -6px rgba(0, 0, 0, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.03) !important;
}

/* DataFrames & Quantitative Tables */
[data-testid="stDataFrame"],
[data-testid="stTable"] {
    border: 1px solid var(--vl-border) !important;
    border-radius: 12px !important;
    overflow: hidden !important;
    box-shadow: 0 4px 16px -4px rgba(0, 0, 0, 0.38) !important;
}

/* Expanders & Popovers */
[data-testid="stExpander"] {
    background: rgba(17, 24, 39, 0.65) !important;
    border: 1px solid var(--vl-border) !important;
    border-radius: 11px !important;
}

[data-testid="stPopoverButton"] {
    border-radius: 9px !important;
    background: var(--vl-surface) !important;
    border: 1px solid var(--vl-border) !important;
}

/* Sidebar Refinement */
section[data-testid="stSidebar"] {
    background: #0b101b !important;
    border-right: 1px solid var(--vl-border) !important;
}

/* Primary & Secondary Action Buttons */
button[kind="primary"] {
    border-radius: 8px !important;
    background: linear-gradient(135deg, #059669 0%, #10b981 100%) !important;
    border: 1px solid rgba(52, 211, 153, 0.4) !important;
    color: #ffffff !important;
    font-weight: 600 !important;
    letter-spacing: 0.01em !important;
    box-shadow: 0 2px 10px rgba(16, 185, 129, 0.25) !important;
}

button[kind="secondary"] {
    border-radius: 8px !important;
    background: var(--vl-surface) !important;
    border: 1px solid var(--vl-border) !important;
    color: var(--vl-text) !important;
}

/* Alerts & Callout Boxes */
div[data-testid="stAlert"] {
    border-radius: 10px !important;
    border: 1px solid var(--vl-border-strong) !important;
}

/* Executive Masthead Component Classes */
.vl-masthead {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
    padding: 12px 18px;
    margin-top: 4px;
    margin-bottom: 12px;
    background: linear-gradient(90deg, rgba(17, 24, 39, 0.95) 0%, rgba(15, 23, 42, 0.85) 100%);
    border: 1px solid rgba(51, 65, 85, 0.65);
    border-left: 3px solid #d4af37;
    border-radius: 12px;
    box-shadow: 0 6px 20px -6px rgba(0, 0, 0, 0.45);
}
.vl-masthead-left {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    min-width: 0;
}
.vl-brand-crest {
    font-family: var(--vl-font-sans);
    font-size: 1.55rem;
    font-weight: 800;
    color: #f8fafc;
    letter-spacing: -0.025em;
    line-height: 1.15;
    white-space: nowrap;
}
.vl-Quick-actions {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: rgba(9, 13, 22, 0.82);
    border: 1px solid rgba(51, 65, 85, 0.65);
    border-radius: 999px;
    padding: 3px 6px;
}
.vl-icon-action {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 9px;
    border-radius: 999px;
    font-family: var(--vl-font-sans);
    font-size: 0.72rem;
    font-weight: 600;
    color: #cbd5e1 !important;
    text-decoration: none !important;
    transition: background 0.15s ease, color 0.15s ease;
    white-space: nowrap;
}
.vl-icon-action:hover {
    background: rgba(56, 189, 248, 0.15);
    color: #f8fafc !important;
}
details.vl-app-qr-dropdown {
    position: relative;
    display: inline-block;
}
details.vl-app-qr-dropdown summary {
    list-style: none;
    cursor: pointer;
}
details.vl-app-qr-dropdown summary::-webkit-details-marker {
    display: none;
}
.vl-app-qr-card {
    position: absolute;
    right: 0;
    top: calc(100% + 8px);
    z-index: 9999;
    width: 270px;
    background: #0f172a;
    border: 1px solid #334155;
    border-top: 2px solid #d4af37;
    border-radius: 12px;
    padding: 12px;
    box-shadow: 0 14px 32px rgba(0, 0, 0, 0.65);
}
.vl-brand-gold {
    color: #d4af37;
    margin-right: 6px;
}
.vl-brand-sub {
    font-family: var(--vl-font-mono);
    font-size: 0.73rem;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    color: #94a3b8;
    padding: 3px 9px;
    border-radius: 6px;
    background: rgba(30, 41, 59, 0.65);
    border: 1px solid rgba(71, 85, 105, 0.45);
}
.vl-pill-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
}
.vl-meta-pill {
    font-family: var(--vl-font-mono);
    font-size: 0.74rem;
    color: #cbd5e1;
    background: rgba(15, 23, 42, 0.85);
    border: 1px solid rgba(51, 65, 85, 0.7);
    border-radius: 9999px;
    padding: 4px 12px;
}

/* Universal Any-Device Responsive Breakpoints (Mobile Phones, iPads/Tablets, Ultrawide) */
.vl-radar-subgrid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    margin-bottom: 10px;
}

@media (max-width: 768px) {
    .block-container {
        padding-top: 3.1rem !important;
        padding-bottom: 1.5rem !important;
        padding-left: 0.75rem !important;
        padding-right: 0.75rem !important;
    }
    .vl-masthead {
        padding: 11px 14px !important;
        gap: 8px !important;
    }
    .vl-brand-crest {
        font-size: 1.38rem !important;
    }
    .vl-brand-sub {
        font-size: 0.65rem !important;
        padding: 2px 7px !important;
    }
    .vl-radar-subgrid {
        grid-template-columns: 1fr !important;
    }
    [data-testid="stSegmentedControl"] {
        display: flex !important;
        overflow-x: auto !important;
        max-width: 100% !important;
        -webkit-overflow-scrolling: touch !important;
    }
    [data-testid="stSegmentedControl"] button {
        padding: 7px 12px !important;
        font-size: 0.78rem !important;
        white-space: nowrap !important;
        min-height: 38px !important;
    }
    div[data-testid="stMetric"] {
        padding: 10px 13px !important;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.18rem !important;
    }
    [data-testid="stPlotlyChart"] {
        padding: 6px 6px !important;
    }
}
</style>

"""


def style_plotly_figure(fig):
    """Apply Classic & Modern Institutional styling to a Plotly Figure in place.

    Preserves 100% of existing traces, values, axes bindings, and hover templates
    while upgrading typography, gridlines, hover cards, margins, and 3D lighting.
    """
    if fig is None or not hasattr(fig, "update_layout"):
        return fig
    try:
        layout = fig.layout
        cur_margin = getattr(layout, "margin", None)
        m_l = max(getattr(cur_margin, "l", 0) or 0, 46)
        m_r = max(getattr(cur_margin, "r", 0) or 0, 22)
        m_t = max(getattr(cur_margin, "t", 0) or 0, 38)
        m_b = max(getattr(cur_margin, "b", 0) or 0, 36)

        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(13, 19, 32, 0.55)",
            font=dict(
                family="JetBrains Mono, Plus Jakarta Sans, sans-serif",
                color="#cbd5e1",
                size=11,
            ),
            margin=dict(l=m_l, r=m_r, t=m_t, b=m_b),
            hoverlabel=dict(
                bgcolor="#0f172a",
                bordercolor="#334155",
                font=dict(
                    family="JetBrains Mono, monospace",
                    size=12,
                    color="#f8fafc",
                ),
            ),
            legend=dict(
                bgcolor="rgba(15, 23, 42, 0.65)",
                bordercolor="rgba(51, 65, 85, 0.45)",
                borderwidth=1,
                font=dict(size=11, color="#e2e8f0"),
            ),
        )

        # Style 2D X and Y axes with subtle institutional dotted grids and crosshairs
        fig.update_xaxes(
            gridcolor="rgba(148, 163, 184, 0.09)",
            griddash="dot",
            zerolinecolor="rgba(148, 163, 184, 0.18)",
            linecolor="rgba(71, 85, 105, 0.5)",
            tickfont=dict(family="JetBrains Mono, monospace", size=11, color="#94a3b8"),
            title_font=dict(family="Plus Jakarta Sans, sans-serif", size=12, color="#cbd5e1"),
            showspikes=True,
            spikemode="across",
            spikethickness=1,
            spikedash="dot",
            spikecolor="#64748b",
        )
        fig.update_yaxes(
            gridcolor="rgba(148, 163, 184, 0.09)",
            griddash="dot",
            zerolinecolor="rgba(148, 163, 184, 0.18)",
            linecolor="rgba(71, 85, 105, 0.5)",
            tickfont=dict(family="JetBrains Mono, monospace", size=11, color="#94a3b8"),
            title_font=dict(family="Plus Jakarta Sans, sans-serif", size=12, color="#cbd5e1"),
        )

        # Refine traces without changing any data or series semantics
        for idx, trace in enumerate(getattr(fig, "data", ())):
            t_type = getattr(trace, "type", "")
            if t_type in ("scatter", "scattergl"):
                # Prevent text label clipping at top/bottom of chart box
                if hasattr(trace, "cliponaxis") and trace.cliponaxis is None:
                    trace.cliponaxis = False
                if hasattr(trace, "textfont"):
                    trace.textfont = dict(
                        family="JetBrains Mono, monospace",
                        size=10,
                        color="#e2e8f0",
                    )
            elif t_type == "surface":
                trace.lighting = dict(
                    ambient=0.68,
                    diffuse=0.82,
                    specular=0.28,
                    roughness=0.45,
                    fresnel=0.15,
                )
    except Exception:
        pass
    return fig


def install_classic_modern_theme() -> None:
    """Inject the Classic & Modern CSS design system and wrap st.plotly_chart idempotently."""
    st.markdown(CLASSIC_MODERN_CSS, unsafe_allow_html=True)
    if not getattr(st, "_vl_classic_modern_wrapped", False):
        orig_plotly_chart = st.plotly_chart

        @wraps(orig_plotly_chart)
        def _wrapped_plotly_chart(figure_or_data, *args, **kwargs):
            style_plotly_figure(figure_or_data)
            return orig_plotly_chart(figure_or_data, *args, **kwargs)

        st.plotly_chart = _wrapped_plotly_chart
        st._vl_classic_modern_wrapped = True


def market_session_badge_html(now: datetime | None = None) -> str:
    """Return a Classic & Modern dark-slate pill badge for current NSE market status."""
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)
    minute = current.hour * 60 + current.minute
    if current.weekday() < 5 and 555 <= minute <= 930:
        label = "● NSE LIVE · REGULAR SESSION"
        style = (
            "background:rgba(16,185,129,0.14); color:#34d399; "
            "border:1px solid rgba(16,185,129,0.45); box-shadow:0 0 12px rgba(16,185,129,0.18);"
        )
    elif current.weekday() < 5 and 540 <= minute < 555:
        label = "◐ NSE · PRE-OPEN SESSION"
        style = (
            "background:rgba(245,158,11,0.14); color:#fbbf24; "
            "border:1px solid rgba(245,158,11,0.45);"
        )
    else:
        label = "○ NSE CLOSED · ARCHIVE / EOD"
        style = (
            "background:rgba(148,163,184,0.10); color:#cbd5e1; "
            "border:1px solid rgba(148,163,184,0.28);"
        )
    return (
        f"<span style='display:inline-block; padding:4px 13px; border-radius:9999px; "
        f"font-family:var(--vl-font-mono); font-weight:600; font-size:0.74rem; "
        f"letter-spacing:0.04em; {style}'>{label}</span>"
    )


def get_lan_ip() -> str:
    """Detect local Wi-Fi / LAN IPv4 address for mobile/tablet access on the same network."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.2)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def render_any_device_access_popover() -> None:
    """Render a compact 📱 Any-Device Access popover with clickable Cloud URL, LAN URL, and QR code."""
    import urllib.parse
    cloud_url = "https://pankagsaini-volatility-lab-streamlit-app-nqnqoa.streamlit.app/"
    lan_ip = get_lan_ip()
    lan_url = f"http://{lan_ip}:8501"
    qr_api = f"https://api.qrserver.com/v1/create-qr-code/?size=180x180&margin=6&data={urllib.parse.quote(cloud_url)}"
    with st.popover("📱 Open on Any Device"):
        st.markdown("**1. Global Cloud URL (Any Phone, Tablet, or Laptop — 24/7)**")
        st.caption("Synced every 1 minute from your Google Cloud / Apps Script Upstox archive:")
        st.code(cloud_url, language=None)
        st.markdown(
            f'<div style="display:flex; align-items:center; gap:14px; background:rgba(15,23,42,0.85); border:1px solid #1e293b; border-radius:10px; padding:10px 12px; margin-bottom:10px;">'
            f'<img src="{qr_api}" width="96" height="96" style="border-radius:6px; border:1px solid #334155;" alt="Scan QR Code" />'
            f'<div style="font-size:0.78rem; color:#cbd5e1; line-height:1.45;">'
            f'<b style="color:#d4af37;">Scan with Phone Camera</b><br/>'
            f'Opens your live Volatility Lab directly on iOS / Android Chrome or Safari (<a href="{cloud_url}" target="_blank" style="color:#38bdf8;">open link</a>).'
            f'</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown("**2. Local Wi-Fi / LAN URL (When Running on Your PC)**")
        st.caption("Server binds to `0.0.0.0:8501` — open this on any phone/tablet on your Wi-Fi:")
        st.code(lan_url, language=None)
