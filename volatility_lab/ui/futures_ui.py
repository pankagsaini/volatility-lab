"""Futures relative value, separate from options and volatility models."""
from volatility_lab.runtime.settings import data_dir as runtime_data_dir
from volatility_lab import PACKAGE_ROOT

from pathlib import Path

import time

import json

import numpy as np

import pandas as pd

import plotly.graph_objects as go

import streamlit as st

from futures_sync import refresh_all

from futures_data import FuturesMonitor,load_latest,daily_pair,intraday,saved_pairs

from market_data import MarketDataError,history_cutoff

from futures_rolling import fetch_rolling,rolling_frame,carry_context,VERSION

from quote_status import futures_display_pairs

from futures_view_cache import database_revision,rolling_revision

ROOT=runtime_data_dir()/'futures'

COLORS=['#50c5b4','#ffbf69']

@st.cache_data(show_spinner=False,max_entries=8)

def saved_rolling_chart(root,symbol,history_revision):

    """Quote refreshes do not change historical spreads or expiry mapping."""

    return rolling_frame(root,symbol)


@st.cache_data(show_spinner=False,max_entries=16)
def saved_dated_chart(root,symbol,front,back,history_revision):
    return daily_pair(root,symbol,front,back)


@st.cache_data(show_spinner=False,max_entries=16)
def saved_intraday_chart(root,symbol,front,back,history_revision):
    return intraday(root,symbol,front,back)


@st.cache_data(show_spinner=False,max_entries=8)
def saved_expiry_pairs(root,symbol,history_revision):
    return saved_pairs(root,symbol)

def table(frame):

    frame=frame.copy()

    for c in ('Price','Bid','Ask','Spread','Annualized carry %','Buy spread','Sell spread'):

        if c in frame:frame[c]=pd.to_numeric(frame[c],errors='coerce')

    st.dataframe(frame,hide_index=True,width='stretch',column_config={c:st.column_config.NumberColumn(c,format='%.2f') for c in frame.select_dtypes(include='number').columns})

def moving_average(frame,metric,method='EMA',window=20):

    values=pd.to_numeric(frame[metric],errors='coerce').replace([np.inf,-np.inf],np.nan)

    if method=='EMA':return values.ewm(span=int(window),adjust=False,min_periods=1,ignore_na=True).mean().where(values.notna())

    return values.rolling(int(window),min_periods=int(window)).mean()

def trend_controls(key,unit='sessions'):

    with st.popover('Chart settings'):

        return _trend_controls(key,unit)

def _trend_controls(key,unit):

    a,b=st.columns([1,2])

    method=a.selectbox('Moving average',['EMA','SMA'],key=key+'_method',help='EMA gives more weight to recent observations; SMA weights the window equally.')

    if st.session_state.get(key+'_window',20)>60:st.session_state[key+'_window']=60

    window=b.slider('Average length ('+unit+')',min_value=5,max_value=60,value=None if key+'_window' in st.session_state else 20,step=1,key=key+'_window')

    st.caption(f'{method} {window} {unit}')

    if method=='SMA':st.caption('Requires a full window of observations.')

    return method,window

def fit_visible_axis(fig,extras=()):

    values=list(extras)

    for trace in fig.data:

        if trace.y is not None:values.extend(pd.to_numeric(pd.Series(trace.y),errors='coerce').dropna().tolist())

    finite=np.asarray(values,float);finite=finite[np.isfinite(finite)]

    if len(finite):

        lo,hi=float(finite.min()),float(finite.max());pad=max((hi-lo)*.08,abs(hi)*.001,.01)

        # Plotly nticks is only a maximum and can choose very sparse labels.
        # Set a rounded interval explicitly for roughly six visible grid lines.
        lower,upper=lo-pad,hi+pad
        raw=(upper-lower)/6
        magnitude=10**np.floor(np.log10(raw))
        step=min((1,2,2.5,5,10),key=lambda n:abs(n-raw/magnitude))*magnitude
        step=max(float(step),.01)
        fig.update_yaxes(range=[lower,upper],tickmode='linear',tick0=0,dtick=step,
                         tickformat='.2f',zeroline=False)

def figure(series,metric,period,anchor,method='EMA',window=20):

    fig=go.Figure()

    start=pd.Timestamp(anchor)-pd.DateOffset(months={'1M':1,'3M':3,'6M':6,'1Y':12,'2Y':24,'3Y':36}[period])

    for i,(label,frame) in enumerate(series):

        if frame.empty:continue

        full=frame.sort_values('Date').copy()

        full['Trend']=moving_average(full,metric,method,window)

        view=full[(full.Date>=start)&(full.Date<=pd.Timestamp(anchor))]

        fig.add_trace(go.Scatter(x=view.Date,y=view[metric],name=label,mode='lines',connectgaps=False,

            line={'color':COLORS[i%len(COLORS)],'width':2},hovertemplate='%{x|%d %b %Y}<br>%{y:.2f}<extra>%{fullData.name}</extra>'))

        fig.add_trace(go.Scatter(x=view.Date,y=view.Trend,name=f'{method} {window}',mode='lines',connectgaps=False,

            line={'color':'#ffbf69','width':2},hovertemplate='%{x|%d %b %Y}<br>%{y:.2f}<extra>%{fullData.name}</extra>'))

    short=period in ('1M','3M')

    tick={'1M':7*86400000,'3M':14*86400000,'6M':'M1','1Y':'M2','2Y':'M3','3Y':'M6'}[period]

    fig.update_layout(height=420,template='plotly_dark',paper_bgcolor='#0c1321',plot_bgcolor='#0c1321',

        margin=dict(l=10,r=10,t=65,b=55),hovermode='x unified',legend={'orientation':'h','y':1.08,'yanchor':'bottom','x':0},

        yaxis={'title':'Spread · index points' if metric=='Spread' else 'Implied net carry · % annualized'},

        xaxis={'title':'Observation date','range':[start,pd.Timestamp(anchor)],'tickformat':'%d %b' if short else '%b %Y','dtick':tick})

    fit_visible_axis(fig)

    return fig

def add_latest_observation(fig,frame,current,metric):
    """Connect a newer quote visually without rewriting closes or their average."""
    value=current.get(metric)
    if frame.empty or value is None or not np.isfinite(value) or not current.get('As of'):return
    stamp=pd.Timestamp(current['As of'])
    if stamp.tzinfo is not None:stamp=stamp.tz_convert('Asia/Kolkata').tz_localize(None)
    last=frame.sort_values('Date').iloc[-1]
    if stamp.normalize()<=pd.Timestamp(last.Date).normalize():return
    fig.add_trace(go.Scatter(x=[last.Date,stamp],y=[last[metric],value],mode='lines+markers',
        name='Latest saved observation',line={'color':COLORS[0],'width':2,'dash':'dot'},
        marker={'size':[0,7]},customdata=['Daily close',str(current.get('Source','Saved quote'))],
        hovertemplate='%{x|%d %b %Y %H:%M}<br>%{y:.2f}<br>%{customdata}<extra></extra>'))
    start=fig.layout.xaxis.range[0]
    fig.update_xaxes(range=[start,stamp+pd.Timedelta(hours=12)])
    fit_visible_axis(fig)


def reference_caption(frame):

    if frame.empty:return

    spread=pd.to_numeric(frame['Spread'],errors='coerce').dropna()

    carry=pd.to_numeric(frame['Annualized carry %'],errors='coerce').dropna()

    st.caption(f"Selected range · average spread {spread.mean():.2f} points · average carry {carry.mean():.2f}% · {len(spread)} daily observations")

def render_rolling(symbol,result,engine,client,ready):

    path=ROOT/f'{symbol}_rolling_audit.json'

    audit=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

    if audit.get('status')!='verified' or audit.get('version')!=VERSION:

        st.info('Connect Upstox and update saved history.')

        if audit.get('error'):st.warning(audit['error'])

        return

    frame=saved_rolling_chart(ROOT,symbol,(VERSION,rolling_revision(ROOT,symbol)))

    if frame.empty:st.info('No matched rolling futures candles.');return

    if audit.get('last_refresh_error'):st.warning('Latest history refresh failed; displaying the previous verified archive. '+audit['last_refresh_error'])

    st.caption(f"Daily closes · Upstox + retained history · {frame.Date.min():%d %b %Y}–{frame.Date.max():%d %b %Y} · loaded {pd.Timestamp(audit['fetched']):%d %b %Y}")

    references={}

    for label in ['Current - Next','Next - Far']:

        quoted=next((p for p in result['pairs'] if p['Pair']==label and p.get('Annualized carry %') is not None),None)

        last=frame[frame.Pair==label].sort_values('Date').iloc[-1]

        # Use the newer reference, explicitly retaining whether it is quoted or a daily close.

        close_time=last.Date.tz_localize('Asia/Kolkata')+pd.Timedelta(hours=15,minutes=30)
        quote_time=pd.Timestamp(quoted['As of']) if quoted else None
        if quote_time is not None and quote_time.tzinfo is None:quote_time=quote_time.tz_localize('Asia/Kolkata')
        if quoted and quote_time>=close_time:

            current=quoted;basis=quoted['Source']

        else:current={**last.to_dict(),'As of':close_time.isoformat()};basis='Latest rolling daily close'

        references[label]=current

    range_col,settings_col=st.columns([4,1])
    period=range_col.segmented_control('Range',['1M','3M','6M','1Y','2Y','3Y'],default='3M',label_visibility='collapsed',key='rolling_range_v2') or '3M'
    with settings_col.popover('Chart settings',width='stretch'):
        metric=st.radio('Measure',['Spread','Annualized carry %'],horizontal=True,key='rolling_measure_v2')
        method,window=_trend_controls('rolling_trend','sessions')

    anchor=max([frame.Date.max()]+[pd.Timestamp(c['As of']).tz_localize(None).normalize() for c in references.values()])

    start=anchor-pd.DateOffset(months={'1M':1,'3M':3,'6M':6,'1Y':12,'2Y':24,'3Y':36}[period])

    series=[];rolls=[]

    for label,f in frame.groupby('Pair',sort=False):

        f=f.sort_values('Date').copy();f['Roll']=f['Front expiry'].ne(f['Front expiry'].shift())|f['Back expiry'].ne(f['Back expiry'].shift())

        f.loc[f.index[0],'Roll']=False

        rolls.append((label,f[f.Roll&(f.Date>=start)]))

        series.append((label,f))

    include_far=True

    for i,((label,view),(_,points)) in enumerate(zip(series,rolls)):

        if label=='Next - Far' and not include_far:continue

        st.markdown(f'**{label}**')

        actual=frame[(frame.Pair==label)&(frame.Date>=start)]

        reference_caption(actual)

        fig=figure([(label,view)],metric,period,anchor,method,window)

        add_latest_observation(fig,view,references[label],metric)

        reference=references[label].get(metric)

        if reference is not None and np.isfinite(reference):


            reference_date=pd.Timestamp(references[label]['As of']).tz_localize(None)

            # Show the latest quote explicitly without treating it as a completed daily candle.

            fig.add_trace(go.Scatter(x=[reference_date],y=[reference],mode='markers',

                name='Latest reference',marker={'color':'#ffbf69','size':9,'symbol':'circle'},

                hovertemplate='%{x|%d %b %Y %H:%M}<br>%{y:.2f}<extra>Latest reference</extra>'))

            fig.update_xaxes(range=[start,max(anchor,reference_date)+pd.Timedelta(hours=12)])

            fit_visible_axis(fig,[reference])

            st.caption(f"Latest reference {reference:.2f}{'%' if metric=='Annualized carry %' else ' points'} · {pd.Timestamp(references[label]['As of']):%d %b %Y %H:%M}")

        fig.add_trace(go.Scatter(x=points.Date,y=points[metric],name='Roll / expiry revision',mode='markers',

            marker={'symbol':'diamond','size':7,'color':COLORS[0]},customdata=points[['Front expiry','Back expiry']],

            hovertemplate='Roll / expiry revision · %{x}<br>%{customdata[0]} / %{customdata[1]}<br>%{y:.2f}<extra></extra>'))

        fig.update_layout(uirevision=f'rolling-{symbol}-{label}-{metric}-{period}-{method}-{window}')
        st.plotly_chart(fig,key=f'rolling-{symbol}-{label}',width='stretch',config={'displaylogo':False})

    with st.popover('Export history'):
        st.download_button('Rolling carry CSV',frame.to_csv(index=False,float_format='%.2f').encode(),file_name=f'{symbol}_rolling_carry_3y.csv',mime='text/csv')

    if st.session_state.get('advanced_workspace'):
        with st.expander('Historical carry comparison · research'):
            table(pd.DataFrame([{'Pair':label,**carry_context(frame[frame.Pair==label],current)} for label,current in references.items()]))
            st.caption('Matched maturity context; unavailable ranks require more comparable observations.')

    with st.expander('Data checks'):

        table(pd.DataFrame(audit.get('overlap_checks',[])))

        st.caption('Rolling vs dated contract closes · unadjusted spreads')

def render():

    st.subheader('Futures carry')

    client=st.session_state.get('data_client');ready=bool(client and client.authenticated)

    top_a,top_b=st.columns([3,1])

    symbol=top_a.selectbox('Futures instrument',['NIFTY','BANKNIFTY'],key='futures_symbol')

    auto=top_b.toggle('Auto refresh futures',value=False,disabled=not ready,help='Refresh both indices every 60 seconds while this view is open.')

    if not isinstance(st.session_state.get('futures_engine'),FuturesMonitor):st.session_state.futures_engine=FuturesMonitor()

    engine=st.session_state.futures_engine

    @st.fragment

    def panel():

        import background_refresh as background
        completed=background.take(st.session_state,'futures')
        if completed:
            errors=completed[1] if completed[0] else [completed[1]]
            st.session_state.futures_sync_errors=errors
            st.toast('Futures updated' if not errors else 'Futures refresh finished with data gaps')
        busy=background.pending(st.session_state,'futures')
        refresh_col,history_col=st.columns([1,5])

        pressed=refresh_col.button('Refresh futures',disabled=not ready or busy,type='secondary',width='stretch')

        with history_col.popover('Data tools'):
            history_pressed=st.button('Update saved history',disabled=not ready or busy,width='stretch',help='Fetch only missing historical candles for both indices.')

        cutoff=str(history_cutoff())

        last=st.session_state.get('futures_sync_attempt',0)

        retry=bool(st.session_state.get('futures_sync_errors')) and time.monotonic()-last>=60

        due=st.session_state.get('futures_sync_cutoff')!=cutoff

        if ready and not busy and (pressed or history_pressed or not last or due or retry or (auto and time.monotonic()-last>55)):

            st.session_state['futures_sync_attempt']=time.monotonic()

            st.session_state['futures_sync_cutoff']=cutoff

            background.submit(st.session_state,'futures',refresh_all,engine,background.frozen_client(client),ROOT,
                history_only=history_pressed or (bool(last) and not pressed and not auto))
        def refresh_in_background():
            if not ready or background.pending(st.session_state,'futures'):return
            last_attempt=st.session_state.get('futures_sync_attempt',0)
            cutoff_now=str(history_cutoff())
            due_now=st.session_state.get('futures_sync_cutoff')!=cutoff_now
            if time.monotonic()-last_attempt<60 or not (auto or due_now):return
            st.session_state.futures_sync_attempt=time.monotonic()
            st.session_state.futures_sync_cutoff=cutoff_now
            background.submit(st.session_state,'futures',refresh_all,engine,background.frozen_client(client),ROOT,history_only=not auto)
        background.status('futures',refresh_in_background)

        for error in st.session_state.get('futures_sync_errors',[]):st.warning(error)

        result=load_latest(ROOT,symbol)

        if result is None:

            st.info('Connect Upstox and refresh futures to load the three contracts.');return

        st.caption(f"Collected {pd.Timestamp(result['collected']):%d %b %Y · %H:%M IST} · {'Upstox connected' if ready else 'Saved view'}")

        legs=result['legs'];pairs=futures_display_pairs(result,ready)

        result={**result,'pairs':pairs}

        pair_table=pd.DataFrame(pairs)

        pair_table['Quote time']=[pd.Timestamp(p['As of']).strftime('%d %b %H:%M IST') if p.get('As of') else 'Unavailable' for p in pairs]

        table(pair_table[['Pair','Front expiry','Back expiry','Spread','Annualized carry %','Buy spread','Sell spread','Quote time','Status']])

        with st.expander('Individual contract quotes & specifications'):
            table(pd.DataFrame([{'Month':name,'Expiry':leg['expiry'],'Contract':leg['contract'],'Price':leg['quote']['Price'],
                'Bid':leg['quote']['Bid'],'Ask':leg['quote']['Ask'],'Lot size':leg['lot_size'],'Source':leg['quote']['Source'],'Status':leg['quote']['Status']}
                for name,leg in zip(['Current','Next','Far'],legs)]))

        st.caption('Spread = earlier expiry − later expiry · bid/ask prices exclude costs.')

        for error in result.get('history_errors',[]):st.warning(error)
        if result.get('history_pending'):st.info('Today’s broker daily candles are pending. Saved history and separately dated quotes remain visible; collection will retry.')

        views=['Rolling carry','Daily history']+(['Intraday observations'] if st.session_state.get('advanced_workspace') else [])
        mode=st.segmented_control('Futures chart',views,default='Rolling carry',label_visibility='collapsed') or 'Rolling carry'

        if mode=='Rolling carry':

            render_rolling(symbol,result,engine,client,ready)

            return

        selected=[(p['Pair'],p['Front expiry'],p['Back expiry']) for p in pairs]
        history_revision=database_revision(ROOT)
        with st.expander('Archived contracts'):
            archived=saved_expiry_pairs(ROOT,symbol,history_revision)
            use_archive=st.checkbox('Use a saved expiry pair',key='futures_use_archive')
            if use_archive and archived:
                front,back=st.selectbox('Expiry pair',archived,format_func=lambda p:p[0]+' / '+p[1],key='futures_archive_pair')
                selected=[('Selected expiry pair',front,back)]


        with st.popover('Chart settings'):
            metric=st.radio('Futures measure',['Spread','Annualized carry %'],horizontal=True)
            method,window=_trend_controls('dated_trend','sessions')

        minute_mode=mode=='Intraday observations' and st.radio('Intraday source',['One-minute history','Captured quotes'],horizontal=True,key='futures_intraday_source')=='One-minute history'
        series=[]

        for name,front,back in selected:

            if minute_mode:
                from upstox_research import futures_minute_pair
                frame=futures_minute_pair(symbol,front,back)
            else:frame=(saved_dated_chart(ROOT,symbol,front,back,history_revision) if mode=='Daily history'
                        else saved_intraday_chart(ROOT,symbol,front,back,history_revision))

            series.append((f'{name} · {front} / {back}',frame))

        populated=[f for _,f in series if not f.empty]

        if not populated:

            st.info('No matched observations for these dated contracts yet. Refresh futures with Upstox connected.');return

        if mode=='Daily history':

            period=st.segmented_control('Futures range',['1M','3M','6M','1Y'],default='3M',label_visibility='collapsed') or '3M'

            anchor=max(f.Date.max() for f in populated)

            start=anchor-pd.DateOffset(months={'1M':1,'3M':3,'6M':6,'1Y':12}[period])

            for label,frame in series:

                st.markdown(f'**{label}**')

                if frame.empty:st.info('No matched history for this pair.');continue

                view=frame[frame.Date>=start]

                reference_caption(view)

                fig=figure([(label,frame)],metric,period,anchor,method,window)
                current=next((p for p in pairs if p['Pair']==label),{})
                add_latest_observation(fig,frame,current,metric)

                fig.update_layout(uirevision=f'daily-{symbol}-{label}-{metric}-{period}-{method}-{window}')
                st.plotly_chart(fig,key=f'daily-{symbol}-{label}',width='stretch',config={'displaylogo':False})

            st.caption(' · '.join(f'{name}: {len(f)} sessions' for name,f in series))

        else:

            days=sorted({d for f in populated for d in f.Date.dt.date},reverse=True)

            day=st.selectbox('Futures session',days)

            method,window=trend_controls('intraday_trend','observations')

            for i,(name,frame) in enumerate(series):

                if frame.empty:continue

                st.markdown(f'**{name}**')

                fig=go.Figure()

                view=frame[frame.Date.dt.date==day]

                for source,group in view.groupby('Source'):

                    fig.add_trace(go.Scatter(x=group.Date,y=group[metric],name=name+' · '+source,mode='lines+markers',connectgaps=False,

                        line={'color':COLORS[0]},hovertemplate='%{x}<br>%{y:.2f}<extra>%{fullData.name}</extra>'))

                for source,group in view.groupby('Source'):

                    group=group.sort_values('Date')

                    fig.add_trace(go.Scatter(x=group.Date,y=moving_average(group,metric,method,window),name=f'{method} {window} · {source}',mode='lines',line={'color':'#ffbf69'}))

                fit_visible_axis(fig)

                if not view.empty:st.caption(f"Session average spread {view.Spread.mean():.2f} points · {len(view)} captured observations; snapshot-weighted")

                fig.update_layout(height=400,template='plotly_dark',paper_bgcolor='#0c1321',yaxis={'tickformat':'.2f','title':metric,'rangemode':'normal'},legend={'orientation':'h'})

                fig.update_layout(uirevision=f'intraday-{symbol}-{name}-{metric}-{day}-{method}-{window}-{minute_mode}')
                st.plotly_chart(fig,key=f'intraday-{symbol}-{name}',width='stretch',config={'displaylogo':False})

        export=pd.concat([f.assign(Pair=name) for name,f in series if not f.empty],ignore_index=True)

        st.download_button('Download futures history',export.to_csv(index=False,float_format='%.2f').encode(),file_name=f'{symbol}_futures_spreads.csv',mime='text/csv')

    panel()

