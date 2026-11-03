"""Focused review screen; calculations are separate and testable."""

from datetime import datetime

import json

import numpy as np

import pandas as pd

import plotly.graph_objects as go

import streamlit as st

from market_data import IST

from trade_review import option_book,atm_pair,freshness,parity_forward,gap_summary

from volatility_regime import regime_input

from snapshot_history import load

from smile import has_usable_points,load_smile

from relative_value import history as research_history

from expiry_data import calculation_key
from expiry_forecast import forecast_restriction

from research_extensions import validate_events,upcoming

def table(frame):

    numeric=frame.select_dtypes(include='number').columns

    frame=frame.copy()
    frame[numeric]=frame[numeric].round(2)
    st.dataframe(frame,hide_index=True,width='stretch',column_config={

        c:st.column_config.NumberColumn(c,format='%.2f') for c in numeric})

def render_review(data,symbol,selected,rate,dividend,spread):

    st.markdown('**Volatility pricing**')

    a,b,c=st.columns(3)

    for col,label,key,unit in [(a,'Selected expiry IV','IV','%'),(b,'IV − RV spread','IV-RV Spread',' pp'),

                               (c,'25 Delta reversal','25 Delta reversal',' pp')]:

        value=selected.get(key)

        col.metric(label,f'{value:.2f}{unit}' if value is not None and np.isfinite(value) else '—')

    stamp=pd.Timestamp(selected['As of']).strftime('%d %b %Y · %H:%M:%S IST') if selected.get('As of') else 'Unavailable'

    st.caption(f"{stamp} · {selected.get('Source','Unavailable')}")

    gap=selected.get('IV-RV Spread')

    if gap is not None and np.isfinite(gap):

        relation='above' if gap>0 else 'below' if gap<0 else 'equal to'

        st.caption(f'IV {relation} trailing HV30 · historical comparison, not a trade signal')

    render_risk_context(data,symbol,selected)

def render_risk_context(data,symbol,selected):


    if selected.get('As of'):

        try:

            with st.expander('Overnight and daily moves · trailing year'):

                table(gap_summary(pd.read_csv(regime_input(data,symbol)),selected['As of']))

                st.caption('Completed sessions · absolute moves (%)')

        except (OSError,ValueError,TypeError) as error:st.info(str(error))

def render_intraday(data,symbol,selected,config,latest_only=False):

    saved=load(data/'monitor.sqlite',symbol,selected.get('Expiry'),config)

    if saved.empty:

        st.info('Refresh quotes to begin your own timestamped IV history.');return

    dates=sorted(saved['As of'].dt.date.unique(),reverse=True)

    day=dates[0] if latest_only else st.selectbox('Session',dates)
    if latest_only:st.caption(f'Session: {day:%d %b %Y}')

    view=saved[saved['As of'].dt.date==day]

    options=['IV','IV-RV Spread','25 Delta reversal']

    if 'Straddle midpoint' in view and view['Straddle midpoint'].notna().any():options.append('Straddle midpoint')

    metric=st.radio('Measure',options,horizontal=True)

    if metric=='Straddle midpoint':

        strike=st.selectbox('Fixed straddle strike',sorted(view['ATM strike'].dropna().unique()),format_func=lambda v:f'{v:.2f}')

        # Retain missing periods rather than splice together different contracts.

        view=view.copy();view.loc[view['ATM strike']!=strike,metric]=np.nan

    fig=go.Figure()

    for source,group in view.groupby('Source'):

        fig.add_trace(go.Scatter(x=group['As of'],y=group[metric],name=source,mode='lines+markers',

                                hovertemplate='%{x}<br>%{y:.2f}<extra>%{fullData.name}</extra>'))

    fig.update_layout(height=340,template='plotly_dark',paper_bgcolor='#0c1321',yaxis_tickformat='.2f',

                      yaxis_title=metric+(' %' if metric=='IV' else ' · points' if metric=='Straddle midpoint' else ' · pp'),margin=dict(l=10,r=10,t=25,b=10))

    st.plotly_chart(fig,width='stretch',config={'displaylogo':False})

    st.caption(f'{len(view)} snapshots · captured on quote refresh')

    if metric=='Straddle midpoint':st.caption('Fixed strike · missing snapshots remain blank')

def render_surface(data,symbol,result,config):

    records=[];points=['10P','25P','ATM','25C','10C']

    stamps=set()

    for row in result.get('rows',[]):

        if row['Instrument']!=symbol or not row.get('As of'):continue

        saved=load_smile(data/'monitor.sqlite',symbol,row['Expiry'],config,row['As of'])

        if saved and has_usable_points(saved):

            stamps.add(row['As of'])

            records.append({'Expiry':row['Expiry'],**{p['Point']:p['IV'] for p in saved['points']}})

    if not records:st.info('No usable broker IV points are saved for this expiry set in Google Cloud captures.');return

    if len(stamps)!=1:

        st.info('Surface withheld: expiry snapshots have different valuation times. Refresh quotes.');return

    grid=pd.DataFrame(records).sort_values('Expiry').set_index('Expiry').reindex(columns=points)

    fig=go.Figure(go.Heatmap(z=grid.to_numpy(float),x=points,y=grid.index,texttemplate='%{z:.2f}',

        colorscale='Viridis',hoverongaps=False,hovertemplate='%{y} · %{x}<br>IV %{z:.2f}%<extra></extra>'))

    fig.update_layout(height=max(280,40*len(grid)+90),template='plotly_dark',paper_bgcolor='#0c1321',

                      yaxis_type='category',xaxis_title='Put delta / spot ATM / call delta',margin=dict(l=10,r=10,t=15,b=10))

    st.plotly_chart(fig,width='stretch',config={'displaylogo':False})
    if grid.isna().any().any():st.caption('Gaps mark expiries without broker IV coverage at those deltas.')

    st.caption('IV by delta and expiry · blanks unavailable · '+', '.join(sorted(str(s) for s in sources)))

