"""Expiry variance matching; calendar annualization matches option IV's ACT/365."""
from datetime import datetime, time, timedelta
import numpy as np
import pandas as pd
from forecast import fit_garch
from vol_lib import validate_ohlc, rv_snapshot
from market_data import IST
from market_calendar import HOLIDAYS, last_trading_day_on_or_before

VERSION = 'expiry-v3-completed-session-only-nse2026'
TERM_VERSION = 'term-v1-last-completed-close'
CALENDAR_SOURCE = 'https://nsearchives.nseindia.com/content/circulars/FAOP71777.pdf'
SPECIAL = pd.Timestamp('2026-11-08').date()


def sessions(start, end):
    if start.year != 2026 or end.year != 2026:
        raise ValueError('Verified trading calendar currently covers 2026 only.')
    return [d.date() for d in pd.date_range(start, end)
            if (d.weekday() < 5 and d.date() not in HOLIDAYS) or d.date() == SPECIAL]


def expiry_forecast(frame, expiry, as_of, iv=None):
    """Condition on completed daily closes only; partial day is a stated approximation.

    Full future sessions each receive one daily variance, including Muhurat.
    No intraday model or overnight/intraday decomposition is claimed.
    """
    now = pd.Timestamp(as_of)
    if now.tzinfo is None:
        raise ValueError('Quote valuation requires an explicit timezone.')
    now = now.tz_convert(IST).to_pydatetime()
    expiry = pd.Timestamp(expiry).date()
    reason=forecast_restriction(expiry,now)
    if reason:raise ValueError(reason)
    end = datetime.combine(expiry, time(15,40), tzinfo=IST)
    years = (end-now).total_seconds()/(365*86400)
    if years <= 0:
        raise ValueError('Expiry has elapsed at the quote valuation time.')
    if now.date() == SPECIAL or expiry == SPECIAL:
        raise ValueError('Muhurat intraday timings require a verified special-session schedule.')
    if expiry not in sessions(expiry, expiry):
        raise ValueError('Expiry is not a verified trading date.')
    data = validate_ohlc(frame)
    # Today's candle is available only after the completed-session boundary.
    cutoff = now.date() if now.time() >= time(15,40) else now.date()-timedelta(days=1)
    data = data[data.index.date <= cutoff]
    if data.empty:
        raise ValueError('No completed candles available at the quote time.')
    last = data.index[-1].date()
    elapsed = sessions(last+timedelta(days=1), cutoff)
    if elapsed:
        raise ValueError('Latest completed session is missing; refresh underlying history.')
    data = data[data.index > data.index[-1]-pd.DateOffset(years=1)]
    if len(data) < 201:
        raise ValueError('Expiry models need at least 200 completed returns.')
    dates = sessions(last+timedelta(days=1), expiry)
    if not dates or len(dates)>245:
        raise ValueError('Unsupported expiry forecast horizon.')
    weights = []
    for date in dates:
        weight = 1.
        if date < now.date():weight = 0.
        elif date == now.date():
            opening = datetime.combine(date,time(9,15),tzinfo=IST)
            closing = datetime.combine(date,time(15,40),tzinfo=IST)
            weight = float(np.clip((closing-now).total_seconds()/(closing-opening).total_seconds(),0,1))
        weights.append(weight)
    weights = np.array(weights)
    if weights.sum() <= 0:raise ValueError('No remaining trading variance horizon.')
    training = data.reset_index()
    ewma = rv_snapshot(training)['ewma_next_session_vol']**2/245
    result = {'Expiry':str(expiry),'As of':now.isoformat(),'Training through':str(last),
              'DTE':years*365,'Trading sessions':int((weights>0).sum()),
              'Session equivalents':float(weights.sum()),'IV':iv,
              'EWMA expiry RV':float(100*np.sqrt(ewma*weights.sum()/years)),
              'GARCH expiry RV':np.nan,'GARCH status':'Ready','Version':VERSION}
    try:
        _, diagnostics, path, _ = fit_garch(training,(len(dates),),min_observations=200)
        variances=(path['Daily conditional vol %'].to_numpy()/100)**2/245
        result['GARCH expiry RV']=float(100*np.sqrt(np.dot(variances,weights)/years))
        result['GARCH status']='; '.join(diagnostics['warnings']) or 'Ready'
    except (ValueError,RuntimeError,FloatingPointError) as error:
        result['GARCH status']=str(error)
    for model in ('GARCH','EWMA'):
        result[f'IV - {model}']=float(iv-result[f'{model} expiry RV']) if iv is not None and np.isfinite(iv) else np.nan
    return result


SAME_DAY_STATUS = 'Withheld: expiry-day RV needs a validated remaining-session model.'
FORECAST_FIELDS = ('GARCH expiry RV','EWMA expiry RV','IV - GARCH','IV - EWMA')

def same_day(expiry,as_of):
    stamp=pd.Timestamp(as_of)
    if stamp.tzinfo is None:raise ValueError('Quote valuation requires an explicit timezone.')
    return pd.Timestamp(expiry).date()==stamp.tz_convert(IST).date()

def eligible_history(frame):
    """Read-time quarantine of old same-day estimates; raw archive stays intact."""
    out=frame.copy()
    if out.empty or not {'Expiry','As of'}.issubset(out):return out
    reasons=out.apply(lambda row:forecast_restriction(row['Expiry'],row['As of']),axis=1)
    mask=reasons.ne('')
    for field in FORECAST_FIELDS:
        if field in out:out.loc[mask,field]=np.nan
    for field in ('Forecast status','GARCH status'):
        out.loc[mask,field]=reasons[mask]
    return out


def forecast_restriction(expiry,as_of):
    stamp=pd.Timestamp(as_of)
    if stamp.tzinfo is None:raise ValueError('Quote valuation requires an explicit timezone.')
    stamp=stamp.tz_convert(IST)
    if same_day(expiry,stamp):return SAME_DAY_STATUS
    if stamp.time()<time(15,40):
        return 'Withheld: expiry RV comparison requires a completed-session snapshot; no validated intraday allocation.'
    return ''


def term_structure_forecast(frame,expiry,as_of):
    """Last-close model context, explicitly separate from a newer intraday IV.

    Do not allocate daily variance across the remaining intraday session. During
    trading hours the horizon still starts at the previous close, and IV/model
    gaps are suppressed because the horizons do not match.
    """
    quote=pd.Timestamp(as_of)
    if quote.tzinfo is None:raise ValueError('Quote valuation requires an explicit timezone.')
    quote=quote.tz_convert(IST)
    if same_day(expiry,quote):raise ValueError(SAME_DAY_STATUS)
    cutoff=quote.date() if quote.time()>=time(15,40) else quote.date()-timedelta(days=1)
    data=validate_ohlc(frame)
    data=data[data.index.date<=cutoff]
    if data.empty:raise ValueError('No completed candles available at the quote time.')
    last=data.index[-1].date()
    expected_prior=last_trading_day_on_or_before(quote.date()-timedelta(days=1))
    if last<expected_prior:raise ValueError('Latest completed session is missing; refresh underlying history.')
    anchor=datetime.combine(last,time(15,40),tzinfo=IST)
    model=expiry_forecast(data.reset_index(),expiry,anchor)
    matched=last==quote.date() and quote.time()>=time(15,40)
    model.update({'Forecast as of':anchor.isoformat(),'Quote as of':quote.isoformat(),
                  'Comparison ready':matched,
                  'Forecast basis':'Same close' if matched else 'Previous close · reference'})
    return model
