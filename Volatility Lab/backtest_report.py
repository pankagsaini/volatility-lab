"""Reusable closed-trade reporting with explicit data exclusions."""
import json,uuid
from datetime import datetime
from pathlib import Path
import numpy as np,pandas as pd,plotly.graph_objects as go,streamlit as st
from plotly.subplots import make_subplots
STORE=Path(__file__).resolve().parent/'data'/'backtest_runs'

def save_run(settings,trades,skips,coverage):
 STORE.mkdir(parents=True,exist_ok=True)
 name=datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
 record={'settings':settings,'coverage':coverage,'trades':json.loads(trades.to_json(orient='records')),'skips':json.loads(skips.to_json(orient='records'))}
 path=STORE/(name+'.json');temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
 try:
  temporary.write_text(json.dumps(record,indent=2),encoding='utf-8')
  temporary.replace(path)
 finally:temporary.unlink(missing_ok=True)
 return name

def render_unfinished_position(coverage):
 position=coverage.get('unfinished_position')
 if not isinstance(position,dict):return
 st.subheader('Unfinished futures position')
 st.warning('This position is excluded from realised P&L, drawdown, win rate and profit factor.')
 fields=['Status','Entry','Near expiry','Far expiry','Direction','Quantity per leg','Entry carry %','Entry fill near','Entry fill far','Estimated entry costs','Last mark date','Last mark near close','Last mark far close','Last mark carry %','Unrealised gross','Unrealised P&L before exit costs','Mark status']
 st.dataframe(pd.DataFrame([{key:position.get(key) for key in fields}]).round(2),hide_index=True,width='stretch')
 st.caption('Mark uses the latest exact-contract close, not an exit fill. Unrealised P&L includes entry costs; exit costs are excluded.')

def render(trades,skips,settings,coverage):
 st.subheader('Results')
 st.caption(f"{settings.get('name','Strategy')} · {settings['symbol']} · {settings['start']} — {settings['end']} · frozen run settings")
 st.caption(f"{len(skips)} excluded sessions · {coverage.get('conflicts',0)} conflicting timestamps withheld")
 if (settings.get('engine')!='dated_futures_v1'
     and (coverage.get('download_state_at_run')!='finished_available_requests'
          or int(coverage.get('confirmed_sessions_missing_minutes',0) or 0)
          or int(coverage.get('unverified_dates',0) or 0))):
  st.warning('Archive is unfinished or coverage is uncertain; results may omit sessions.')
 render_unfinished_position(coverage)
 if trades.empty:st.info('No complete trades. Open Data coverage for the exclusion reasons.');
 else:
  t=trades.copy();dates=pd.to_datetime(t['Date'] if 'Date' in t else t['Exit']);net=t.Net.astype(float);equity=net.cumsum();dd=equity-equity.cummax().clip(lower=0)
  wins=net[net>0];losses=net[net<0];factor=wins.sum()/abs(losses.sum()) if len(losses) else None
  a,b,c,d=st.columns(4);a.metric('Net P&L',f'₹{net.sum():,.0f}');b.metric('Closed-trade drawdown',f'₹{-dd.min():,.0f}');c.metric('Win rate',f'{(net>0).mean()*100:.1f}%');d.metric('Profit factor',f'{factor:.2f}' if factor is not None else '—')
  tabs=st.tabs(['Performance','Monthly & weekday','Trade ledger','Data coverage'],
               key='backtest_report_tab_'+settings.get('engine','legacy'),on_change='rerun')
  if tabs[0].open:
   with tabs[0]:
    fig=make_subplots(rows=2,cols=1,shared_xaxes=True,row_heights=[.7,.3],vertical_spacing=.08)
    xs=[dates.iloc[0]-pd.Timedelta(seconds=1)]+dates.tolist()
    fig.add_trace(go.Scatter(x=xs,y=[0]+equity.tolist(),name='Net P&L',line_color='#50c5b4'),row=1,col=1)
    fig.add_trace(go.Scatter(x=xs,y=[0]+dd.tolist(),name='Drawdown',fill='tozeroy',line_color='#f17e91'),row=2,col=1)
    fig.update_layout(height=470,template='plotly_dark',paper_bgcolor='#0c1321',plot_bgcolor='#0c1321',margin=dict(l=15,r=15,t=25,b=15),legend=dict(orientation='h'),hovermode='x unified')
    fig.update_yaxes(tickprefix='₹',tickformat=',.0f');st.plotly_chart(fig,width='stretch')
    a,b,c,d=st.columns(4);a.metric('Average trade',f'₹{net.mean():,.0f}');b.metric('Average win / loss',f'{wins.mean() if len(wins) else 0:,.0f} / {losses.mean() if len(losses) else 0:,.0f}');c.metric('Total trading costs',f'₹{(t.Charges+t.Slippage).sum():,.0f}');d.metric('Completed trades',str(len(t)))
    if 'Capital required' in t:
     covered=pd.to_numeric(t['Capital required'],errors='coerce').notna()
     st.caption(f'Return on capital is available for {covered.sum()} of {len(t)} trades. Capital source: {t["Capital source"].iloc[0] if "Capital source" in t.columns and len(t) else "entry debit or margin allocation"}.')
    else:st.caption('Legacy fixed-unit run: no historical lot-size, capital or return-on-capital fields were recorded.')
    st.caption(f"Costs use {settings.get('charges','saved-run fee assumptions')} plus entered slippage. Drawdown uses closed trades; intratrade risk and margin are not modeled.")
  if tabs[1].open:
   with tabs[1]:
    grouped=pd.DataFrame({'Month':dates.dt.strftime('%Y-%m'),'Weekday':dates.dt.day_name(),'Net':net})
    a,b=st.columns(2)
    a.dataframe(grouped.groupby('Month').Net.agg(['count','sum','mean']).rename(columns={'count':'Trades','sum':'Net INR','mean':'Average INR'}).round(2),width='stretch')
    b.dataframe(grouped.groupby('Weekday').Net.agg(['count','sum','mean']).rename(columns={'count':'Trades','sum':'Net INR','mean':'Average INR'}).round(2),width='stretch')
  if tabs[2].open:
   with tabs[2]:
    display=t.copy();display['Total costs']=display.Charges+display.Slippage
    cols=[c for c in ['Date','Entry','Exit','Expiry','Entry time','Exit time','Exit reason','Near expiry','Far expiry','Entry carry %','Exit signal carry %','Reason','Sizing','Strategy lots','Entry premium debit','Capital required','Capital source','Net / capital %','Gross','Total costs','Net'] if c in display]
    display=display[cols]
    st.dataframe(display.round(2),hide_index=True,width='stretch')
    if 'Legs' in t:
     labels=(t['Date'].astype(str)+' · '+t['Exit reason'].astype(str)).tolist()
     idx=st.selectbox('Inspect trade',range(len(t)),format_func=labels.__getitem__)
     st.dataframe(pd.DataFrame(json.loads(t.iloc[idx].Legs)).round(2),hide_index=True,width='stretch')
    st.download_button('Export trades',lambda frame=display:frame.to_csv(index=False).encode(),
                       'strategy_trades.csv','text/csv',on_click='ignore')
  if tabs[3].open:
   with tabs[3]:
    st.json({k:v for k,v in coverage.items() if k not in ('sessions','unfinished_position')})
    if coverage.get('sessions'):st.dataframe(pd.DataFrame(coverage['sessions']),hide_index=True,width='stretch')
    if not skips.empty:st.dataframe(skips,hide_index=True,width='stretch')
    st.caption('Confirmed sessions without data are listed separately. Unverified dates are not classified as holidays; exclusions are not zero-P&L trades.')
 if trades.empty:
  if not skips.empty:st.dataframe(skips,hide_index=True,width='stretch')
  if coverage.get('sessions'):st.dataframe(pd.DataFrame(coverage['sessions']),hide_index=True,width='stretch')
 with st.expander('Run settings & assumptions'):
  st.json(settings)
  if settings.get('engine')!='dated_futures_v1':
   st.caption(f"Options: one-minute fills, {settings.get('charges','saved-run fee assumptions')}, and archived lot sizes. Stops use minute-close P&L and fill next minute; margin is user-provided.")
