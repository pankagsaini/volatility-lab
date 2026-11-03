"""Read-only Upstox market-data connection with Windows-encrypted credentials."""
from datetime import datetime, timedelta
import json
import os
import re
import socket
import time
from http.client import HTTPSConnection
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener, HTTPSHandler

from collector_support import protect, atomic_json
from market_data import IST, NoRedirect, MarketDataError

ROOT=Path(__file__).resolve().parent
TOKEN_PATH=ROOT/'.auth'/'upstox_analytics.bin'
REPORT_PATH=ROOT/'data'/'upstox'/'connection_test.json'
INDICES={'NIFTY':'NSE_INDEX|Nifty 50','BANKNIFTY':'NSE_INDEX|Nifty Bank'}


def _prefer_ipv4(address,timeout=socket._GLOBAL_DEFAULT_TIMEOUT,source_address=None):
    # Some local networks advertise IPv6 without a working route. Keep TLS/SNI intact.
    addresses=socket.getaddrinfo(address[0],address[1],0,socket.SOCK_STREAM)
    addresses.sort(key=lambda r:r[0]!=socket.AF_INET)
    error=None
    for family,kind,proto,_,target in addresses:
        sock=socket.socket(family,kind,proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:sock.settimeout(timeout)
            if source_address:sock.bind(source_address)
            sock.connect(target);return sock
        except OSError as exc:error=exc;sock.close()
    if error:raise error
    raise OSError('No endpoint addresses available')

class _MarketHTTPSConnection(HTTPSConnection):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._create_connection=_prefer_ipv4

class _MarketHTTPSHandler(HTTPSHandler):
    def https_open(self,req):
        return self.do_open(_MarketHTTPSConnection,req,context=self._context)


class UpstoxDataError(MarketDataError):
    pass


def http_error_message(error,path):
    # Only expose allowlisted codes, never response messages/invalid_value or tokens.
    codes=[]
    try:
        payload=json.loads(error.read(65536))
        if payload.get('cloudflare_error') is True and payload.get('error_code')==1010:
            return 'Upstox gateway rejected the client signature (Cloudflare 1010). Token validity could not be checked.'
        for item in payload.get('errors',[]):
            code=item.get('error_code',item.get('errorCode',''))
            if isinstance(code,str) and re.fullmatch(r'UDAPI[0-9]{1,10}',code):codes.append(code)
    except (ValueError,TypeError,AttributeError,OSError):pass
    explanations={
        'UDAPI100050':'Upstox says the token is invalid. Copy the full token using the Analytics copy icon; do not copy the shortened displayed text.',
        'UDAPI100073':'Upstox says your client ID is inactive. Account reactivation must be completed with Upstox.',
        'UDAPI100067':'Upstox does not permit this endpoint with this token type. Use an Analytics Token.',
        'UDAPI100016':'Upstox rejected the credentials.',
        'UDAPI1149':'This endpoint requires Upstox Plus access.',
    }
    reason=next((explanations[c] for c in codes if c in explanations),None)
    if reason is None:
        reason={401:'Upstox did not authenticate the token.',403:'Upstox denied access to this data endpoint.',429:'Upstox rate limit reached; retry later.'}.get(error.code,'Upstox request failed.')
    return reason+' [HTTP '+str(error.code)+('; '+', '.join(dict.fromkeys(codes)) if codes else '')+'; '+path+']'


class UpstoxDataClient:
    def __init__(self,token):
        token=token.strip()
        if token.lower().startswith('bearer '):token=token[7:].strip()
        if len(token)>1 and token[0]==token[-1] and token[0] in (chr(34),chr(39)):
            token=token[1:-1].strip()
        if '...' in token or '…' in token:
            raise UpstoxDataError('This looks like the shortened display token. Use the copy icon in Upstox Analytics to copy the full token.')
        if not token or any(c.isspace() for c in token):
            raise UpstoxDataError('Enter the Analytics Token from Upstox Developer Apps.')
        self._token=token
        self._opener=build_opener(NoRedirect(),_MarketHTTPSHandler())

    def _get(self,path,params=None,*,before_attempt=None):
        allowed=path in ('/v2/market-quote/quotes','/v2/option/contract','/v2/option/chain') or path.startswith('/v3/historical-candle/') or path in ('/v2/expired-instruments/expiries','/v2/expired-instruments/option/contract','/v2/expired-instruments/future/contract') or path.startswith('/v2/expired-instruments/historical-candle/')
        if not allowed or '..' in path or '?' in path or '#' in path:
            raise UpstoxDataError('Only approved read-only market-data endpoints are enabled.')
        url='https://api.upstox.com'+path
        if params:url+='?'+urlencode(params)
        request=Request(url,headers={'Authorization':'Bearer '+self._token,'Accept':'application/json','User-Agent':'VolatilityLab/1.0 (read-only market-data client)'},method='GET')
        for attempt in range(3):
            if before_attempt is not None:
                before_attempt()
            try:
                with self._opener.open(request,timeout=20) as response:
                    payload=json.loads(response.read(20000000))
                break
            except HTTPError as error:
                if error.code in (429,500,502,503,504) and attempt<2:
                    try:delay=min(float((error.headers or {}).get('Retry-After','0')),30.)
                    except (TypeError,ValueError):delay=0.
                    time.sleep(max(delay,2.0*(2**attempt)))
                    continue
                raise UpstoxDataError(http_error_message(error,path)) from None
            except (URLError,OSError,ValueError):
                if attempt<2:
                    time.sleep(2.0*(2**attempt))
                    continue
                raise UpstoxDataError('Upstox connection failed or returned an invalid response after 3 attempts (retry horizon exceeded).') from None
        if payload.get('status')!='success' or 'data' not in payload:
            raise UpstoxDataError('Upstox did not return successful market data.')
        return payload['data']

    def quotes(self,keys):
        return self._get('/v2/market-quote/quotes',{'instrument_key':','.join(keys)})

    def contracts(self,key):
        return self._get('/v2/option/contract',{'instrument_key':key})

    def chain(self,key,expiry):
        return self._get('/v2/option/chain',{'instrument_key':key,'expiry_date':expiry})

    def daily(self,key,start,end):
        return self._get('/v3/historical-candle/'+quote(key,safe='')+'/days/1/'+str(end)+'/'+str(start))

    def intraday_daily(self,key):
        """Broker's current-session daily candle; callers must enforce close cutoff."""
        return self._get('/v3/historical-candle/intraday/'+quote(key,safe='')+'/days/1')


def save_token(token):
    client=UpstoxDataClient(token)
    # Verify read-only market access before persisting a replacement token.
    result=client.quotes(list(INDICES.values()))
    if not result:raise UpstoxDataError('Upstox returned no index quotes; token was not saved.')
    TOKEN_PATH.parent.mkdir(exist_ok=True)
    temp=TOKEN_PATH.with_suffix('.tmp')
    try:
        temp.write_bytes(protect(client._token.encode()))
        os.replace(temp,TOKEN_PATH)
    finally:temp.unlink(missing_ok=True)
    return client


def saved_client():
    try:return UpstoxDataClient(protect(TOKEN_PATH.read_bytes(),decrypt=True).decode())
    except Exception:raise UpstoxDataError('No readable saved Upstox token. Connect it below.') from None


def connection_test(client):
    now=datetime.now(IST)
    report={'checked_at':now.isoformat(),'status':'partial','checks':[],'note':'Read-only Upstox market-data access checks.'}
    for symbol,key in INDICES.items():
        for kind in ('quotes','daily_history','option_chain'):
            row={'instrument':symbol,'check':kind}
            try:
                if kind=='quotes':
                    data=client.quotes([key]);count=len(data)
                elif kind=='daily_history':
                    data=client.daily(key,now.date()-timedelta(days=14),now.date()-timedelta(days=1));count=len(data.get('candles',[]))
                else:
                    contracts=client.contracts(key)
                    expiries=sorted({r['expiry'] for r in contracts if r.get('expiry','')>=str(now.date())})
                    if not expiries:raise UpstoxDataError('No current option expiries returned.')
                    data=client.chain(key,expiries[0]);count=len(data)
                    row['expiry']=expiries[0]
                if not count:raise UpstoxDataError('No market observations returned.')
                row.update(status='passed',observations=count)
            except UpstoxDataError as error:row.update(status='failed',reason=str(error))
            report['checks'].append(row)
    if all(r['status']=='passed' for r in report['checks']):report['status']='passed'
    atomic_json(REPORT_PATH,report)
    return report


def render_connection():
    import streamlit as st
    from collector_support import read_json
    with st.expander('Upstox connection'):
        st.link_button('Open Upstox Developer Apps','https://account.upstox.com/developer/apps')
        st.caption('Use the Analytics token from your Upstox developer app.')
        with st.form('upstox_connect',clear_on_submit=True):
            token=st.text_input('Analytics Token',type='password')
            submitted=st.form_submit_button('Save and test Upstox')
        retry=st.button('Retest saved Upstox connection',disabled=not TOKEN_PATH.exists())
        if submitted or retry:
            try:
                with st.spinner('Checking Upstox market-data access…'):
                    client=save_token(token) if submitted else saved_client()
                    report=connection_test(client)
                if report['status']=='passed':st.success('Upstox market-data access verified.')
                else:st.warning('Some Upstox data checks failed; see results below.')
            except UpstoxDataError as error:st.error(str(error))
            except Exception:st.error('Could not save or test Upstox. Retry after checking your connection.')
        report=read_json(REPORT_PATH)
        if report:
            checked=str(report.get('checked_at') or 'time unavailable').replace('T',' ')[:25]
            st.caption(f"Last access test · {report.get('status','unknown')} · {checked}")
            failed=[row for row in report.get('checks',[]) if row.get('status')!='passed']
            for row in failed:
                st.error(f"{row.get('instrument','Upstox')} · {row.get('check','check')}: {row.get('reason','Check failed')}")
