"""Unified backtest cockpit: builder, charge validation, and run review."""
from datetime import date,time
import json
import pandas as pd
import streamlit as st
from backtest_engine import ROOT
from collector_support import read_json
from upstox_data import INDICES
from strategy_backtest import TEMPLATES,SCENARIO_PRESETS,scenario_preset,template,validate
from backtest_report import render as report,STORE
import backtest_jobs as jobs

def _apply_backtest_theme():
    import ui_theme
    ui_theme.install_classic_modern_theme()

def checked_charges():
    c=read_json(ROOT.parent/'charges'/'upstox'/'current.json')
    if not c:
        st.warning('Broker charge configuration is missing; backtests will default to flat 0.15% fee model.')

def render_options():
    _apply_backtest_theme()
    st.subheader('Strategy builder')
    a,b=st.columns([2,3]);preset=a.selectbox('Starting template',list(TEMPLATES),index=4 if len(TEMPLATES)>4 else 2)
    name=b.text_input('Strategy name',value=preset)
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

    default_entry = _parse_hhmm(scen_info['entry'], time(9,20)) if scen_info else time(9,20)
    default_exit = _parse_hhmm(scen_info['exit'], time(15,15)) if scen_info else time(15,15)
    rank_options = ['Nearest','Next','Next+1']
    default_rank_idx = rank_options.index(scen_info['expiry_rank']) if scen_info and scen_info.get('expiry_rank') in rank_options else 0
    bias_options = ['Neutral','Bullish','Bearish']
    default_bias_idx = bias_options.index(scen_info['bias']) if scen_info and scen_info.get('bias') in bias_options else 0
    default_dte = tuple(scen_info['dte']) if scen_info and scen_info.get('dte') else (1, 35)
    default_stop = float(scen_info.get('stop', 0.)) if scen_info else 0.
    default_target = float(scen_info.get('target', 0.)) if scen_info else 0.
    preset_filters = dict((m, (lo, hi)) for m, lo, hi in scen_info.get('filters', [])) if scen_info else {}

    # Live controls are outside a form so adding a filter or editing legs is immediate.
    a,b,c=st.columns(3);symbol=a.selectbox('Index',list(INDICES),key='builder_symbol');start=b.date_input('From',date(2024,10,1),key='builder_start');end=c.date_input('To',date(2024,10,31),key='builder_end')
    a,b,c,d=st.columns(4);entry=a.time_input('Entry · IST',default_entry,key='builder_entry_'+preset);exit=b.time_input('Exit · IST',default_exit,key='builder_exit_'+preset);lots=c.number_input('Strategy lots',min_value=1,value=1,step=1);expiry_rank=d.selectbox('Expiry rank',rank_options,index=default_rank_idx,key='builder_rank_'+preset)
    a,b,c=st.columns(3);trigger=a.selectbox('Trigger logic',['Market open','Time based'],key='builder_trigger');mode=b.selectbox('Allocation',['Equal','By margin','By notional'],key='builder_mode');direction=c.selectbox('Bias',bias_options,index=default_bias_idx,key='builder_bias_'+preset)
    with st.expander('Session & expiry rules'):
        weekdays=st.multiselect('Trading weekdays',['Mon','Tue','Wed','Thu','Fri'],default=['Mon','Tue','Wed','Thu','Fri'])
        dte=st.slider('Calendar days to expiry',1,365,default_dte,key='builder_dte_'+preset)
    st.markdown('**Option legs**')
    leg_df = pd.DataFrame(template(preset))[['Side', 'Type', 'Offset', 'Ratio']]
    legs=st.data_editor(leg_df,num_rows='dynamic',hide_index=True,width='stretch',key='builder_legs_'+preset,column_config={
        'Side':st.column_config.SelectboxColumn(options=['Buy','Sell'],required=True),
        'Type':st.column_config.SelectboxColumn(options=['CE','PE'],required=True),
        'Offset':st.column_config.NumberColumn('Strike offset from ATM · points',required=True,step=50),
        'Ratio':st.column_config.NumberColumn('Units multiplier',required=True,min_value=1,max_value=20,step=1)})
    st.caption('ATM uses the previous spot-minute close. Strike offsets are points; runs use each contract\'s saved lot size.')
    with st.expander('Costs & capital'):
        a,b,c=st.columns(3);capital_allocated=a.number_input('Capital allocated · INR · 0 = no cap',min_value=0.,value=0.,step=50000.);margin_per_lot=b.number_input('Broker margin per strategy lot · INR',min_value=0.,value=0.,step=5000.);fees=c.number_input('Broker fee model · %',min_value=0.,max_value=10.,value=0.15,step=0.01)
        estimate_margin=st.checkbox('Auto-estimate exchange SPAN + Exposure margin proxy for short legs',value=True,help='Approximates SEBI/NSE SPAN and exposure margin (~10% notional for naked shorts / high leverage).')
        st.caption('Enter broker margin per strategy lot, or leave at zero to use the automated exchange SPAN + Exposure proxy.')
        a,b,c,d=st.columns(4);stop=a.number_input('Portfolio stop · INR · 0 = off',min_value=0.,value=default_stop,step=500.,key='builder_stop_'+preset);target=b.number_input('Portfolio target · INR · 0 = off',min_value=0.,value=default_target,step=500.,key='builder_target_'+preset);max_trades=c.number_input('Max trades per day',min_value=0,value=0,step=1);trade_size=d.number_input('Trade size units',min_value=1,value=1,step=1)
        st.caption('Stops and targets use estimated P&L at minute close and fill at the next minute open. Actual losses can exceed the stop.')
    filters=[]
    with st.expander('Volatility Lab entry conditions',expanded=bool(preset_filters)):
        selected=st.multiselect('Require saved signal',['IV-RV Spread','IV','25 Delta reversal','HV30'],default=list(preset_filters.keys()),key='builder_sig_sel_'+preset)
        for metric in selected:
            def_lo, def_hi = preset_filters.get(metric, (0., 100.))
            a,b=st.columns(2);low=a.number_input(metric+' minimum',value=float(def_lo),key=f'builder_low_{preset}_{metric}');high=b.number_input(metric+' maximum',value=float(def_hi),key=f'builder_high_{preset}_{metric}')
            filters.append((metric,low,high))
        st.caption('Signals must be same-expiry and no more than 15 minutes old. Live Greek signals are available for Oct 20–Nov 6, 2026. For 2022–2025 backtests, leave signal filters empty to run on pure 1-minute OHLC candles.')

    settings=dict(name=name,template=preset,symbol=symbol,start=str(start),end=str(end),entry=entry.strftime('%H:%M'),exit=exit.strftime('%H:%M'),lots=int(lots),capital_allocated=capital_allocated,margin_per_lot=margin_per_lot,fees=fees,trigger=trigger,mode=mode,expiry_rank=expiry_rank,trade_size=trade_size,stop=stop,target=target,max_trades=max_trades,dte=dte,bias=direction,weekdays=weekdays,legs=legs.to_dict('records'),filters=filters)
    
    btn_col, stat_col = st.columns([1.5, 3.5])
    with btn_col:
        run_clicked = st.button('Run backtest', type='primary', disabled=jobs.pending(st.session_state))
    if run_clicked:
        try:
            validate(settings)
            if not weekdays:raise ValueError('Select at least one weekday.')
            if any(low>high for _,low,high in filters):raise ValueError('Filter minimum must not exceed maximum.')
            st.session_state.pop('_backtest_error',None)
            jobs.submit(st.session_state,'Options',settings)
            try:
                st.rerun(scope='app')
            except TypeError:
                st.rerun()
        except (ValueError,KeyError,TypeError,OSError) as exc:st.error('Cannot run: '+str(exc))

    render_run_status()

    if 'builder_result' in st.session_state:report(*st.session_state.builder_result)
    saved=st.expander('Saved runs',key='backtest_saved_runs',on_change='rerun')
    if saved.open:
        with saved:
            files=sorted(STORE.glob('*.json'),reverse=True) if STORE.exists() else []
            if files:
                summaries=[]
                opt_files=[]
                for file in files:
                    try:
                        r=json.loads(file.read_text(encoding='utf-8'))
                        if r.get('settings',{}).get('engine')=='dated_futures_v1':continue
                        info=file.stat()
                        summaries.append(saved_run_summary(str(file),(info.st_mtime_ns,info.st_size)))
                        opt_files.append(file)
                    except Exception:continue
                if opt_files:
                    st.dataframe(pd.DataFrame(summaries[:20]).round(2),hide_index=True,width='stretch')
                    st.caption('Latest options runs · compare matching dates, size, costs, and coverage.')
                    chosen=st.selectbox('Run',opt_files[:20],format_func=lambda p:p.stem)
                    if st.button('Open saved report'):
                        r=json.loads(chosen.read_text(encoding='utf-8'));st.session_state.builder_result=(pd.DataFrame(r['trades']),pd.DataFrame(r['skips']),r['settings'],r['coverage']);st.rerun()
                else:st.caption('No saved options runs yet.')
            else:st.caption('No saved runs yet.')

@st.cache_data(max_entries=40,show_spinner=False)
def saved_run_summary(path,revision):
    from pathlib import Path
    file=Path(path);r=json.loads(file.read_text(encoding='utf-8'));t=r['trades'];cfg=r['settings']
    return {'Run':file.stem,'Strategy':cfg.get('name'),'Index':cfg['symbol'],'From':cfg['start'],'To':cfg['end'],'Trades':len(t),'Net INR':sum(row['Net'] for row in t) if t else None,'Total costs INR':sum(row.get('Cost',0) for row in t) if t else None,'Total premium INR':sum(row.get('Premium',0) for row in t) if t else None}

@st.fragment(run_every=5)
def render_inventory():
    inventory,busy,error=jobs.inventory_status()
    if busy:st.caption('Updating archive coverage in background')
    if error:st.warning(error)
    if inventory:
        st.caption('Request inventory · '+inventory['generated_at'][:19].replace('T',' ')+' UTC · refreshes at most once per minute')
        st.dataframe(pd.DataFrame(inventory['summary']),hide_index=True,width='stretch')
        st.caption('Expiry coverage does not guarantee candle coverage. Overlapping rows are retained; empty responses are not treated as market closures.')
        st.download_button('Download contract coverage',lambda:pd.DataFrame(inventory['contracts']).to_csv(index=False).encode(),'historical_contract_coverage.csv','text/csv',on_click='ignore')

@st.fragment(run_every=2)
def render_run_status():
    task=st.session_state.get(jobs.SLOT)
    if task is None:return
    if not task.future.done():
        st.info(f"{task.kind} backtest · {task.phase} · {task.settings.get('name','Strategy')}")
        st.caption('Settings for this run are frozen. You can keep using the lab.')
    else:
        left,right=st.columns([4,1])
        if task.future.exception() is None:
            left.success(task.kind+' backtest finished')
        else:
            left.warning(f"Backtest needs attention: {task.future.exception()}")
        if right.button('View results',key='view_completed_backtest',width='stretch'):
            outcome=jobs.collect(st.session_state)
            if outcome and not outcome[0]:st.session_state['_backtest_error']=outcome[1]
            try:
                st.rerun(scope='app')
            except TypeError:
                st.rerun()

def render():
    _apply_backtest_theme()
    completed=st.session_state.pop('_backtest_completed_kind',None)
    if completed:st.session_state.backtest_market=completed
    kind=st.session_state.get('backtest_market','Options')
    if st.session_state.get('_backtest_error'):st.error('Cannot run: '+st.session_state['_backtest_error'])
    checked_charges()
    if kind=='Futures':
        from futures_backtest_ui import render as futures_render
        futures_render()
    else:render_options()
