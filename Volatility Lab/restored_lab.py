"""Recovered research views; no data fabrication or trading operations."""
import json
import sqlite3
from datetime import datetime
import pandas as pd
import streamlit as st
from market_data import IST
from trade_review_ui import table


def render_archived_intraday(data,symbol,selected,config):
    from trade_review_ui import render_intraday
    st.caption('Selected expiry · latest captured session · minute-price history stays in the calculation backend')
    render_intraday(data,symbol,selected,config,latest_only=True)


def render_research_tools(data,symbol,selected,result,config):
    from monitor_ui import render_relative_value
    render_relative_value(symbol,selected,config)


def render_trade_tools(data,symbol,selected,rate,dividend,spread):
    from trade_review_ui import render_review
    from trade_review import option_book,atm_pair,parity_forward,make_position,position_greeks,stress_position,freshness
    from history_policy import assumptions
    from dividend_yield import for_symbol
    paths=sorted(data.glob(symbol.lower()+'_option_quotes_*.json'),reverse=True)
    if not paths:
        st.info('No saved option books for trade analysis.');return
    preferred=next((i for i,p in enumerate(paths) if p.stem.endswith(str(selected.get('Expiry')))),0)
    path=st.selectbox('Saved option book',paths,index=preferred,format_func=lambda p:p.stem.replace(symbol.lower()+'_option_quotes_',''),key='restored_trade_book')
    try:
        payload=json.loads(path.read_text(encoding='utf-8'))
        settings=assumptions(data/'monitor.sqlite',payload.get('config'))
        if not settings:raise ValueError('Original pricing assumptions unavailable for this book.')
        if settings['rate'] is None or (settings['dividend'].get(symbol) if isinstance(settings['dividend'],dict) else settings['dividend']) is None:raise ValueError('Model scenarios require rate/dividend inputs; broker IV remains available in the other views.')
        expiry=path.stem.replace(symbol.lower()+'_option_quotes_','')
        context=option_book(payload,expiry,settings['rate'],for_symbol(settings['dividend'],symbol),min(3.,settings['max_spread']))
        st.caption('Book captured '+str(context['stamp'])+' · '+freshness(context,datetime.now(IST),payload.get('spot_quote')))
        st.caption('Current IV/Greeks: Upstox · scenario P&L: lab model · bid/ask liquidity checks remain active')
        protected=st.toggle('Add protective wings',value=True,key='stress_wings')
        put=call=None
        if protected:
            book=context['book']
            puts=sorted(book.loc[(book.Kind=='PE')&(book.Strike<context['strike']),'Strike'].unique())
            calls=sorted(book.loc[(book.Kind=='CE')&(book.Strike>context['strike']),'Strike'].unique())
            if not puts or not calls:raise ValueError('No valid protective wing quotes in the saved book.')
            put=st.selectbox('Put wing',puts,index=len(puts)-1)
            call=st.selectbox('Call wing',calls)
        position=make_position(context,put,call)
        days=st.number_input('Elapsed calendar days',min_value=0.,max_value=365.,value=1.,step=.25)
        costs=st.number_input('Additional costs per position (₹)',min_value=0.,value=0.,step=10.)
        scenarios,summary=stress_position(context,position,days,costs)
        table(position.drop(columns=['Bid IV','Ask IV'],errors='ignore'));table(pd.DataFrame([{**summary,**position_greeks(context,position)}]))
        grid=scenarios.pivot(index='Spot move %',columns='IV shock · pp',values='Unhedged P&L · ₹').reset_index()
        grid.columns=[str(c) if c=='Spot move %' else 'IV +'+str(c)+' pp' for c in grid.columns]
        table(grid)
        st.caption('One lot · sticky-strike IV shocks · bid/ask costs · frozen hedge is hypothetical spot units, not futures lots')
        st.download_button('Download stress scenarios',scenarios.to_csv(index=False).encode(),file_name=symbol+'_stress.csv')
    except (OSError,ValueError,TypeError,KeyError) as error:st.info(str(error))
