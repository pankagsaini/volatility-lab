"""Compare recorded IV across rate updates; never merge unknown assumptions."""
from contextlib import closing
import hashlib
import json
import sqlite3

LEGACY_CONVENTION='spot-atm-close1540-quality-v1'
CONVENTION='upstox-supplied-iv-v1'

def normalized_dividend(dividend):
    return {k:(float(v) if v is not None else None) for k,v in sorted(dividend.items())} if isinstance(dividend,dict) else (float(dividend) if dividend is not None else None)

def key(rate,dividend,spread,convention=CONVENTION):
    return hashlib.sha256(json.dumps([float(rate) if rate is not None else None,normalized_dividend(dividend),float(spread),convention]).encode()).hexdigest()[:20]

def register(path,rate,dividend,spread):
    path.parent.mkdir(parents=True,exist_ok=True)
    config=key(rate,dividend,spread)
    # Ordinary chart reruns are readers, not SQLite writers. In particular, do
    # not wait on the cloud import's write lock to re-register existing settings.
    if path.exists():
        with closing(sqlite3.connect(path,timeout=20)) as con:
            if (con.execute("SELECT 1 FROM sqlite_master WHERE name='calculation_assumptions'").fetchone()
                    and con.execute('SELECT 1 FROM calculation_assumptions WHERE config=?',(config,)).fetchone()):
                return
    with closing(sqlite3.connect(path,timeout=20)) as con,con:
        con.execute('CREATE TABLE IF NOT EXISTS calculation_assumptions(config TEXT PRIMARY KEY,rate REAL,dividend REAL,spread REAL,convention TEXT)')
        con.execute('INSERT OR IGNORE INTO calculation_assumptions VALUES(?,?,?,?,?)',(config,rate,json.dumps(normalized_dividend(dividend),sort_keys=True) if isinstance(dividend,dict) else dividend,spread,CONVENTION))

def compatible(path,config):
    # Only this legacy configuration is independently documented by the audit.
    known={key(.065,0.,3.,LEGACY_CONVENTION):(.065,0.,3.,LEGACY_CONVENTION)}
    if path.exists():
        with closing(sqlite3.connect(path,timeout=20)) as con:
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='calculation_assumptions'").fetchone():
                known.update({r[0]:r[1:] for r in con.execute('SELECT * FROM calculation_assumptions')})
    if config not in known:return [config]
    target=known[config]
    return [config]+sorted(k for k,v in known.items() if k!=config and tuple(v[1:])==tuple(target[1:]))

def assumptions(path,config):
    if config==key(.065,0.,3.,LEGACY_CONVENTION):return {'rate':.065,'dividend':0.,'max_spread':3.}
    if not path.exists():return None
    with closing(sqlite3.connect(path)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='calculation_assumptions'").fetchone():return None
        row=con.execute('SELECT rate,dividend,spread FROM calculation_assumptions WHERE config=?',(config,)).fetchone()
    if not row:return None
    result=dict(zip(('rate','dividend','max_spread'),row))
    if isinstance(result['dividend'],str):result['dividend']=json.loads(result['dividend'])
    return result
