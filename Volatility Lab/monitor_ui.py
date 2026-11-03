"""Compact options, expiry-specific history, and volatility model views."""

from datetime import datetime,timedelta

from pathlib import Path

import json

import time

import numpy as np

import pandas as pd

import plotly.graph_objects as go

from plotly.subplots import make_subplots

import streamlit as st

from market_data import IST,MarketDataError,history_cutoff

from market_table import INDICES,COLUMNS,DATA,one_year,historical_vol,chart_iv_spread

from expiry_data import ExpiryMonitor,calculation_key,load_snapshot,history,align_snapshot

from term_structure import term_structure

from smile import has_usable_points,load_smile,load_latest_smile

from quote_status import snapshot_status

from expiry_forecast import expiry_forecast, VERSION as EXPIRY_VERSION,term_structure_forecast,TERM_VERSION

def render_trade_decision_radar(selected_rows):
    """Executive trade decision panel with regime, spread bias, expected move, and basis."""
    if not selected_rows:return
    st.markdown('<div style="margin-top:2px; margin-bottom:10px;"><span style="font-size:0.76rem; font-weight:700; letter-spacing:0.12em; text-transform:uppercase; color:#06b6d4;">Tactical Volatility & Trade Decision Radar</span></div>', unsafe_allow_html=True)
    cols=st.columns(len(selected_rows))
    for col,row in zip(cols,selected_rows):
        symbol=row.get('Instrument','—')
        exp=row.get('Expiry','—')
        spot=row.get('Price')
        iv=row.get('IV')
        spread=row.get('IV-RV Spread')
        syn=row.get('Synthetic future')
        m_fut=row.get('Month future LTP')
        rev25=row.get('25 Delta reversal')

        with col:
            if not spot or not np.isfinite(spot) or not iv or not np.isfinite(iv):
                st.info(f"{symbol} ({exp}): Live quotes pending.")
                continue

            try:
                exp_date=pd.Timestamp(exp).date()
                today=datetime.now(IST).date()
                dte=max(0.2,float((exp_date-today).days))
            except Exception:
                dte=5.0
            move_pts=spot*(iv/100.0)*np.sqrt(dte/365.0)
            move_pct=(move_pts/spot)*100.0
            lower_range=spot-move_pts
            upper_range=spot+move_pts

            if pd.notna(spread) and np.isfinite(spread):
                if spread>1.5:
                    bias_badge='RICH VOLATILITY'
                    bias_color='#10b981'
                    bias_bg='rgba(16, 185, 129, 0.12)'
                    bias_border='#10b981'
                    bias_text='⚡ Premium Selling Favorable (Credit Spreads, Iron Condors, Straddles). IV commands an attractive premium over trailing realized movement.'
                elif spread<-1.5:
                    bias_badge='COMPRESSED VOLATILITY'
                    bias_color='#06b6d4'
                    bias_bg='rgba(6, 182, 212, 0.12)'
                    bias_border='#06b6d4'
                    bias_text='🎯 Long Gamma / Debit Spreads Favorable (Long Calendars, Straddles). Market pricing is discounted relative to historical movement.'
                else:
                    bias_badge='FAIR VALUE SPREAD'
                    bias_color='#94a3b8'
                    bias_bg='rgba(148, 163, 184, 0.12)'
                    bias_border='#475569'
                    bias_text='⚖️ Directional / Skew Neutral. IV tracks realized volatility closely within statistical bounds.'
                spread_display=f'{spread:+.2f} pp'
            else:
                bias_badge='SPREAD PENDING'
                bias_color='#f59e0b'
                bias_bg='rgba(245, 158, 11, 0.12)'
                bias_border='#b45309'
                bias_text='Historical daily prices updating for RV alignment.'
                spread_display='—'

            if pd.notna(syn) and pd.notna(m_fut) and np.isfinite(syn) and np.isfinite(m_fut):
                basis=syn-m_fut
                basis_threshold=30.0 if symbol=='NIFTY' else 75.0
                if abs(basis)<=basis_threshold:
                    basis_status=f'● Parity Normal ({basis:+.1f} pts)'
                    basis_color='#10b981'
                else:
                    basis_status=f'⚠️ Basis Dislocation ({basis:+.1f} pts)'
                    basis_color='#f59e0b'
            else:
                basis_status='Parity: Reference'
                basis_color='#94a3b8'

            skew_text=f'Put Skew: {abs(rev25):.2f} pp' if pd.notna(rev25) and np.isfinite(rev25) else 'Skew: —'

            st.markdown(f'''
            <div style="background:#111927; border:1px solid #1f2d42; border-radius:12px; padding:14px 16px; margin-bottom:12px; box-shadow:0 4px 16px rgba(0,0,0,0.25);">
                <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #1b2637; padding-bottom:8px; margin-bottom:10px;">
                    <div>
                        <span style="font-weight:700; font-size:1.05rem; color:#f8fafc;">{symbol}</span>
                        <span style="color:#64748b; font-size:0.8rem; margin-left:6px;">{exp} ({dte:.1f}d)</span>
                    </div>
                    <span style="background:{bias_bg}; color:{bias_color}; border:1px solid {bias_border}; font-size:0.7rem; font-weight:700; padding:2px 8px; border-radius:6px; letter-spacing:0.04em;">
                        {bias_badge}
                    </span>
                </div>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:10px;">
                    <div style="background:#0c1320; border:1px solid #1a2538; border-radius:8px; padding:8px 12px;">
                        <div style="color:#64748b; font-size:0.7rem; text-transform:uppercase; letter-spacing:0.05em;">Spot & ATM IV</div>
                        <div style="font-size:1.15rem; font-weight:700; color:#f8fafc; font-variant-numeric:tabular-nums;">{spot:,.1f}</div>
                        <div style="color:#38bdf8; font-size:0.75rem; font-weight:600;">IV: {iv:.2f}% <span style="color:#64748b;">(Sprd: {spread_display})</span></div>
                    </div>
                    <div style="background:#0c1320; border:1px solid #1a2538; border-radius:8px; padding:8px 12px;">
                        <div style="color:#64748b; font-size:0.7rem; text-transform:uppercase; letter-spacing:0.05em;">Expected Expiry Move</div>
                        <div style="font-size:1.15rem; font-weight:700; color:#f8fafc; font-variant-numeric:tabular-nums;">±{move_pts:.0f} pts</div>
                        <div style="color:#a855f7; font-size:0.75rem; font-weight:600;">±{move_pct:.2f}% <span style="color:#64748b;">({lower_range:,.0f}–{upper_range:,.0f})</span></div>
                    </div>
                </div>
                <div style="font-size:0.78rem; line-height:1.45; color:#cbd5e1; background:#0e1726; border-left:3px solid {bias_color}; border-radius:4px; padding:6px 10px; margin-bottom:8px;">
                    {bias_text}
                </div>
                <div style="display:flex; justify-content:space-between; font-size:0.72rem; color:#64748b;">
                    <span style="color:{basis_color}; font-weight:600;">{basis_status}</span>
                    <span>{skew_text}</span>
                </div>
            </div>
            ''', unsafe_allow_html=True)

def show_table(frame):

    numeric=frame.select_dtypes(include='number').columns

    st.dataframe(frame.assign(**{c:frame[c].round(2) for c in numeric}),hide_index=True,width='stretch',column_config={c:st.column_config.NumberColumn(c,format='%.2f' if 'sessions' not in c.lower() else '%d') for c in numeric})

def _price_history_revision(symbol):
    paths=list((DATA/'underlying_history').glob(symbol.lower()+'_*.csv'))
    paths.extend([DATA/f'{symbol.lower()}_one_year.csv',DATA/'upstox'/'research.sqlite'])
    if symbol=='NIFTY':paths.append(DATA/'nifty_daily.csv')
    archive=DATA/'upstox'/'research.sqlite'
    # SQLite updates the shared-memory sidecar during ordinary reads; use the
    # database and WAL timestamps instead so the cache can actually be reused.
    paths.append(Path(str(archive)+'-wal'))
    revision=[]
    for path in sorted(set(paths),key=str):
        try:
            stat=path.stat();revision.append((str(path),stat.st_mtime_ns,stat.st_size))
        except OSError:continue
    return tuple(revision)

def _load_full_price_history(symbol):
    """Merge valid saved closes; a delayed provider cannot erase a newer session."""
    from vol_lib import validate_ohlc
    import sqlite3
    frames=[]
    for path in reversed(sorted((DATA/'underlying_history').glob(symbol.lower()+'_*.csv'))):
        try:
            candidate=validate_ohlc(pd.read_csv(path)).reset_index()
            frames.insert(0,candidate)
            if len(candidate)>=300:break
        except (OSError,ValueError,KeyError):continue
    for path in [DATA/f'{symbol.lower()}_one_year.csv']+([DATA/'nifty_daily.csv'] if symbol=='NIFTY' else []):
        try:frames.append(validate_ohlc(pd.read_csv(path)).reset_index())
        except (OSError,ValueError,KeyError):continue
    archive=DATA/'upstox'/'research.sqlite'
    if archive.exists():
        try:
            from upstox_data import INDICES as keys
            from contextlib import closing
            with closing(sqlite3.connect('file:'+archive.as_posix()+'?mode=ro',uri=True,timeout=5)) as con:
                recent=pd.read_sql_query("SELECT stamp AS Date,open AS Open,high AS High,low AS Low,close AS Close FROM candles WHERE instrument=? AND interval='day' ORDER BY stamp",con,params=(keys[symbol],))
            if not recent.empty:
                recent['Date']=pd.to_datetime(recent.Date,utc=True).dt.tz_convert(IST).dt.tz_localize(None).dt.normalize()
                frames.append(validate_ohlc(recent).reset_index())
        except (OSError,ValueError,KeyError,sqlite3.Error):pass
    if not frames:raise ValueError('No valid saved daily candles are available for '+symbol)
    merged=pd.concat(frames,ignore_index=True).drop_duplicates('Date',keep='last').sort_values('Date')
    merged=merged[merged.Date<=pd.Timestamp(history_cutoff())]
    return validate_ohlc(merged).reset_index()

@st.cache_data(show_spinner=False,max_entries=4)
def _cached_full_price_history(symbol,revision):
    return _load_full_price_history(symbol)

def full_price_history(symbol):
    """Cache merged history until one of its source files changes."""
    return _cached_full_price_history(symbol,(str(history_cutoff()),_price_history_revision(symbol)))


def load_prices(symbol):
    return one_year(full_price_history(symbol),history_cutoff())


def chart_hv(symbol,frame):
    """Compute rolling HV before restricting the chart, retaining warm-up candles."""
    try:return historical_vol(full_price_history(symbol))
    except (OSError,ValueError,KeyError):
        return historical_vol(frame) if frame is not None else None


@st.cache_data(show_spinner=False,max_entries=4)
def _cached_chart_prices(symbol,revision):
    full=_cached_full_price_history(symbol,revision)
    return one_year(full,revision[0]),historical_vol(full)


def chart_prices(symbol):
    """Share one versioned daily-price/HV calculation between both history charts."""
    return _cached_chart_prices(symbol,(str(history_cutoff()),_price_history_revision(symbol)))

def history_figure(symbol,expiry,hv,iv,period,relative=False,spread_only=False):

    end=pd.Timestamp(datetime.now(IST).date())+pd.Timedelta(hours=12)

    start=end.normalize()-pd.DateOffset(months={'1M':1,'3M':3,'6M':6,'1Y':12}[period])

    if hv is not None:hv=hv[(hv.Date>=start)&(hv.Date<=end)].copy()

    if len(iv):iv=iv[(iv.Date>=start)&(iv.Date<=end)].copy()

    fig=make_subplots(rows=1,cols=1)

    if hv is not None and not spread_only:

        for column,name,color in [('Historical vol 10D','HV 10D','#50c5b4'),('Historical vol 30D','HV 30D','#939cec')]:

            fig.add_trace(go.Scatter(x=hv.Date,y=hv[column],name=name,mode='lines',line={'color':color,'width':1.8},hovertemplate=name+': %{y:.2f}%<extra></extra>'),row=1,col=1)

    # Underlying candles identify actual sessions, including special sessions.
    # Weekends and holidays without candles do not create missing-data gaps.
    sessions=pd.DatetimeIndex(hv.Date).normalize() if hv is not None else pd.DatetimeIndex([])
    if len(iv):
        iv=iv.sort_values('Date',kind='stable').copy()
        sessions=sessions.union(pd.DatetimeIndex(iv.Date).normalize()).unique().sort_values()

    spread_name='Relative spread · 5-session %' if relative else 'IV-RV Spread'

    series=[(spread_name,'#f17e91',1,'%' if relative else ' pp')] if spread_only else [('IV','#f4bc64',1,'%')]
    for name,color,row,unit in series:
        observed=iv.copy()
        if len(observed):
            observed[name]=pd.to_numeric(observed[name],errors='coerce')
            observed=observed[np.isfinite(observed[name])]
        paths={'solid':([],[]),'dot':([],[])}
        for i in range(1,len(observed)):
            left,right=observed.iloc[i-1],observed.iloc[i]
            # Different IV methods are not a continuous market move.
            if ('Upstox supplied IV' in str(left.get('Source',''))) != ('Upstox supplied IV' in str(right.get('Source',''))):
                continue
            first,last=pd.Timestamp(left.Date).normalize(),pd.Timestamp(right.Date).normalize()
            missing=set(sessions[(sessions>first)&(sessions<last)])
            # Split only the drawn connector at midnight; no synthetic observations.
            left_time,right_time=pd.Timestamp(left.Date),pd.Timestamp(right.Date)
            boundaries=[left_time]+list(pd.date_range(first+pd.Timedelta(days=1),right_time,freq='D'))
            if boundaries[-1]!=right_time:boundaries.append(right_time)
            duration=(right_time-left_time).total_seconds()
            for begin,finish in zip(boundaries,boundaries[1:]):
                dash='dot' if begin.normalize() in missing else 'solid'
                xs,ys=paths[dash]
                def visual_y(t):
                    weight=(t-left_time).total_seconds()/duration if duration else 1.
                    return left[name]+weight*(right[name]-left[name])
                xs.extend([begin,finish,None])
                ys.extend([visual_y(begin),visual_y(finish),None])
        for dash,(xs,ys) in paths.items():
            fig.add_trace(go.Scatter(x=xs,y=ys,name=name,legendgroup=name,showlegend=False,
                mode='lines',line={'color':color,'width':2.3,'dash':dash},
                connectgaps=False,hoverinfo='skip'),row=row,col=1)
        metadata=observed.get('Source',pd.Series('',index=observed.index)).fillna('').astype(str)
        symbols=['circle']*len(observed)
        if 'Series' in observed:
            symbols=['diamond' if i and observed.Series.iloc[i]!=observed.Series.iloc[i-1] else 'circle' for i in range(len(observed))]
        # Points are captured observations; connector lines never populate missing values.
        fig.add_trace(go.Scatter(customdata=metadata,x=observed.Date if len(observed) else [],
            y=observed[name] if len(observed) else [],name=name,legendgroup=name,
            mode='markers',marker={'size':6,'color':color,'symbol':symbols},
            hovertemplate=name+': %{y:.2f}'+unit+'<br>%{customdata}<extra></extra>'),row=row,col=1)

    fig.update_layout(height=470,template='plotly_dark',paper_bgcolor='#0c1321',plot_bgcolor='#0c1321',hovermode='x unified',margin={'l':10,'r':15,'t':45,'b':15},legend={'orientation':'h','y':1.12,'x':0},font={'family':'Arial','size':12,'color':'#bccbdf'})

    fig.update_yaxes(tickformat='.2f',gridcolor='#202c40',zeroline=False)

    fig.update_yaxes(title_text=('Relative spread %' if relative else 'IV–RV Spread · pp') if spread_only else 'Volatility %',row=1,col=1,zeroline=spread_only,zerolinecolor='#46566e')

    # Fit each panel to this date window, not the full-year extremes.

    # A minimum margin also makes constant / single-point series readable.

    for axis,row in [('y',1)]:

        values=[]

        for trace in fig.data:

            if (trace.yaxis or 'y')==axis and trace.y is not None:

                values.extend(pd.to_numeric(pd.Series(trace.y),errors='coerce').tolist())

        finite=np.asarray(values,dtype=float)

        finite=finite[np.isfinite(finite)]

        if len(finite):

            low,high=float(finite.min()),float(finite.max())

            if not spread_only and len(finite) >= 4:
                p95 = float(np.percentile(finite, 95))
                if high > p95 * 1.8 and high > 35.0:
                    high = min(high, p95 * 1.4)

            padding=max((high-low)*.12,.10,abs(high)*.02 if high==low else 0.)

            lower=low-padding if spread_only else max(0.,low-padding)

            fig.update_yaxes(range=[lower,high+padding],autorange=False,row=row,col=1)

        else:fig.update_yaxes(autorange=True,row=row,col=1)

    fig.add_annotation(text='Dotted: missing observations · expiry changes retain recorded points',xref='paper',yref='paper',x=0,y=-.20,showarrow=False,font={'size':10})

    fig.update_layout(margin={'b':55})

    fig.update_layout(uirevision=f'{symbol}:{expiry}:{period}:{relative}:{spread_only}')

    fig.update_xaxes(type='date',range=[start,end],showgrid=False, tickformat='%d %b' if period in ('1M','3M') else '%b %Y', dtick={'1M':7*86400000,'3M':14*86400000,'6M':'M1','1Y':'M2'}[period])

    return fig

@st.cache_data(show_spinner=False)
def analyze_models(raw):
    frame=one_year(pd.read_csv(__import__('io').BytesIO(raw)),history_cutoff())
    from model_view import model_tables
    return model_tables(frame)

@st.cache_data(show_spinner=False)
def analyze_candidates(raw):
    frame=one_year(pd.read_csv(__import__('io').BytesIO(raw)),history_cutoff())
    from model_view import fit_candidates
    return fit_candidates(frame)

def render_validation(symbol):

    with st.expander('Walk-forward validation'):

        for manifest in sorted((DATA/'validation').glob('*/metadata.json'),reverse=True):

            try:

                metadata=json.loads(manifest.read_text(encoding='utf-8'))

                if metadata.get('status')!='complete' or metadata.get('symbol')!=symbol:continue

                metrics=pd.read_csv(manifest.parent/'metrics.csv')

            except (OSError,ValueError):continue

            sample=st.radio('Evaluation sample',['Daily overlapping','Nonoverlapping'],

                            horizontal=True,key=f'validation_sample_{symbol}')

            label='daily_overlapping' if sample=='Daily overlapping' else 'nonoverlapping'
            st.caption('Daily overlapping: many forecasts share future returns.' if sample=='Daily overlapping' else 'Nonoverlapping: separate holding periods; fewer observations, no shared target days.')

            view=metrics[metrics['sample']==label]

            show_table(view[['horizon','model','scored','qlike','rmse_pp','mae_pp','bias_pp']].rename(

                columns={'horizon':'Forward sessions','model':'Model','scored':'Observations',

                         'qlike':'QLIKE','rmse_pp':'RMSE · pp','mae_pp':'MAE · pp','bias_pp':'Bias · pp'}))

            dates=pd.to_datetime(view.evaluation_start).min(),pd.to_datetime(view.evaluation_end).max()

            if not any(pd.isna(d) for d in dates):

                st.caption(f'Forecast origins: {dates[0]:%d %b %Y}–{dates[1]:%d %b %Y} · rolling one-year training')

            st.caption('Lower scores are better · positive bias = overprediction · daily samples overlap')
            if metadata.get('evaluation_start'):
                st.caption(f"Evaluation window: {metadata['evaluation_start']} to {metadata.get('data_through','—')} · recent forecasts without a completed outcome are excluded from scores. Yang–Zhang is a historical baseline.")

            st.download_button('Download validation scores',data=(manifest.parent/'metrics.csv').read_bytes(),

                               file_name=f'{symbol}_validation.csv',mime='text/csv')

            break

        else:st.info('No completed validation. Needs a training year plus subsequent history.')

@st.cache_data(show_spinner=False)

def matched_expiry(raw,expiry,as_of,iv,version):

    return expiry_forecast(pd.read_csv(__import__('io').BytesIO(raw)),expiry,as_of,iv)


@st.cache_data(show_spinner=False,max_entries=96)
def _cached_term_forecast(raw,expiry,valuation_bucket,version):
    return term_structure_forecast(pd.read_csv(__import__('io').BytesIO(raw)),expiry,valuation_bucket)


def term_forecast(raw,expiry,as_of,version):
    # The daily model only depends on quote date and whether that session closed.
    # Intraday quote ticks must not fit identical GARCH models again for each expiry.
    stamp=pd.Timestamp(as_of)
    if stamp.tzinfo is None:raise ValueError('Quote valuation requires an explicit timezone.')
    stamp=stamp.tz_convert(IST)
    closed=(stamp.hour,stamp.minute)>=(15,40)
    bucket=stamp.normalize()+pd.Timedelta(hours=15,minutes=40) if closed else stamp.normalize()
    result=_cached_term_forecast(raw,expiry,bucket.isoformat(),(version,EXPIRY_VERSION))
    result['Quote as of']=stamp.isoformat()
    return result


@st.cache_resource(show_spinner=False)
def _calendar_cache():
    from options_context_cache import CalendarCache
    return CalendarCache()

def render_models(symbol,selected,frame,result):

    from model_view import model_tables

    if frame is None:

        st.info('Refresh quotes to download the price history for this index.');return

    try:

        if selected.get('As of'):

            from expiry_forecast import sessions

            stamp=pd.Timestamp(selected['As of'])

            cutoff=stamp.date() if stamp.hour*60+stamp.minute>=940 else stamp.date()-timedelta(days=1)

            frame=frame[frame.Date.dt.date<=cutoff]

            if frame.empty or sessions(frame.Date.iloc[-1].date()+timedelta(days=1),cutoff):

                raise ValueError('Latest completed candle is missing. Refresh index table before interpreting forecasts.')

        forecasts,realized,ewma,diag=analyze_models(frame.to_csv(index=False).encode())

    except ValueError as exc:st.warning(str(exc));return

    a,b,c=st.columns(3)

    iv=selected.get('IV')

    a.metric('Selected expiry IV','—' if iv is None or not np.isfinite(iv) else f'{iv:.2f}%')

    garch20=float(forecasts.loc[forecasts['Forward sessions']==20,'GARCH annualized %'].iloc[0])
    b.metric('GARCH · next 20 trading days',f'{garch20:.2f}%')

    vix=result.get('india_vix',{}).get('value')

    yz20=float(realized.loc[realized['Trailing sessions']==20,'Yang–Zhang RV %'].iloc[0])
    c.metric('INDIA VIX' if symbol=='NIFTY' else 'Yang–Zhang · past 20 trading days',(f'{vix:.2f}%' if vix is not None else '—') if symbol=='NIFTY' else f'{yz20:.2f}%')

    st.markdown('**Volatility by horizon**')
    show_candidates=st.checkbox('Include candidate models (GJR-GARCH leverage, GARCH-t)',value=False,key='show_candidate_models',help='Fits asymmetric GJR-GARCH and fat-tailed Student-t models alongside baseline normal GARCH(1,1).')
    candidates=analyze_candidates(frame.to_csv(index=False).encode()) if show_candidates else None
    from model_view import horizon_grid
    grid=horizon_grid(forecasts,realized,ewma,candidates=candidates)
    horizon=st.select_slider('Holding period · trading days',options=grid.iloc[:,0].astype(int).tolist(),value=20,key='model_holding_period')
    show_table(grid[grid.iloc[:,0]==horizon])
    st.caption('Annualized % · Days = trading sessions · GARCH/EWMA: next N days · Yang–Zhang: past N days. Standard EWMA has a flat annualized forecast across horizons.')
    st.caption(f'EWMA holding-period volatility: {ewma*np.sqrt(horizon/252):.2f}% over {horizon} trading days')

    st.caption(f'{symbol} · {len(frame)} daily candles · through {frame.Date.max():%d %b %Y}')

    st.markdown('**Selected expiry · IV versus forecast RV**')

    try:

        if not selected.get('As of'):raise ValueError('Refresh quotes to establish the valuation time.')

        from model_comparison import comparison_snapshot
        comparison,using_close=comparison_snapshot(DATA/'monitor.sqlite',symbol,selected)
        matched=matched_expiry(full_price_history(symbol).to_csv(index=False).encode(),comparison['Expiry'],
                               comparison['As of'],comparison.get('IV'),EXPIRY_VERSION)
        if using_close:st.caption('Last matched end-of-day comparison · '+str(comparison['As of'])+' · not today’s live IV')

        show_table(pd.DataFrame([matched])[['DTE','Trading sessions','IV','GARCH expiry RV',

                                            'EWMA expiry RV','IV - GARCH','IV - EWMA']].rename(columns={
            'DTE':'Calendar days left','Trading sessions':'Trading days left','IV':'IV %',
            'GARCH expiry RV':'GARCH to expiry %','EWMA expiry RV':'EWMA to expiry %',
            'IV - GARCH':'IV − GARCH (pp)','IV - EWMA':'IV − EWMA (pp)'}))

        st.caption(f"Quotes: {matched['As of']} · prices through {matched['Training through']} · spreads: pp")

        with st.expander('Forecast context'):

            st.caption(f"Remaining sessions: {matched['Session equivalents']:.2f} · calendar: 2026")

            st.caption(f"GARCH: {matched['GARCH status']}")

    except (ValueError,KeyError,TypeError) as exc:st.info(f'Expiry forecast unavailable: {exc}')

    with st.expander('Model status'):

        st.caption('GARCH / EWMA: forecasts · Yang–Zhang: historical volatility')

        for message in diag['warnings']:st.warning(message)

        show_table(pd.DataFrame([{'Parameter':k,'Value':v} for k,v in diag.items() if isinstance(v,(int,float)) and not isinstance(v,bool)]))

    render_validation(symbol)

def _volatility_gap_reason(row,event_rows,calendar_status):

    if not row.get('Comparison ready',False):
        return (str(row.get('GARCH status','Forecast unavailable')) if not row.get('Forecast as of') else
                'Models start at the previous completed close. IV is newer; the IV–forecast gap is withheld until both refer to the same close.')
    iv=pd.to_numeric(row.get('IV'),errors='coerce')
    garch=pd.to_numeric(row.get('GARCH FRV %'),errors='coerce')
    ewma=pd.to_numeric(row.get('EWMA FRV %'),errors='coerce')
    forecasts=[float(value) for value in (garch,ewma) if pd.notna(value) and np.isfinite(value)]
    if not pd.notna(iv) or not np.isfinite(iv):
        return 'Broker IV is unavailable for this listed expiry.'
    if not forecasts:
        return 'FRV forecast is unavailable for this quote time or price history.'
    low,high=min(forecasts),max(forecasts)
    if iv>high+2:
        reason=f'IV ({iv:.2f}%) is above the GARCH/EWMA FRV range ({low:.2f}–{high:.2f}%). Possible contributors include scheduled results or meetings, option demand, and uncertainty not reflected in past prices.'
    elif iv<low-2:
        reason=f'IV ({iv:.2f}%) is below the GARCH/EWMA FRV range ({low:.2f}–{high:.2f}%). The models may be carrying recent turbulence forward while option prices imply calmer movement.'
    else:
        reason=f'IV ({iv:.2f}%) is close to the GARCH/EWMA FRV range ({low:.2f}–{high:.2f}%).'
    if event_rows:
        names=', '.join(f"{item.get('Event') or item.get('Company') or 'Scheduled event'} ({item['Date']})" for item in event_rows[:3])
        reason+=f' Scheduled event dates before this expiry: {names}. Dates are context and do not establish causation.'
    elif 'unavailable' in str(calendar_status).lower():
        reason+=' Some event calendars could not be refreshed, so event context may be incomplete.'
    else:
        reason+=' No scheduled event is listed before this expiry; positioning, skew, liquidity, and broader risk sentiment can also affect IV.'
    return reason


def render_term_structure(symbol,result):

    today=datetime.now(IST).date()
    catalog=result.get('catalog',{}).get(symbol,[])
    listed=sorted({pd.Timestamp(item.get('date')).date() for item in catalog
                   if item.get('date') and pd.Timestamp(item['date']).date()>=today})
    if not listed:
        st.info('Refresh quotes to load currently listed expiries for this index.');return
    listed_text={day.isoformat() for day in listed}
    rows=[row for row in result.get('rows',[]) if row.get('Instrument')==symbol
          and str(row.get('Expiry','')) in listed_text]
    curve=term_structure(rows,symbol)
    if curve.empty:
        st.info('No quote rows are saved for the currently listed expiries. Refresh quotes to update them.');return

    event_end=max(listed)
    events,event_status=_calendar_cache().get(DATA,symbol,today,event_end)
    forecastable=curve[curve['As of'].notna()&pd.to_numeric(curve.DTE,errors='coerce').gt(0)]
    forecasts=[]
    if not forecastable.empty:
        try:price_bytes=full_price_history(symbol).to_csv(index=False).encode()
        except (OSError,ValueError,KeyError) as error:
            price_bytes=None
            st.info(f'GARCH/EWMA FRV unavailable: {error}')
        if price_bytes:
            with st.spinner('Calculating FRV through each listed expiry…'):
                for row in curve.to_dict('records'):
                    item=dict(row)
                    item.update({'GARCH FRV %':np.nan,'EWMA FRV %':np.nan,
                                 'Training through':None,'GARCH status':'Unavailable',
                                 'Forecast as of':None,'Comparison ready':False,'Forecast basis':'Unavailable'})
                    if row.get('As of') and pd.to_numeric(row.get('DTE'),errors='coerce')>0:
                        try:
                            model=term_forecast(price_bytes,row['Expiry'],row['As of'],TERM_VERSION)
                            item.update({'GARCH FRV %':model.get('GARCH expiry RV',np.nan),
                                'EWMA FRV %':model.get('EWMA expiry RV',np.nan),
                                'Training through':model.get('Training through'),
                                'GARCH status':model.get('GARCH status','Unavailable'),
                                'Forecast as of':model['Forecast as of'],
                                'Comparison ready':model['Comparison ready'],'Forecast basis':model['Forecast basis']})
                        except Exception as error:item['GARCH status']=str(error)
                    forecasts.append(item)
    else:
        forecasts=curve.assign(**{'GARCH FRV %':np.nan,'EWMA FRV %':np.nan,
            'Training through':None,'GARCH status':'IV unavailable'}).to_dict('records')

    view=pd.DataFrame(forecasts)
    if view.empty:view=curve.assign(**{'GARCH FRV %':np.nan,'EWMA FRV %':np.nan,'Training through':None,'GARCH status':'Unavailable'})
    for field,default in [('Forecast as of',None),('Forecast basis','Unavailable'),('Comparison ready',False)]:
        if field not in view:view[field]=default
    # Bring the unadjusted put-call parity diagnostic forward from the original
    # option snapshot without treating it as a volatility-model input.
    parity_fields=['ATM strike','ATM call LTP','ATM put LTP','Synthetic future',
                   'Month future LTP','Future contract','Future quote time',
                   'Future reference','Synthetic status']
    parity_by_expiry={str(row.get('Expiry')):row for row in rows}
    for field in parity_fields:
        view[field]=view['Expiry'].astype(str).map(lambda expiry:parity_by_expiry.get(expiry,{}).get(field))
    fig=go.Figure()
    view=view.sort_values('Expiry').reset_index(drop=True)
    custom=np.column_stack([view.Expiry,view['As of'].fillna(''),view['Source'].fillna(''),
                            view.get('Training through',pd.Series('',index=view.index)).fillna(''),
                            view['Forecast as of'].fillna(''),view['Forecast basis']])
    series=[('IV','IV','solid','#5b8cff','top center'),
            ('GARCH FRV %','GARCH FRV','dash','#ff684d','bottom right'),
            ('EWMA FRV %','EWMA FRV','dot','#00c99a','top left')]
    plotted=[]
    for field,label,dash,color,text_position in series:
        values=pd.to_numeric(view[field],errors='coerce')
        if not values.notna().any():continue
        plotted.append(values)
        fig.add_trace(go.Scatter(x=pd.to_datetime(view.Expiry),y=values,name=label,
            mode='lines+markers+text',text=[f'{value:.2f}%' if pd.notna(value) else '' for value in values],
            textposition=text_position,textfont={'color':color,'size':10},cliponaxis=False,
            line={'dash':dash,'color':color},marker={'color':color},connectgaps=False,customdata=custom,
            hovertemplate='Expiry: %{customdata[0]}<br>%{fullData.name}: %{y:.2f}%<br>IV source: %{customdata[2]}<br>IV time: %{customdata[1]}<br>Model time: %{customdata[4]}<br>%{customdata[5]}<extra></extra>'))

    start_day=today
    event_colors={'India policy':'#b79cff','India macro':'#f0b74e',
                  'Global policy':'#63b3ed','Global inflation':'#4fd1c5'}
    marked_events={(str(item.get('Date')),item.get('Category')) for item in events
                   if isinstance(item,dict) and item.get('Date') and item.get('Category') in event_colors}
    for event_day,category in sorted(marked_events):
        try:date_value=pd.Timestamp(event_day).date()
        except (TypeError,ValueError):continue
        if pd.isna(date_value):continue
        if start_day<=date_value<=event_end:
            fig.add_vline(x=date_value,line_dash='dot',line_color=event_colors[category],opacity=.55)
    fig.update_layout(height=350,template='plotly_dark',paper_bgcolor='#0c1321',plot_bgcolor='#0c1321',
        margin={'l':10,'r':15,'t':25,'b':15},xaxis_title='Listed expiry',yaxis_title='Annualized volatility %',
        xaxis_type='date',xaxis_range=[today,event_end],yaxis_tickformat='.2f',legend={'orientation':'h'})
    if plotted:
        all_values=pd.concat(plotted).dropna()
        padding=max(1.0,(all_values.max()-all_values.min())*.12)
        fig.update_yaxes(range=[max(0,float(all_values.min())-padding),float(all_values.max())+padding])
    st.plotly_chart(fig,key=f'term_structure_{symbol}',width='stretch',config={'displaylogo':False})
    st.caption('IV: broker quote · FRV: model forecast to expiry from completed closes · dotted lines: macro event dates')
    if (~view['Comparison ready']).any() and view['Forecast as of'].notna().any():
        st.caption('During trading hours, models show the previous-close reference. IV–forecast gaps require the same close.')

    st.subheader('Event calendar')
    scope='Macro events + bank results' if symbol=='BANKNIFTY' else 'Index macro events'
    st.caption(f'{scope} · refreshes automatically every 12 hours when viewed · through {event_end:%d %b %Y}')
    if event_status.startswith('Partly available') or 'checking updates' in event_status:st.caption(event_status)
    if events:
        event_frame=pd.DataFrame(events)
        st.dataframe(event_frame[['Date','Event','Category','Details','Source']],hide_index=True,width='stretch',
            column_config={'Source':st.column_config.LinkColumn('Source',display_text='Open source')})
    else:
        st.caption('No events are listed before the furthest currently listed expiry.')

    if not view.empty:
        expiry_labels=view.Expiry.astype(str).tolist()
        picked=st.selectbox('Listed expiry',expiry_labels,key=f'term_expiry_{symbol}')
        selected=view[view.Expiry.astype(str)==picked].iloc[0].to_dict()
        try:
            valuation_day=pd.Timestamp(selected['As of']).tz_convert(IST).date()
            event_context=[event for event in events if valuation_day<=pd.Timestamp(event['Date']).date()<=pd.Timestamp(picked).date()]
        except (ValueError,TypeError,KeyError):event_context=[]
        iv=pd.to_numeric(selected.get('IV'),errors='coerce')
        garch=pd.to_numeric(selected.get('GARCH FRV %'),errors='coerce')
        ewma=pd.to_numeric(selected.get('EWMA FRV %'),errors='coerce')
        a,b,c,d=st.columns(4)
        a.metric('IV',f'{iv:.2f}%' if pd.notna(iv) else '—')
        b.metric('GARCH FRV',f'{garch:.2f}%' if pd.notna(garch) else '—')
        c.metric('EWMA FRV',f'{ewma:.2f}%' if pd.notna(ewma) else '—')
        gap=iv-garch if selected.get('Comparison ready') and pd.notna(iv) and pd.notna(garch) else np.nan
        d.metric('IV − GARCH FRV',f'{gap:+.2f} pp' if pd.notna(gap) else '—')
        st.caption(f"IV: {selected.get('As of','unavailable')} · model: {selected.get('Forecast as of') or 'unavailable'} · {selected.get('Forecast basis','Unavailable')}")
        st.info(_volatility_gap_reason(selected,event_context,event_status))
        synthetic=pd.to_numeric(selected.get('Synthetic future'),errors='coerce')
        monthly=pd.to_numeric(selected.get('Month future LTP'),errors='coerce')
        a,b=st.columns(2)
        a.metric('ATM synthetic future',f'{synthetic:.2f}' if pd.notna(synthetic) else '—')
        b.metric('Month future LTP',f'{monthly:.2f}' if pd.notna(monthly) else '—')
        st.caption(f"ATM strike: {selected.get('ATM strike') if selected.get('ATM strike') is not None else 'unavailable'} · {selected.get('Future reference','Future reference unavailable')} · {selected.get('Synthetic status','Synthetic future not captured yet')}")

    view['IV − GARCH FRV · pp']=(pd.to_numeric(view.IV,errors='coerce')-pd.to_numeric(view['GARCH FRV %'],errors='coerce')).where(view['Comparison ready'])
    view['IV − EWMA FRV · pp']=(pd.to_numeric(view.IV,errors='coerce')-pd.to_numeric(view['EWMA FRV %'],errors='coerce')).where(view['Comparison ready'])
    show_table(view[['Expiry','Type','DTE','IV','GARCH FRV %','EWMA FRV %','IV − GARCH FRV · pp',
                     'IV − EWMA FRV · pp','Synthetic future','Month future LTP','Future reference',
                     'Synthetic status','As of','Forecast as of','Forecast basis','GARCH status']])
    with st.popover('Export'):
        st.download_button('Term structure CSV',view.to_csv(index=False).encode(),
            file_name=f'{symbol}_term_structure.csv',mime='text/csv')

def render_smile(symbol,selected,config):

    as_of=selected.get('As of')
    saved=load_smile(DATA/'monitor.sqlite',symbol,selected.get('Expiry'),config,as_of)
    saved_as_of=as_of
    exact_capture_unusable=saved is not None and not has_usable_points(saved)
    exact_cloud_capture_missing=saved is None and bool(as_of) and selected.get('Capture source')=='Google Drive'
    if saved is not None:
        if exact_capture_unusable:saved=None
    if saved is None:
        saved=load_latest_smile(DATA/'monitor.sqlite',symbol,selected.get('Expiry'),config)
        if saved is not None:saved_as_of=saved.pop('as_of',None)

    if saved is None:
        if exact_capture_unusable or exact_cloud_capture_missing:
            st.info('This Google Cloud capture has no usable broker IV around ATM, 25-delta or 10-delta. No smile is estimated from option prices.')
        else:
            st.info('No usable Google Cloud smile capture is saved for this expiry yet. It will appear after the next archive sync.')
        return

    if saved_as_of and saved_as_of!=as_of:
        if exact_capture_unusable or selected.get('Capture source')=='Google Drive':
            reason='the selected Cloud capture has no usable saved smile points'
        else:reason='the selected quote has not synced yet'
        st.caption(f'Latest usable Google Cloud capture · {saved_as_of} · {reason}')

    points=pd.DataFrame(saved['points'])

    fig=go.Figure(go.Scatter(x=points.Point,y=points.IV,mode='lines+markers+text',connectgaps=False,
        text=[f'{v:.2f}%' if pd.notna(v) else '' for v in points.IV],textposition='top center',cliponaxis=False,

        line={'color':'#50c5b4'},hovertemplate='%{x}: %{y:.2f}%<extra></extra>'))

    fig.update_layout(height=300,template='plotly_dark',paper_bgcolor='#0c1321',plot_bgcolor='#0c1321',

                      margin={'l':10,'r':15,'t':25,'b':15},yaxis_title='IV %',yaxis_tickformat='.2f')

    st.plotly_chart(fig,key=f'smile_{symbol}',width='stretch',config={'displaylogo':False})
    cols=st.columns(len(saved['metrics']))
    for col,(label,value) in zip(cols,saved['metrics'].items()):
        measure='RR'+label.split('RR',1)[1].split(' ·',1)[0]+' risk reversal' if label.startswith('RR') else 'BF'+label.split('BF',1)[1].split(' ·',1)[0]+' butterfly'
        col.metric(measure,'—' if value is None else f'{value:.2f} pp')
    st.caption('RR = put IV − call IV. BF = average wing IV − ATM IV. Values are volatility points.')

    with st.expander('Smile values & measures'):
        show_table(points)
        show_table(pd.DataFrame([saved['metrics']]))

    st.caption('Broker IV and Greeks from Google Cloud · fixed-delta points interpolate between listed contracts')

    with st.popover('Export'):
        st.download_button('Smile CSV',points.to_csv(index=False).encode(),file_name=f'{symbol}_smile.csv',mime='text/csv')

def render_relative_value(symbol,selected,config):

    from relative_value import history as research_history,maturity_stats
    from volatility_regime import regime_input,regime_summary

    with st.expander('Historical volatility regime'):

        try:

            path=regime_input(DATA,symbol)

            summary=regime_summary(pd.read_csv(path),selected.get('As of'))

            show_table(summary[['Measure','HV %','Prior-year percentile','Regime','Prior sessions','As of']])

            st.caption('Historical HV percentile · low ≤20 · elevated ≥80')

        except (OSError,ValueError,TypeError) as exc:st.info(str(exc))

    saved=research_history(DATA/'monitor.sqlite',symbol,config,comparable=True)

    if saved.empty:

        st.info('Research observations will appear after Cloud captures.');return

    match=saved[(saved.Expiry==selected.get('Expiry'))&(saved['As of']==selected.get('As of'))&(saved.Config==config)]

    if match.empty:

        latest=pd.to_datetime(saved['As of'],format='ISO8601',utc=True).max().tz_convert(IST)
        st.info(f'No matching research record for this quote. Latest saved history: {latest:%d %b} · {len(saved)} observations.')
        return

    current=match.iloc[-1].to_dict()

    measures=[]

    for metric in ['IV','IV-RV Spread','25 Delta reversal']:

        measures.append({'Measure':metric,'Value':current.get(metric),**maturity_stats(saved,current,metric)})

    ready=[m for m in measures if m['Percentile'] is not None]

    if ready:show_table(pd.DataFrame(ready))

    else:st.caption('Relative rankings unavailable · collecting comparable expiry cycles')

    with st.expander('Research coverage'):

        show_table(pd.DataFrame(measures)[['Measure','Prior days','Expiry cycles','Status']])

    st.caption('Volatility: annualized % · spreads: pp · historical DTE matched ±3 days')

    st.caption(f"Saved observations: {len(saved)} · latest snapshot: {current['As of']}")

    st.download_button('Download research history',saved.to_csv(index=False).encode(),file_name=f'{symbol}_research_history.csv',mime='text/csv')

def render_monitor(rate=.065,dividend=0.,spread=3.):

    config=calculation_key(rate,dividend,spread)

    client=st.session_state.get('data_client');ready=bool(client and client.authenticated)

    if not isinstance(st.session_state.get('expiry_engine'),ExpiryMonitor):st.session_state.expiry_engine=ExpiryMonitor()

    engine=st.session_state.expiry_engine

    top_a,top_b=st.columns([3,2])

    with top_a:
        st.subheader('Options overview')

    with top_b:auto=st.toggle('Auto refresh',value=True,disabled=not ready,help='Refresh quotes during market hours; discover listed expiries hourly while this page is open.')

    @st.fragment

    def panel():

        from pricing_inputs import get_rate

        from pricing_inputs import get_yields
        current_dividends={k:v.get('model_yield') for k,v in get_yields().items()}
        if isinstance(dividend,dict) and current_dividends!=dividend:st.rerun()
        current_rate=get_rate().get('model_rate')

        if current_rate is not None and current_rate!=rate:st.rerun()

        config=calculation_key(rate,dividend,spread)
        import background_refresh as background
        completed=background.take(st.session_state,'options')
        if completed:
            if completed[0]:
                st.session_state.pop('options_refresh_error',None)
                st.toast('Options data updated')
            else:st.session_state.options_refresh_error=completed[1]
        if st.session_state.get('options_refresh_error'):st.warning(st.session_state.options_refresh_error)

        a,b=st.columns([1,5])

        pressed=a.button('Refresh quotes',disabled=not ready or background.pending(st.session_state,'options'),type='secondary',width='stretch')

        requested_config=config
        result=load_snapshot(config,allow_saved=True)
        if result is not None:st.session_state['_last_options_snapshot']=result
        else:result=st.session_state.get('_last_options_snapshot')
        earlier_settings=bool(result and result.get('config')!=requested_config)

        initial=not st.session_state.get('expiry_initial_attempted',False)

        from refresh_policy import refresh_due
        timed=auto and refresh_due(datetime.now(IST),result,
            time.monotonic()-st.session_state.get('expiry_last_attempt',0))

        if ready and (pressed or initial or timed) and not background.pending(st.session_state,'options'):
            st.session_state.expiry_initial_attempted=True
            st.session_state.expiry_last_attempt=time.monotonic()
            background.submit(st.session_state,'options',engine.refresh_all,
                background.frozen_client(client),rate,dict(dividend) if isinstance(dividend,dict) else dividend,spread)
        def refresh_in_background():
            if not auto or not ready or background.pending(st.session_state,'options'):return
            if refresh_due(datetime.now(IST),None,time.monotonic()-st.session_state.get('expiry_last_attempt',0)):
                st.session_state.expiry_last_attempt=time.monotonic()
                background.submit(st.session_state,'options',engine.refresh_all,
                    background.frozen_client(client),rate,dict(dividend) if isinstance(dividend,dict) else dividend,spread)
        background.status('options',refresh_in_background)

        if result:

            if result.get('config')!=config:

                st.caption('Previous snapshot · original pricing settings retained until updated quotes are ready')

            config=result.get('config',config)

            stamp=pd.Timestamp(result['timestamp'])

            dates=sorted({str(r.get('As of',''))[:10] for r in result['rows'] if r.get('As of')})

            action='last attempt' if any(r.get('Refresh status') for r in result['rows']) else 'refreshed'
            b.caption(f"Quotes: {', '.join(dates)} · {action} {stamp:%d %b %H:%M} · {'Connected' if ready else 'Saved view'}")

        else:

            b.caption('Refresh quotes to apply these settings.' if ready else 'Connect Upstox in the sidebar to load expiry quotes.')

            result={'rows':[],'details':[],'catalog':{},'india_vix':{}}

        # load_snapshot already aligns spreads; fresh refresh results are aligned at collection.

        chosen={};catalog=result.get('catalog',{})

        for col,symbol in zip(st.columns(len(INDICES)),INDICES):

            entries=catalog.get(symbol,[])

            # Keep only genuinely listed dates from the fetched master / saved snapshot.

            entries=[e for e in entries if pd.Timestamp(e['date']).date()>=datetime.now(IST).date()]

            dates=[e['date'] for e in entries];kinds={e['date']:e['kind'] for e in entries}

            if dates:

                key='expiry_choice_'+symbol

                if st.session_state.get(key) not in dates:st.session_state[key]=dates[0]

                chosen[symbol]=col.selectbox(symbol,dates,key=key,format_func=lambda d,k=kinds:f"{pd.Timestamp(d):%d %b %Y} · {k[d]}")

            else:col.selectbox(symbol,['Connect / refresh'],disabled=True,key='unavailable_'+symbol)

        weekly_dates=[pd.Timestamp(e['date']) for e in catalog.get('NIFTY',[]) if e.get('kind')=='Weekly']
        if weekly_dates:
            last_weekly=max(weekly_dates)
            st.caption(f'NIFTY weeklies listed through {last_weekly:%d %b %Y} · BANKNIFTY is monthly.')
        else:
            st.caption('NIFTY weeklies appear as listed · BANKNIFTY is monthly.')

        result={**result,'rows':[dict(r) for r in result['rows']]}

        selected_rows=[]

        for symbol in INDICES:

            row=next((r for r in result['rows'] if r['Instrument']==symbol and r['Expiry']==chosen.get(symbol)),None)

            if row:selected_rows.append(dict(row))

            else:selected_rows.append({'Instrument':symbol,'Expiry':chosen.get(symbol,'—'),**{c:np.nan for c in COLUMNS}})

        chart_rows=[dict(r) for r in selected_rows]
        # Keep verified captured values readable; age is explicit, not hidden.
        for row in selected_rows:
            status=snapshot_status(row.get('As of'),row.get('Source'),ready,retrieved_at=row.get('Retrieved at'))
            if status=='Unavailable' or status.startswith('Invalid future'):
                for column in COLUMNS:row[column]=np.nan
        table=pd.DataFrame(selected_rows)

        table['Quote status']=[snapshot_status(r.get('As of'),r.get('Source'),ready,retrieved_at=r.get('Retrieved at')) for r in selected_rows]
        if any(r.get('Refresh status') for r in selected_rows):
            st.caption('Latest refresh failed · showing saved observations with their original quote times. Details below.')

        if earlier_settings:table['Quote status']='Saved · earlier settings'

        table['Underlying as of']=[pd.Timestamp(r['As of']).strftime('%d %b %H:%M') if r.get('As of') else 'Unavailable' for r in selected_rows]

        render_trade_decision_radar(selected_rows)

        parity_columns=['ATM strike','ATM call LTP','ATM put LTP','Synthetic future',
                        'Month future LTP','Future contract','Future quote time',
                        'Future reference','Synthetic status']
        for column in parity_columns:
            if column not in table:table[column]=np.nan if column not in ('Future contract','Future quote time','Future reference','Synthetic status') else 'Unavailable'
        show_table(table[['Instrument','Price','IV','IV-RV Spread','Synthetic future','Month future LTP','Quote status']])
        with st.expander('Quote details'):
            show_table(table[['Instrument','Expiry','% change','Call IV','Put IV','25 Delta reversal','Underlying as of']])
            st.markdown('**ATM synthetic future check**')
            show_table(table[['Instrument','Expiry',*parity_columns]])
            st.caption('Synthetic future = ATM strike + ATM call LTP − ATM put LTP. Monthly expiries use the same-expiry future; NIFTY weekly expiries use that calendar month’s listed monthly future. Option-chain LTP timestamps are not supplied by the broker, so these are displayed separately as a market-consistency reference, not an executable price or fair-value model.')

        issues=[f"{r['Instrument']}: {r.get('RV status')}" for r in selected_rows if r.get('RV status') not in (None,'Same-session close','Prior completed session')]

        if issues:st.warning('IV–RV unavailable · '+ ' · '.join(issues) + '. Refresh quotes to fetch completed candles.')

        if result.get('catalog_incomplete'):st.caption('The saved expiry list is incomplete; refresh quotes to reload Upstox-listed contracts.')

        st.session_state.active_monitor_result={**result,'rows':selected_rows}

        st.divider()

        a,b=st.columns([1,3])

        symbol=a.selectbox('Instrument',list(INDICES),key='research_symbol')

        expiry=chosen.get(symbol)

        if st.session_state.get('options_chart_view_v2')=='Position risk':
            st.session_state.options_chart_view_v2='Volatility history'
        with b:
            views=['Volatility history','IV–RV Spread','Term structure','Smile & skew','3D Vol Surface']
            if st.session_state.get('advanced_workspace'):views+=['Research','Intraday']
            if st.session_state.get('options_chart_view_v2') not in views:st.session_state.options_chart_view_v2='Volatility history'
            chart=st.segmented_control('Chart',views,key='options_chart_view_v2',label_visibility='collapsed',selection_mode='single')
        chart=chart or 'Volatility history'



        selected={**next(r for r in selected_rows if r['Instrument']==symbol),'Config':config}
        chart_selected={**next(r for r in chart_rows if r['Instrument']==symbol),'Config':config}

        frame=None;hv=None
        if chart in ('Volatility history','IV–RV Spread'):
            try:frame,hv=chart_prices(symbol)
            except (OSError,ValueError,KeyError):
                st.warning('HV could not be loaded from saved daily prices. IV–RV requires those prices too.')


        if chart in ('Term structure','Smile & skew','3D Vol Surface'):
            status=snapshot_status(chart_selected.get('As of'),chart_selected.get('Source'),ready,retrieved_at=chart_selected.get('Retrieved at'))
            if not status.startswith(('Recent snapshot','Recent API retrieval')):
                st.caption('Saved observation · underlying '+str(chart_selected.get('As of','unavailable'))+' · '+status)
        if chart=='Term structure':render_term_structure(symbol,result)
        elif chart=='Smile & skew':render_smile(symbol,chart_selected,config)
        elif chart=='3D Vol Surface':
            from vol_surface_3d import render_vol_surface_3d
            render_vol_surface_3d(symbol,chart_selected,result,DATA)
        elif chart=='Research':
            res_view=st.radio('Research tool',['Relative value & smile metrics','Candidate model validation & diagnostics','HAR-RV volatility cascades'],horizontal=True,key='research_tool_selector')
            if res_view=='Relative value & smile metrics':
                render_relative_value(symbol,chart_selected,config)
            elif res_view=='Candidate model validation & diagnostics':
                from research_ui import render_research
                render_research(DATA.parent,symbol,chart_selected)
            elif res_view=='HAR-RV volatility cascades':
                st.subheader(f'{symbol} · HAR-RV Volatility Cascades')
                st.caption('Heterogeneous Autoregressive realized volatility decomposing trailing daily (1d), weekly (5d), and monthly (22d) components.')
                hist_path=DATA.parent/'data'/f'{symbol.lower()}_daily.csv'
                if not hist_path.exists():hist_path=DATA/f'{symbol.lower()}_one_year.csv'
                if hist_path.exists():
                    df=pd.read_csv(hist_path)
                    from har_research import har_components_summary
                    summary=har_components_summary(df)
                    st.line_chart(summary.set_index('Date').tail(126)[['Daily RV %','Weekly (5d) RV %','Monthly (22d) RV %']])
                    st.dataframe(summary.tail(10).round(2),hide_index=True,width='stretch')
                else:
                    st.info('Daily price history not found for HAR calculation.')
        elif chart=='Intraday':
            from restored_lab import render_archived_intraday
            render_archived_intraday(DATA,symbol,chart_selected,config)


        if chart in ('Volatility history','IV–RV Spread'):

            scope_col,range_col,settings_col=st.columns([3,2,1])
            exp_label = f"Selected: {expiry}" if expiry else "Selected expiry"
            scope_map = {exp_label: 'Selected expiry', 'Continuous roll (across expiries)': 'Across expiries'}
            
            chosen_scope_label = scope_col.segmented_control(
                'Scope',
                [exp_label, 'Continuous roll (across expiries)'],
                default=exp_label,
                label_visibility='collapsed',
                key=f'iv_history_scope_v7_{symbol}_{expiry}'
            ) or exp_label
            scope = scope_map.get(chosen_scope_label, 'Selected expiry')

            period=range_col.segmented_control('Range',['1M','3M','6M','1Y'],default='1M',label_visibility='collapsed',key='options_range_v2') or '1M'
            with settings_col.popover('Chart settings',width='stretch'):
                relative=st.toggle('Relative spread %',value=False,key='iv_history_relative_v2')
                filter_0dte=st.toggle('Filter 0-DTE terminal anomalies',value=True,key='filter_0dte_artifacts',help='Excludes terminal settlement Black-Scholes solver spikes on expiry day (T=0).')

            if scope=='Across expiries':
                from option_context import transition_context
                hist=transition_context(DATA/'monitor.sqlite',symbol,config)
            else:
                hist=history(symbol,expiry,config) if expiry else pd.DataFrame(columns=['Date','IV','Spread','Source'])

            if not hist.empty:
                sources=hist.Source.fillna('')
                hist=hist[sources.str.startswith('Upstox supplied IV')].copy()
                hist=hist.dropna(subset=['IV']).copy()
                # Exclude 0-DTE terminal Black-Scholes vega singularities on expiration day (Date == Expiry)
                if filter_0dte and not hist.empty and 'Expiry' in hist.columns:
                    terminal_spike = (pd.to_datetime(hist['Date']) == pd.to_datetime(hist['Expiry'])) & (pd.to_numeric(hist['IV']) > 50.0)
                    if terminal_spike.any():
                        hist = hist[~terminal_spike].copy()

            if hist.empty:
                st.info(f'No historical IV observations recorded for {symbol} {expiry} yet. Switch to "Continuous roll" to view historical index volatility.')
            else:
                active_label = f"Expiry {expiry} ({hist.Date.nunique()} sessions)" if scope == 'Selected expiry' else "Continuous Front-Month Index Roll"
                st.caption(f"**Series:** {active_label} · {hist.Date.min():%d %b %Y} to {hist.Date.max():%d %b %Y}")

            data=chart_iv_spread(hist,hv)

            relative=st.session_state.get('iv_history_relative_v2', False)

            st.plotly_chart(history_figure(symbol,expiry,hv,data,period,relative,spread_only=chart=='IV–RV Spread'),key=f'history_{symbol}_{expiry}_{period}_{chart}',width='stretch',config={'displaylogo':False})

            start=pd.Timestamp(datetime.now(IST).date())-pd.DateOffset(months={'1M':1,'3M':3,'6M':6,'1Y':12}[period])
            visible=data[data.Date>=start]
            spread_days=int(visible['IV-RV Spread'].notna().sum())
            available_hv=hv[(hv.Date>=start)&hv['Historical vol 30D'].notna()] if hv is not None else pd.DataFrame()
            hv_text=f'HV: {len(available_hv)} sessions through {available_hv.Date.max():%d %b %Y}' if len(available_hv) else 'HV: unavailable'
            st.caption(hv_text+f' · IV–RV: {spread_days} sessions')
            if not visible.empty and visible['IV-RV Spread'].isna().any():
                reasons=visible.loc[visible['IV-RV Spread'].isna(),'RV status'].value_counts()
                if len(reasons):st.caption('Spread gaps: '+', '.join(str(k)+' ('+str(v)+')' for k,v in reasons.items()))
            if chart=='IV–RV Spread':
                st.caption('Spread = broker IV − HV30. When HV30 moves slowly, the spread can track IV closely.')

        if st.session_state.get('advanced_workspace'):
            from trade_review_ui import render_risk_context
            render_risk_context(DATA,symbol,chart_selected)

        with st.expander('Data notes'):
            st.caption('HV uses rolling log returns from daily closes, annualized with 252 trading sessions.')
            if st.button('Save selected table'):
                folder=DATA/'exports';folder.mkdir(exist_ok=True)
                table[['Instrument','Expiry',*COLUMNS]].round(2).to_csv(folder/'index_options_table.csv',index=False)
                st.success('Saved in Volatility Lab / data / exports.')

    panel()

