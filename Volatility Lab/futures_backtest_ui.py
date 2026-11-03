from datetime import date
import streamlit as st
import pandas as pd
import backtest_jobs as jobs

def render():
 st.subheader('Futures carry backtest')
 st.caption('Carry = ln(far ÷ near) × 365 ÷ days to expiry × 100. Signals use daily closes; fills use the next session open in the same contracts.')
 with st.form('futures_backtest'):
  a,b,c=st.columns(3);symbol=a.selectbox('Index',['NIFTY','BANKNIFTY']);pair_name=b.selectbox('Spread',['Near - Far','Near - Next','Next - Far']);direction=c.selectbox('Entry legs',['Sell near / buy far','Buy near / sell far'])
  a,b=st.columns(2);start=a.date_input('From',date(2024,10,1));end=b.date_input('To',date.today())
  a,b,c=st.columns(3);entry=a.number_input('Enter carry at or below (%)',value=6.,step=.01);target=b.number_input('Exit carry at or above (%)',value=6.66,step=.01);sizing_mode=c.selectbox('Sizing mode',['Strategy lots (historical contract lot sizes)','Fixed units per leg'])
  a,b,c,d=st.columns(4)
  if sizing_mode.startswith('Strategy lots'):
   lots=a.number_input('Strategy lots',min_value=1,value=1,step=1,help='Automatically scales by dated contract lot size (e.g. NIFTY 25 or 75 units/lot).')
   quantity_units=75
  else:
   lots=None
   quantity_units=a.number_input('Quantity per leg · units',min_value=1,value=75,step=1)
  slip=b.number_input('Slippage bps per fill',0.,100.,1.);hold=c.number_input('Maximum calendar days held',1,180,30);brokerage=d.number_input('Brokerage per order · INR',min_value=0,value=20,step=1,help='Upstox standard is ₹20 per order; enter the fee for your plan.')
  st.caption('Rising carry widens sell-near/buy-far. P&L is calculated from the two leg prices.')
  go=st.form_submit_button('Run futures test',disabled=jobs.pending(st.session_state))
 if go:
  if start>end or target<=entry:st.error('Choose From ≤ To and exit carry above entry carry.');return
  settings=dict(name=pair_name+' carry',symbol=symbol,start=str(start),end=str(end),entry_carry=entry,target_carry=target,lots=int(lots) if lots is not None else None,quantity=int(quantity_units),slippage_bps=slip,max_hold_days=hold,brokerage_per_order=brokerage,charges='Upstox dated estimate',direction=direction,engine='dated_futures_v1')
  st.session_state.pop('_backtest_error',None)
  jobs.submit(st.session_state,'Futures',settings)
 if 'futures_bt_v2' in st.session_state:
  from backtest_report import render as report
  t,s,settings,coverage=st.session_state.futures_bt_v2
  report(t,s,settings,coverage)
