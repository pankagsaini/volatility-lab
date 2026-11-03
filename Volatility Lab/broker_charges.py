"""Dated Upstox NSE fee estimates for backtests; expiry exercise is excluded."""
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
import json,math
import requests
from collector_support import atomic_json,read_json

ROOT=Path(__file__).resolve().parent/'data'/'charges'/'upstox'
URL='https://upstox.com/brokerage-charges/'


class PageText(HTMLParser):
    def __init__(self):
        super().__init__();self.parts=[]

    def handle_data(self,data):
        if data.strip():self.parts.append(data.strip())


def refresh():
    """Verify the published standard rates still match the modeled schedule."""
    today=datetime.now().astimezone().date().isoformat()
    old=read_json(ROOT/'current.json')
    if old.get('checked')==today:return old
    try:
        response=requests.get(URL,timeout=12);response.raise_for_status()
        parser=PageText();parser.feed(response.text)
        page=' '.join(' '.join(parser.parts).split())
        expected=('₹20','0.15%','0.05%','0.03553%','0.00183%')
        if any(value not in page for value in expected):
            raise ValueError('Published rates differ from the saved fee schedule.')
        result={'checked':today,'rates':{kind:rates(today,kind) for kind in ('Futures','Options')},
                'source':URL,'status':'checked','plan':'Upstox standard; per-order brokerage is configurable'}
        ROOT.mkdir(parents=True,exist_ok=True)
        atomic_json(ROOT/(today+'.json'),result);atomic_json(ROOT/'current.json',result)
        atomic_json(ROOT/'check_status.json',result)
        return result
    except Exception:
        result={**old,'status':'check_failed','attempted':today,
                'warning':'Upstox fee schedule could not be verified. The saved dated rates remain in use.'}
        atomic_json(ROOT/'check_status.json',result)
        return result


def rates(day,kind,brokerage_per_order=20):
    day=str(day)[:10]
    if day<'2024-04-01':raise ValueError('Upstox fee history is modeled from 2024-04-01.')
    if kind not in ('Futures','Options'):raise ValueError('Unsupported contract type.')
    try:brokerage=float(brokerage_per_order)
    except (TypeError,ValueError):raise ValueError('Brokerage per order must be non-negative.')
    if brokerage<0 or not math.isfinite(brokerage):
        raise ValueError('Brokerage per order must be non-negative.')

    if kind=='Futures':
        stt=.000125;exchange=.0000188;ipft=.10/1e7;stamp=.00002;brokerage_rate=.0005
    else:
        stt=.000625;exchange=.000495;ipft=.50/1e7;stamp=.00003;brokerage_rate=None
    if day>='2024-10-01':
        stt=.0002 if kind=='Futures' else .001
        exchange=.0000173 if kind=='Futures' else .0003503
    if day>='2026-03-01':
        # Upstox publishes these as combined exchange/IPFT charges.
        exchange=.0000183 if kind=='Futures' else .0003553
        ipft=0.
    if day>='2026-04-01':
        stt=.0005 if kind=='Futures' else .0015
    return {'stt':stt,'exchange':exchange,'ipft':ipft,'stamp':stamp,'sebi':.000001,
            'gst':.18,'brokerage':brokerage,'brokerage_rate':brokerage_rate}


def charge(day,kind,price,qty,side,brokerage_per_order=20):
    if side not in ('buy','sell'):raise ValueError('Side must be buy or sell.')
    r=rates(day,kind,brokerage_per_order)
    value=float(price)*float(qty)
    if value<0 or not math.isfinite(value):raise ValueError('Invalid traded value.')
    brokerage=min(r['brokerage'],value*r['brokerage_rate']) if r['brokerage_rate'] is not None else r['brokerage']
    exchange_ipft=value*(r['exchange']+r['ipft'])
    parts={'Brokerage':brokerage,'STT':value*r['stt'] if side=='sell' else 0.,
           'Exchange/IPFT':exchange_ipft,'SEBI':value*r['sebi'],
           'Stamp':value*r['stamp'] if side=='buy' else 0.}
    parts['GST']=(brokerage+exchange_ipft)*r['gst']
    return parts


if __name__=='__main__':print(json.dumps(refresh()))
