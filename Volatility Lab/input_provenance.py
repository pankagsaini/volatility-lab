"""Retain pricing inputs beside raw broker captures; never store credentials."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parent/'data'

def record(kind,payload):
    raw=json.dumps({'kind':kind,'input':payload},sort_keys=True,allow_nan=False).encode()
    digest=hashlib.sha256(raw).hexdigest();folder=ROOT/'input_history';folder.mkdir(exist_ok=True)
    path=folder/(kind+'_'+digest+'.json')
    if not path.exists():path.write_bytes(raw)
    return digest

def snapshot(rate,dividend,symbol):
    result={'rate':rate,'dividend':dividend,'symbol':symbol}
    for name,file in [('rate','risk_free_rate.json'),('dividend','dividend_yields.json')]:
        try:
            value=json.loads((ROOT/file).read_text(encoding='utf-8'))
            if name=='dividend':value=value[symbol]
            expected=rate if name=='rate' else dividend
            actual=value.get('model_rate' if name=='rate' else 'model_yield')
            if actual is not None and expected is not None and abs(actual-expected)<1e-12:
                result[name+'_source']=value
                result[name+'_record']=record(name,value)
        except (OSError,ValueError,KeyError):pass
    return result
