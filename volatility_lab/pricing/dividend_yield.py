"""Official index factsheet dividend yields; dated proxy, not forward dividends."""
from volatility_lab import PACKAGE_ROOT
from datetime import datetime,timezone
from pathlib import Path
import hashlib,io,json,re,threading
from urllib.request import Request,urlopen
from pypdf import PdfReader
from collector_support import atomic_json
from collector_support import IST

ROOT=PACKAGE_ROOT/'data'
SOURCES={'NIFTY':'https://www.niftyindices.com/Factsheet/ind_nifty50.pdf','BANKNIFTY':'https://www.niftyindices.com/Factsheet/ind_nifty_bank.pdf'}
_lock=threading.Lock()

def parse_pdf(raw,symbol,today):
    if not raw.startswith(b'%PDF'):raise ValueError('Source is not a PDF')
    text=PdfReader(io.BytesIO(raw)).pages[0].extract_text(extraction_mode='layout')
    identity='Nifty 50 index' if symbol=='NIFTY' else 'Nifty Bank Index'
    if identity.lower() not in text.lower():raise ValueError('Wrong index factsheet')
    date=re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}',text)
    if not date:raise ValueError('Missing publication date')
    published=datetime.strptime(date.group(),'%B %d, %Y').date()
    if not 0<=(today-published).days<=62:raise ValueError('Factsheet is future dated or older than 62 days')
    lines=text.splitlines();values=[]
    for i,line in enumerate(lines):
        if 'Dividend Yield' not in line or 'P/E' not in line or 'P/B' not in line:continue
        following=next((l for l in lines[i+1:i+4] if l.strip()),'')
        cell=following[max(0,line.index('Dividend Yield')-3):line.index('Dividend Yield')+30]
        numbers=re.findall(r'(?<![\d.])\d+(?:\.\d+)?(?![\d.])',cell)
        if len(numbers)!=1:raise ValueError('Ambiguous dividend column')
        values.append(float(numbers[0]))
    if len(values)!=1 or not 0<=values[0]<=15:raise ValueError('Missing or invalid dividend yield')
    return {'yield_percent':values[0],'model_yield':values[0]/100,'as_of':str(published),'basis':'Published index dividend yield proxy; not expiry-specific expected dividends'}

def get_yields(force=False,root=ROOT,now=None):
    now=now or datetime.now(IST);today=now.astimezone(IST).date();path=root/'dividend_yields.json'
    with _lock:
        try:cache=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):cache={}
        changed=False
        for symbol,url in SOURCES.items():
            item=cache.get(symbol,{})
            attempt=item.get('attempt_day')
            if force or attempt!=str(today):
                changed=True
                try:
                    raw=urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=8).read(4000000)
                    parsed=parse_pdf(raw,symbol,today)
                    if item.get('as_of','')>parsed['as_of']:raise ValueError('Publication date regressed')
                    digest=hashlib.sha256(raw).hexdigest();folder=root/'dividend_sources';folder.mkdir(exist_ok=True)
                    target=folder/(symbol+'_'+digest+'.pdf')
                    if not target.exists():target.write_bytes(raw)
                    item={**parsed,'source':url,'sha256':digest,'checked_at':now.isoformat(),'error':None}
                except Exception as error:
                    item={**item,'source':url,'error':'Official dividend lookup failed; '+type(error).__name__}
                item['attempt_day']=str(today);cache[symbol]=item
        if changed:
            from input_provenance import record
            for symbol,item in cache.items():
                if item.get('error') is None and item.get('model_yield') is not None:record('dividend',{'symbol':symbol,**item})
            try:atomic_json(path,cache)
            except OSError:
                for item in cache.values():item['error']='Verified yield available; cache save failed, retry next refresh'
        result=json.loads(json.dumps(cache))
        for item in result.values():
            if not item.get('as_of') or (today-datetime.fromisoformat(item['as_of']).date()).days>62:
                item['model_yield']=None;item['error']='No recent verified dividend yield; refresh unavailable'
        return result

def for_symbol(value,symbol):
    return float(value[symbol]) if isinstance(value,dict) else float(value)
