"""RBI 91-day auction cutoff yield; cached and converted to continuous ACT/365."""
from datetime import datetime,timezone
from html import unescape
from pathlib import Path
import json,math,re,threading
from urllib.request import Request,urlopen
from collector_support import atomic_json
URL='https://www.rbi.org.in/'
PATH=Path(__file__).resolve().parent/'data'/'risk_free_rate.json'
_lock=threading.Lock()

def parse_rate(html):
    text=unescape(re.sub('<[^>]+>',' ',html))
    text=' '.join(text.split())
    match=re.search(r'91\s+day\s+T-bills\s*:?\s*([0-9]+(?:\.[0-9]+)?)\s*%',text,re.I)
    if not match or 'cut-off at the last auction' not in text.lower():raise ValueError('RBI T91 auction rate not found')
    value=float(match[1])
    if not 0<value<30:raise ValueError('Invalid T91 rate')
    return value

def get_rate(force=False):
    with _lock:
        try:saved=json.loads(PATH.read_text(encoding='utf-8'))
        except (OSError,ValueError):saved={}
        now=datetime.now(timezone.utc)
        checked=saved.get('last_attempt')
        if not force and checked and (now-datetime.fromisoformat(checked)).total_seconds()<3600:return saved
        saved['last_attempt']=now.isoformat()
        try:
            with urlopen(Request(URL,headers={'User-Agent':'VolatilityLab/1.0'}),timeout=6) as response:
                raw=response.read(500000).decode('utf-8')
            value=parse_rate(raw)
            saved.update(yield_percent=value,model_rate=math.log1p(value/100*91/365)*365/91,
                         checked_at=now.isoformat(),source=URL,basis='91-day T-bill auction cutoff',error=None)
        except (OSError,ValueError) as error:
            saved['error']='RBI update unavailable; using last verified rate.' if 'model_rate' in saved else 'RBI rate unavailable.'
        if saved.get('error') is None and saved.get('model_rate') is not None:
            from input_provenance import record
            record('rate',saved)
        atomic_json(PATH,saved)
        return saved
