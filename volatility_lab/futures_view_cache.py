"""Read-only revisions for futures view caches; no TTL can hide a new close."""
from contextlib import closing
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3


def file_revision(path):
    try:
        stat=Path(path).stat()
        return (stat.st_mtime_ns,stat.st_size)
    except FileNotFoundError:
        return None


def database_revision(root):
    """Include committed WAL updates but exclude read-only shared-memory touches."""
    path=Path(root)/'futures.sqlite'
    return (file_revision(path),file_revision(str(path)+'-wal'),
            file_revision(Path(__file__).with_name('futures_data.py')))


@lru_cache(maxsize=16)
def _rolling_content(root,symbol,db_revision,calendar_revision):
    path=Path(root)/'futures.sqlite'
    if not path.exists():return 'missing'
    with closing(sqlite3.connect('file:'+path.resolve().as_posix()+'?mode=ro',uri=True,timeout=5)) as con:
        con.execute('BEGIN')
        prices=con.execute('SELECT rank,day,close,volume FROM continuous WHERE symbol=? ORDER BY rank,day',(symbol,)).fetchall()
        exists=con.execute("SELECT 1 FROM sqlite_master WHERE name='continuous_provenance'").fetchone()
        provenance=con.execute('SELECT rank,day,expiry,provider FROM continuous_provenance WHERE symbol=? ORDER BY rank,day',(symbol,)).fetchall() if exists else []
    calendar=Path(root).parent/'listed_monthly_expiries.json'
    if calendar.exists():
        values=json.loads(calendar.read_text(encoding='utf-8')).get(symbol,{})
        expiries=sorted((month,row.get('expiry')) for month,row in values.items())
    else:expiries=[]
    return hashlib.sha256(repr((prices,provenance,expiries)).encode('utf-8')).hexdigest()


def rolling_revision(root,symbol):
    root=Path(root).resolve()
    calendar=root.parent/'listed_monthly_expiries.json'
    content=deepcopy(_rolling_content(str(root),symbol,database_revision(root),file_revision(calendar)))
    source=Path(__file__).parent
    return (content,*(file_revision(source/name) for name in
                     ('futures_rolling.py','futures_data.py','market_calendar.py')))
