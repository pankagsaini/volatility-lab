from datetime import date,time
import json
import pandas as pd
import streamlit as st
from backtest_engine import ROOT
from collector_support import read_json
from upstox_data import INDICES
from strategy_backtest import TEMPLATES,template,validate
from backtest_report import render as report,STORE
import backtest_jobs as jobs

def checked_charges():
    return jobs.fee_status()

def _apply_backtest_theme():
    st.markdown('''
    <style>
    :root {
        --bg: #0b1220;
        --panel: #111d2d;
        --panel-soft: #13253d;
        --border: #243447;
        --text: #e2e8f0;
        --muted: #94a3b8;
        --primary: #10b981;
        --success: #34d399;
        --warning: #fbbf24;
        --danger: #f87171;
    }

    .stApp {
        background: var(--bg);
        color: var(--text);
    }

    [data-testid="stHeader"] {
        background: rgba(11, 18, 32, 0.8);
        box-shadow: none;
    }

    [data-testid="stDataFrame"], [data-testid="stExpander"] {
        border: 1px solid var(--border);
        border-radius: 12px;
        overflow: hidden;
    }

    [data-testid="stDataFrame"] {
        background: #0d1a2b;
    }

    [data-testid="stExpander"] {
        background: var(--panel-soft);
    }

    .stButton > button {
        border-radius: 8px;
        background: linear-gradient(180deg, #17314d 0%, #0f1d33 100%);
        color: var(--text);
        border: 1px solid rgba(52, 211, 153, 0.6);
    }

    .stButton > button:hover {
        border-color: rgba(16, 185, 129, 0.9);
    }

    button[kind="primary"] {
        border-radius: 8px;
        background: linear-gradient(180deg, #17314d 0%, #0f1d33 100%);
        border: 1px solid rgba(52, 211, 153, 0.6);
        color: var(--text);
    }

    div[data-testid="stMetric"] {
        background: linear-gradient(180deg, #122235 0%, #0f1b2d 100%);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 16px 18px;
    }

    h2, h3 {
        color: var(--text);
        letter-spacing: -0.03em;
    }

    .stCaptionContainer {
        color: var(--muted);
    }
    </style>
    ''', unsafe_allow_html=True)

def render_options():
 _apply_backtest_theme()
 st.subheader('Strategy builder')
 a,b=st.columns([2,3]);preset=a.selectbox('Starting template',list(TEMPLATES),index=2)
 name=b.text_input('Strategy name',value=preset)
 # Live controls are outside a form so adding a filter or editing legs is immediate.
 a,b,c=st.columns(3);symbol=a.selectbox('Index',list(INDICES),key='builder_symbol');start=b.date_input('From',date(2024,10,1),key='builder_start');end=c.date_input('To',date(2024,10,31),key='builder_end')
 a,b,c,d=st.columns(4);entry=a.time_input('Entry · IST',time(9,20));exit=b.time_input('Exit · IST',time(15,15));lots=c.number_input('Strategy lots',min_value=1,value=1,step=1);expiry_rank=d.selectbox('Expiry rank',['Nearest','Next','Next+1'],index=0)
 a,b,c=st.columns(3);trigger=a.selectbox('Trigger logic',['Market open','Time based'],key='builder_trigger');mode=b.selectbox('Allocation',['Equal','By margin','By notional'],key='builder_mode');direction=c.selectbox('Bias',['Neutral','Bullish','Bearish'],key='builder_bias')
 with st.expander('Session & expiry rules'):
  weekdays=st.multiselect('Trading weekdays',['Mon','Tue','Wed','Thu','Fri'],default=['Mon','Tue','Wed','Thu','Fri'])
  dte=st.slider('Calendar days to expiry',1,365,(1,35))
 st.markdown('**Option legs**')
 legs=st.data_editor(pd.DataFrame(template(preset)),num_rows='dynamic',hide_index=True,width='stretch',key='builder_legs_'+preset,column_config={
  'Side':st.column_config.SelectboxColumn(options=['Buy','Sell'],required=True),
  'Type':st.column_config.SelectboxColumn(options=['CE','PE'],required=True),
  'Offset':st.column_config.NumberColumn('Strike offset from ATM · points',required=True,step=50),
  'Ratio':st.column_config.NumberColumn('Units multiplier',required=True,min_value=1,max_value=20,step=1)})
 st.caption('ATM uses the previous spot-minute close. Strike offsets are points; runs use each contract’s saved lot size.')
 with st.expander('Costs & capital'):
  a,b,c=st.columns(3);capital_allocated=a.number_input('Capital allocated · INR · 0 = no cap',min_value=0.,value=0.,step=50000.);margin_per_lot=b.number_input('Broker margin per strategy lot · INR',min_value=0.,value=0.,step=5000.);fees=c.number_input('Broker fee model · %',min_value=0.,max_value=10.,value=0.15,step=0.01)
  estimate_margin=st.checkbox('Auto-estimate exchange SPAN + Exposure margin proxy for short legs',value=True,help='Approximates SEBI/NSE SPAN and exposure margin (~10% notional for naked shorts / high leverage).')
  st.caption('Enter broker margin per strategy lot, or leave at zero to use the automated exchange SPAN + Exposure proxy.')
 a,b,c,d=st.columns(4);stop=a.number_input('Portfolio stop · INR · 0 = off',min_value=0.,value=0.,step=500.);target=b.number_input('Portfolio target · INR · 0 = off',min_value=0.,value=0.,step=500.);max_trades=c.number_input('Max trades per day',min_value=0,value=0,step=1);trade_size=d.number_input('Trade size units',min_value=1,value=1,step=1)
 st.caption('Stops and targets use estimated P&L at minute close and fill at the next minute open. Actual losses can exceed the stop.')
 filters=[]
 with st.expander('Volatility Lab entry conditions'):
  selected=st.multiselect('Require saved signal',['IV-RV Spread','IV','25 Delta reversal','HV30'])
  for metric in selected:
   a,b=st.columns(2);low=a.number_input(metric+' minimum',value=0.,key='builder_low_'+metric);high=b.number_input(metric+' maximum',value=100.,key='builder_high_'+metric)
   filters.append((metric,low,high))
  st.caption('Signals must be same-expiry and no more than 15 minutes old. Historical OHLC has no IV or delta; missing signals exclude that session.')
 settings=dict(name=name,symbol=symbol,start=str(start),end=str(end),entry=entry.strftime('%H:%M'),exit=exit.strftime('%H:%M'),lots=int(lots),capital_allocated=capital_allocated,margin_per_lot=margin_per_lot,fees=fees,trigger=trigger,mode=mode,expiry_rank=expiry_rank,trade_size=trade_size,stop=stop,target=target,max_trades=max_trades,dte=dte,bias=direction,weekdays=weekdays)
 if st.button('Run backtest',type='primary',disabled=jobs.pending(st.session_state)):
  try:
   validate(settings)
   if not weekdays:raise ValueError('Select at least one weekday.')
   if any(low>high for _,low,high in filters):raise ValueError('Filter minimum must not exceed maximum.')
   st.session_state.pop('_backtest_error',None)
   jobs.submit(st.session_state,'Options',settings)
  except (ValueError,KeyError,TypeError,OSError) as exc:st.error('Cannot run: '+str(exc))
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
  left.success(task.kind+' backtest finished') if task.future.exception() is None else left.warning('Backtest needs attention')
  if right.button('View results',key='view_completed_backtest',width='stretch'):
   outcome=jobs.collect(st.session_state)
   if outcome and not outcome[0]:st.session_state['_backtest_error']=outcome[1]
   st.rerun()


def render():
 _apply_backtest_theme()
 completed=st.session_state.pop('_backtest_completed_kind',None)
 if completed:st.session_state.backtest_market=completed
 kind=st.session_state.get('backtest_market','Options')
 if st.session_state.get('_backtest_error'):st.error('Cannot run: '+st.session_state['_backtest_error'])
 checked_charges()
 assumptions=st.expander('Archive & execution assumptions',key='backtest_archive_details',on_change='rerun')
 if assumptions.open:
  with assumptions:
   if (ROOT/'queue.sqlite').exists():
    render_inventory()
   audit=read_json(ROOT.parent/'audit'/'candles'/'status.json')
   if audit:
    st.caption('Candle validation · '+audit.get('status','unknown')+' · '+str(audit.get('contracts_checked',0))+' instruments checked')
    counts=audit.get('counts',{})
    st.write({k:counts.get(k,0) for k in ('raw_rows','identical_duplicate_rows','conflicting_rows','invalid_values','invalid_timestamp','unreadable_files')})
    st.caption('Zero-volume rows are retained; unobserved minute slots are not automatically classified as failures.')
   info=checked_charges();st.caption('Upstox fee page checked '+info.get('checked','unavailable'))
   if info.get('status')=='check_failed':st.warning(info.get('warning'))
   st.caption('Charge estimates exclude financing, margin, contract-note rounding and expiry settlement. Historical candles have no wide-spread filter.')
 if kind=='Futures':
  from futures_backtest_ui import render as futures_render
  futures_render()
 else:render_options()
 render_run_status()
