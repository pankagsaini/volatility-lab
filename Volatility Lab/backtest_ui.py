from datetime import date, time, timedelta
import json
import sqlite3
import pandas as pd
import streamlit as st
from backtest_engine import ROOT, DATA_DIR
from collector_support import read_json
from upstox_data import INDICES
from strategy_backtest import TEMPLATES, SCENARIO_PRESETS, scenario_preset, template, validate
from backtest_report import render as report, STORE
import backtest_jobs as jobs


def checked_charges():
    return jobs.fee_status()


def _apply_backtest_theme():
    try:
        import ui_theme
        ui_theme.install_classic_modern_theme()
    except Exception:
        pass


def _default_backtest_date_window(symbol: str = 'NIFTY') -> tuple[date, date, str]:
    """Detect available date range in 1-minute archive (`queue.sqlite`) or Cloud (`monitor.sqlite`)."""
    q_path = ROOT / 'queue.sqlite'
    if q_path.exists():
        try:
            with sqlite3.connect(q_path, timeout=5) as con:
                has_jobs = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
                if has_jobs:
                    rows = con.execute("SELECT meta FROM jobs WHERE state='done' AND kind='minute' AND json_extract(meta, '$.symbol')=? ORDER BY rowid DESC LIMIT 500", (symbol,)).fetchall()
                    starts, ends = [], []
                    for (m_txt,) in rows:
                        m = json.loads(m_txt)
                        if m.get('symbol') == symbol and m.get('start') and m.get('end'):
                            starts.append(str(m['start'])[:10])
                            ends.append(str(m['end'])[:10])
                    if starts and ends:
                        d_max = date.fromisoformat(max(ends))
                        d_min = max(date.fromisoformat(min(starts)), d_max - timedelta(days=30))
                        return d_min, d_max, f"1-minute archive ({min(starts)} to {max(ends)})"
        except Exception:
            pass

    mon_db = DATA_DIR / 'monitor.sqlite'
    if mon_db.exists():
        try:
            with sqlite3.connect(mon_db, timeout=5) as con:
                has_tbl = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='intraday_observations'").fetchone()
                if has_tbl:
                    row = con.execute(
                        "SELECT MIN(substr(as_of,1,10)), MAX(substr(as_of,1,10)), COUNT(*) FROM intraday_observations WHERE symbol=?",
                        (symbol,),
                    ).fetchone()
                    if row and row[0] and row[1] and int(row[2] or 0) > 0:
                        d_min = date.fromisoformat(str(row[0])[:10])
                        d_max = date.fromisoformat(str(row[1])[:10])
                        return d_min, d_max, f"Cloud intraday archive ({row[0]} to {row[1]} · {row[2]} captures)"
        except Exception:
            pass

    return date(2024, 10, 1), date(2024, 10, 31), "Default range"


def render_options():
    _apply_backtest_theme()
    st.subheader('Strategy builder')
    a, b = st.columns([2, 3])
    preset = a.selectbox('Starting template', list(TEMPLATES), index=2)
    name = b.text_input('Strategy name', value=preset)
    scen_info = scenario_preset(preset)
    if scen_info:
        st.markdown(
            f'<div style="background:linear-gradient(180deg, rgba(22,32,51,0.78) 0%, rgba(17,24,39,0.95) 100%); border:1px solid #1e293b; border-left:3px solid #d4af37; border-radius:10px; padding:11px 15px; margin-bottom:10px;">'
            f'<div style="font-size:0.74rem; font-weight:700; text-transform:uppercase; letter-spacing:0.07em; color:#d4af37; margin-bottom:4px;">◈ Quantitative Trade Scenario Hypothesis</div>'
            f'<div style="font-size:0.83rem; color:#f1f5f9; line-height:1.45; margin-bottom:5px;">{scen_info["hypothesis"]}</div>'
            f'<div style="font-family:var(--vl-font-mono, monospace); font-size:0.73rem; color:#38bdf8;"><b>Streamlit Triggers:</b> {scen_info["streamlit_triggers"]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    def _parse_hhmm(val, fallback):
        try:
            parts = [int(x) for x in str(val).split(':')]
            return time(parts[0], parts[1])
        except Exception:
            return fallback

    default_entry = _parse_hhmm(scen_info['entry'], time(9, 20)) if scen_info else time(9, 20)
    default_exit = _parse_hhmm(scen_info['exit'], time(15, 15)) if scen_info else time(15, 15)
    rank_options = ['Nearest', 'Next', 'Next+1']
    default_rank_idx = rank_options.index(scen_info['expiry_rank']) if scen_info and scen_info.get('expiry_rank') in rank_options else 0
    bias_options = ['Neutral', 'Bullish', 'Bearish']
    default_bias_idx = bias_options.index(scen_info['bias']) if scen_info and scen_info.get('bias') in bias_options else 0
    default_dte = tuple(scen_info['dte']) if scen_info and scen_info.get('dte') else (1, 35)
    default_stop = float(scen_info.get('stop', 0.)) if scen_info else 0.
    default_target = float(scen_info.get('target', 0.)) if scen_info else 0.
    preset_filters = dict((m, (lo, hi)) for m, lo, hi in scen_info.get('filters', [])) if scen_info else {}

    a, b, c = st.columns(3)
    symbol = a.selectbox('Index', list(INDICES), key='builder_symbol')
    def_start, def_end, cov_note = _default_backtest_date_window(symbol)
    start = b.date_input('From', def_start, key=f'builder_start_{symbol}')
    end = c.date_input('To', def_end, key=f'builder_end_{symbol}')
    if cov_note != "Default range":
        st.caption(f"Available data window for {symbol}: {cov_note}")

    a, b, c, d = st.columns(4)
    entry = a.time_input('Entry · IST', default_entry, key='builder_entry_' + preset)
    exit = b.time_input('Exit · IST', default_exit, key='builder_exit_' + preset)
    lots = c.number_input('Strategy lots', min_value=1, value=1, step=1)
    expiry_rank = d.selectbox('Expiry rank', rank_options, index=default_rank_idx, key='builder_rank_' + preset)

    a, b, c = st.columns(3)
    trigger = a.selectbox('Trigger logic', ['Market open', 'Time based'], key='builder_trigger')
    mode = b.selectbox('Allocation', ['Equal', 'By margin', 'By notional'], key='builder_mode')
    direction = c.selectbox('Bias', bias_options, index=default_bias_idx, key='builder_bias_' + preset)

    with st.expander('Session & expiry rules'):
        weekdays = st.multiselect('Trading weekdays', ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'], default=['Mon', 'Tue', 'Wed', 'Thu', 'Fri'])
        dte = st.slider('Calendar days to expiry', 1, 365, default_dte, key='builder_dte_' + preset)

    st.markdown('**Option legs**')
    leg_df = pd.DataFrame(template(preset))[['Side', 'Type', 'Offset', 'Ratio']]
    legs = st.data_editor(
        leg_df,
        num_rows='dynamic',
        hide_index=True,
        width='stretch',
        key='builder_legs_' + preset,
        column_config={
            'Side': st.column_config.SelectboxColumn(options=['Buy', 'Sell'], required=True),
            'Type': st.column_config.SelectboxColumn(options=['CE', 'PE'], required=True),
            'Offset': st.column_config.NumberColumn('Strike offset from ATM · points', required=True, step=50),
            'Ratio': st.column_config.NumberColumn('Units multiplier', required=True, min_value=1, max_value=20, step=1),
        },
    )
    st.caption("ATM uses the previous spot-minute close. Strike offsets are points; runs use each contract's saved lot size.")

    with st.expander('Costs & capital'):
        a, b, c = st.columns(3)
        capital_allocated = a.number_input('Capital allocated · INR · 0 = no cap', min_value=0., value=0., step=50000.)
        margin_per_lot = b.number_input('Broker margin per strategy lot · INR', min_value=0., value=0., step=5000.)
        fees = c.number_input('Broker fee model · %', min_value=0., max_value=10., value=0.15, step=0.01)
        estimate_margin = st.checkbox(
            'Auto-estimate exchange SPAN + Exposure margin proxy for short legs',
            value=True,
            help='Approximates SEBI/NSE SPAN and exposure margin (~10% notional for naked shorts / high leverage).',
        )
        st.caption('Enter broker margin per strategy lot, or leave at zero to use the automated exchange SPAN + Exposure proxy.')
        a, b, c, d = st.columns(4)
        stop = a.number_input('Portfolio stop · INR · 0 = off', min_value=0., value=default_stop, step=500., key='builder_stop_' + preset)
        target = b.number_input('Portfolio target · INR · 0 = off', min_value=0., value=default_target, step=500., key='builder_target_' + preset)
        max_trades = c.number_input('Max trades per day', min_value=0, value=0, step=1)
        trade_size = d.number_input('Trade size units', min_value=1, value=1, step=1)
        st.caption('Stops and targets use estimated P&L at minute close and fill at the next minute open. Actual losses can exceed the stop.')

    filters = []
    with st.expander('Volatility Lab entry conditions', expanded=bool(preset_filters)):
        selected = st.multiselect(
            'Require saved signal',
            ['IV-RV Spread', 'IV', '25 Delta reversal', 'HV30'],
            default=list(preset_filters.keys()),
            key='builder_sig_sel_' + preset,
        )
        for metric in selected:
            def_lo, def_hi = preset_filters.get(metric, (0., 100.))
            a, b = st.columns(2)
            low = a.number_input(metric + ' minimum', value=float(def_lo), key=f'builder_low_{preset}_{metric}')
            high = b.number_input(metric + ' maximum', value=float(def_hi), key=f'builder_high_{preset}_{metric}')
            filters.append((metric, low, high))
        st.caption('Signals must be same-expiry and no more than 15 minutes old. Historical OHLC has no IV or delta; missing signals exclude that session.')

    settings = dict(
        name=name,
        template=preset,
        symbol=symbol,
        start=str(start),
        end=str(end),
        entry=entry.strftime('%H:%M'),
        exit=exit.strftime('%H:%M'),
        lots=int(lots),
        capital_allocated=capital_allocated,
        margin_per_lot=margin_per_lot,
        estimate_margin=estimate_margin,
        fees=fees,
        trigger=trigger,
        mode=mode,
        expiry_rank=expiry_rank,
        trade_size=trade_size,
        stop=stop,
        target=target,
        max_trades=max_trades,
        dte=dte,
        bias=direction,
        weekdays=weekdays,
        legs=legs.to_dict('records'),
        filters=filters,
    )
    if st.button('Run backtest', type='primary', disabled=jobs.pending(st.session_state)):
        try:
            validate(settings)
            if not weekdays:
                raise ValueError('Select at least one weekday.')
            if any(low > high for _, low, high in filters):
                raise ValueError('Filter minimum must not exceed maximum.')
            st.session_state.pop('_backtest_error', None)
            jobs.submit(st.session_state, 'Options', settings)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            st.error('Cannot run: ' + str(exc))

    # Automatically collect finished backtest job if ready so results appear without requiring an extra click
    task = st.session_state.get(jobs.SLOT)
    if task is not None and task.future.done():
        outcome = jobs.collect(st.session_state)
        if outcome and not outcome[0]:
            st.session_state['_backtest_error'] = outcome[1]
            st.error('Cannot run: ' + str(outcome[1]))

    if 'builder_result' in st.session_state:
        report(*st.session_state.builder_result)

    saved = st.expander('Saved runs', key='backtest_saved_runs', on_change='rerun')
    if saved.open:
        with saved:
            files = sorted(STORE.glob('*.json'), reverse=True) if STORE.exists() else []
            if files:
                summaries = []
                opt_files = []
                for file in files:
                    try:
                        r = json.loads(file.read_text(encoding='utf-8'))
                        if r.get('settings', {}).get('engine') == 'dated_futures_v1':
                            continue
                        info = file.stat()
                        summaries.append(saved_run_summary(str(file), (info.st_mtime_ns, info.st_size)))
                        opt_files.append(file)
                    except Exception:
                        continue
                if opt_files:
                    st.dataframe(pd.DataFrame(summaries[:20]).round(2), hide_index=True, width='stretch')
                    st.caption('Latest options runs · compare matching dates, size, costs, and coverage.')
                    chosen = st.selectbox('Run', opt_files[:20], format_func=lambda p: p.stem)
                    if st.button('Open saved report'):
                        r = json.loads(chosen.read_text(encoding='utf-8'))
                        st.session_state.builder_result = (pd.DataFrame(r['trades']), pd.DataFrame(r['skips']), r['settings'], r['coverage'])
                        st.rerun()
                else:
                    st.caption('No saved options runs yet.')
            else:
                st.caption('No saved runs yet.')


@st.cache_data(max_entries=40, show_spinner=False)
def saved_run_summary(path, revision):
    from pathlib import Path
    file = Path(path)
    r = json.loads(file.read_text(encoding='utf-8'))
    t = r['trades']
    cfg = r['settings']
    return {
        'Run': file.stem,
        'Strategy': cfg.get('name'),
        'Index': cfg['symbol'],
        'From': cfg['start'],
        'To': cfg['end'],
        'Trades': len(t),
        'Net INR': sum(row['Net'] for row in t) if t else None,
        'Total costs INR': sum(row.get('Cost', 0) for row in t) if t else None,
        'Total premium INR': sum(row.get('Premium', 0) for row in t) if t else None,
    }


@st.fragment(run_every=5)
def render_inventory():
    inventory, busy, error = jobs.inventory_status()
    if busy:
        st.caption('Updating archive coverage in background')
    if error:
        st.warning(error)
    if inventory:
        st.caption('Request inventory · ' + inventory['generated_at'][:19].replace('T', ' ') + ' UTC · refreshes at most once per minute')
        st.dataframe(pd.DataFrame(inventory['summary']), hide_index=True, width='stretch')
        st.caption('Expiry coverage does not guarantee candle coverage. Overlapping rows are retained; empty responses are not treated as market closures.')
        st.download_button(
            'Download contract coverage',
            lambda: pd.DataFrame(inventory['contracts']).to_csv(index=False).encode(),
            'historical_contract_coverage.csv',
            'text/csv',
            on_click='ignore',
        )


@st.fragment(run_every=2)
def render_run_status():
    task = st.session_state.get(jobs.SLOT)
    if task is None:
        return
    if not task.future.done():
        st.info(f"{task.kind} backtest · {task.phase} · {task.settings.get('name', 'Strategy')}")
        st.caption('Settings for this run are frozen. You can keep using the lab.')
    else:
        outcome = jobs.collect(st.session_state)
        if outcome and not outcome[0]:
            st.session_state['_backtest_error'] = outcome[1]
        st.rerun()


def render():
    _apply_backtest_theme()
    completed = st.session_state.pop('_backtest_completed_kind', None)
    if completed:
        st.session_state.backtest_market = completed
    kind = st.session_state.get('backtest_market', 'Options')
    if st.session_state.get('_backtest_error'):
        st.error('Cannot run: ' + st.session_state['_backtest_error'])
    checked_charges()
    if kind == 'Futures':
        from futures_backtest_ui import render as futures_render
        futures_render()
    else:
        render_options()
    render_run_status()
