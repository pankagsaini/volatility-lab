"""Deterministic quote selection; no network or IV calculation."""
import math
import pandas as pd
from market_data import IST

VERSION='quote-quality-v3-feed60s'


def number(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (ValueError,TypeError):return None


def timestamp(value):
    if value is None or value=='':raise ValueError('Missing timestamp')
    if isinstance(value,(int,float)) or str(value).strip().isdigit():
        numeric=float(value)
        if not math.isfinite(numeric):raise ValueError('Invalid timestamp')
        stamp=pd.to_datetime(numeric,unit='ms' if abs(numeric)>1e11 else 's',utc=True)
    else:
        stamp=pd.Timestamp(value)
    if pd.isna(stamp):raise ValueError('Missing timestamp')
    return stamp.tz_localize(IST) if stamp.tzinfo is None else stamp.tz_convert(IST)


def assess_quote(quote,now,max_spread,closing=False):
    if not isinstance(quote,dict):quote={}
    result=dict(accepted=False,reason='',source='',premium=None,bid=None,ask=None,
        mid=None,ltp=number(quote.get('last_price')),spread_pct=None,
        quote_timestamp=quote.get('timestamp'),quote_age_seconds=None,
        trade_timestamp=quote.get('last_trade_time'),trade_age_seconds=None,
        quality_version=VERSION)
    def reject(reason):
        result['reason']=reason
        return result
    try:
        now=timestamp(now);stamp=timestamp(quote.get('timestamp'))
        age=(now-stamp).total_seconds();result['quote_age_seconds']=age
        if closing:
            if stamp.date()!=now.date() or stamp.hour<15:return reject('wrong_closing_session')
        elif not -5<=age<=60:return reject('stale_or_future_quote')
    except (ValueError,TypeError):return reject('missing_quote_timestamp')
    depth=quote.get('depth') or {}
    if not isinstance(depth,dict):depth={}
    def top(side):
        values=depth.get(side) or []
        return values[0] if isinstance(values,list) and values and isinstance(values[0],dict) else {}
    bid,ask=top('buy'),top('sell')
    b,a=number(bid.get('price')),number(ask.get('price'))
    result.update(bid=b,ask=a)
    book_reason='missing_or_empty_depth'
    if b is not None and a is not None and b>0 and a>0:
        if b>a:return reject('crossed_book')
        if b==a:return reject('locked_book')
        mid=(a+b)/2;spread=100*(a-b)/mid
        result.update(mid=mid,spread_pct=spread)
        if (number(bid.get('quantity')) or 0)<=0 or (number(ask.get('quantity')) or 0)<=0:
            book_reason='zero_depth_quantity'
        elif spread>max_spread:book_reason='wide_spread'
        else:
            result.update(accepted=True,premium=mid,source='Bid/ask midpoint',
                          reason='closing_midpoint_estimate' if closing else 'valid_midpoint')
            return result
    if not closing:return reject(book_reason)
    try:
        traded=timestamp(quote.get('last_trade_time'))
        age=(now-traded).total_seconds();result['trade_age_seconds']=age
        if not 0<=age<=1800:return reject('stale_or_future_last_trade')
    except (ValueError,TypeError):return reject('missing_last_trade_timestamp')
    if result['ltp'] is None or result['ltp']<=0:return reject('invalid_ltp')
    result.update(accepted=True,premium=result['ltp'],source='Closing-session LTP estimate',
                  reason='ltp_fallback_'+book_reason)
    return result
