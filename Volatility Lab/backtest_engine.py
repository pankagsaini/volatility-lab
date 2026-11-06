"""Draft intraday backtest: exact-minute open fills, explicit missing-data skips."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
import json,gzip,sqlite3,math,threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from backtest_common import record_skip

ROOT=Path(__file__).resolve().parent/'data/backtest_1minute'
_CACHE_LOCK=threading.RLock()
_FILE_CACHE=OrderedDict()
_MAX_CACHE_FILES=4096
_ZIP_HANDLE=None
_ZIP_LOCK=threading.Lock()


def _ensure_queue_sqlite():
    local_q = ROOT / 'queue.sqlite'
    if local_q.is_file() and local_q.stat().st_size > 1000:
        return local_q
    for letter in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
        cand = Path(f"{letter}:/My Drive/Historical Data/Volatility Lab - Historical Archives/queue.sqlite")
        if cand.is_file() and cand.stat().st_size > 1000:
            import shutil
            ROOT.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(cand, local_q)
                return local_q
            except Exception:
                return cand
    return local_q


def _get_archive_zip():
    global _ZIP_HANDLE
    if _ZIP_HANDLE is not None:
        return _ZIP_HANDLE
    with _ZIP_LOCK:
        if _ZIP_HANDLE is not None:
            return _ZIP_HANDLE
        candidates = [ROOT / 'backtest_1minute_5year_archive.zip']
        for letter in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
            candidates.append(Path(f"{letter}:/My Drive/Historical Data/Volatility Lab - Historical Archives/backtest_1minute_5year_archive.zip"))
        for p in candidates:
            if p.is_file():
                try:
                    import zipfile
                    _ZIP_HANDLE = zipfile.ZipFile(p, 'r')
                    return _ZIP_HANDLE
                except Exception:
                    pass
    return None


def _read_gzip_json(path_or_file):
    filename = Path(path_or_file).name if isinstance(path_or_file, (str, Path)) else str(path_or_file)
    with _CACHE_LOCK:
        if filename in _FILE_CACHE:
            _FILE_CACHE.move_to_end(filename)
            return _FILE_CACHE[filename]

    local_file = ROOT / filename
    if local_file.is_file():
        with gzip.open(local_file, 'rt', encoding='utf-8') as f:
            obj = json.load(f)
    else:
        z = _get_archive_zip()
        if z is not None:
            try:
                with z.open(filename, 'r') as zf:
                    with gzip.GzipFile(fileobj=zf) as gf:
                        obj = json.load(gf)
            except KeyError:
                raise FileNotFoundError(f"Candle archive file {filename} not found locally or in archive zip.")
        else:
            raise FileNotFoundError(f"Candle archive file {filename} not found in {ROOT} and archive zip not located.")

    with _CACHE_LOCK:
        _FILE_CACHE[filename] = obj
        while len(_FILE_CACHE) > _MAX_CACHE_FILES:
            _FILE_CACHE.popitem(last=False)
    return obj


def _fast_stamp(iso_s):
    if len(iso_s) >= 16 and (iso_s.endswith('+05:30') or iso_s.endswith('+0530')):
        return f"{iso_s[:10]} {iso_s[11:16]}"
    try:
        dt = datetime.fromisoformat(iso_s)
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return None


def load(symbol, start, end):
    q_path = _ensure_queue_sqlite()
    if not q_path.is_file() or q_path.stat().st_size == 0:
        raise FileNotFoundError(
            f"Historical 1-minute database (queue.sqlite) is not available. "
            f"Please ensure Google Drive Desktop is connected or place queue.sqlite in {ROOT}."
        )
    con = sqlite3.connect(q_path, timeout=30)
    try:
        jobs = con.execute(
            "SELECT kind,meta,file FROM jobs WHERE state='done' AND kind IN ('contracts','minute') AND json_extract(meta, '$.symbol') = ?",
            (symbol,)
        ).fetchall()
    except sqlite3.OperationalError:
        jobs = con.execute("SELECT kind,meta,file FROM jobs WHERE state='done' AND kind IN ('contracts','minute')").fetchall()
    finally:
        con.close()

    start_s, end_s = str(start), str(end)
    contracts_jobs = []
    minute_jobs = []
    for kind, metastr, file in jobs:
        meta = json.loads(metastr)
        if meta.get('symbol') != symbol:
            continue
        if kind == 'contracts':
            contracts_jobs.append((file, meta))
        elif kind == 'minute':
            if meta.get('end', '9999') >= start_s and meta.get('start', '0000') <= end_s:
                minute_jobs.append((file, meta))

    contracts = {}
    for file, meta in contracts_jobs:
        try:
            o = _read_gzip_json(file)
            for r in o.get('data', []):
                if r.get('instrument_type') in ('CE', 'PE'):
                    contracts[r['instrument_key']] = r
        except Exception:
            continue

    series = {}
    conflicts = set()

    def _read_minute(job):
        file, meta = job
        try:
            o = _read_gzip_json(file)
            return meta['key'], o.get('data', {}).get('candles', [])
        except Exception:
            return None

    max_w = min(12, max(2, (len(minute_jobs) // 100) + 1))
    with ThreadPoolExecutor(max_workers=max_w, thread_name_prefix='bt-load') as pool:
        results = pool.map(_read_minute, minute_jobs)

    for item in results:
        if not item:
            continue
        key, candles = item
        dest = series.setdefault(key, {})
        for row in candles:
            stamp = _fast_stamp(row[0])
            if not stamp or not (start_s <= stamp[:10] <= end_s):
                continue
            if stamp in dest and dest[stamp] != row[1:]:
                conflicts.add((key, stamp))
            dest[stamp] = row[1:]

    for key, stamp in conflicts:
        series[key].pop(stamp, None)
    return list(contracts.values()), series, len(conflicts)


def simulate(contracts,series,spotkey,start,end,entry='09:20',exit='15:15',offset=0,wings=0,quantity=1,slip_bps=5,order_fee=25,turnover_bps=0,min_dte=1,max_dte=35,use_upstox_fees=False,signal_rows=None,filters=()):
    if entry>=exit or not 0<=slip_bps<10000 or quantity<=0 or offset<0 or wings<0:raise ValueError('Invalid strategy settings')
    trades=[];skips=[];spot=series.get(spotkey,{})
    days=sorted({s[:10] for s in spot if str(start)<=s[:10]<=str(end)})
    for day in days:
        decision=(datetime.fromisoformat(day+' '+entry)-timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M')
        bar=spot.get(decision)
        if not bar:record_skip(skips,day,'Missing preceding spot minute');continue
        price=float(bar[3])
        eligible=[r for r in contracts if min_dte<=(pd.Timestamp(r['expiry'])-pd.Timestamp(day)).days<=max_dte]
        if not eligible:record_skip(skips,day,'No eligible expiry in catalogue');continue
        exp=min(r['expiry'] for r in eligible)
        if filters:
            from backtest_signals import decide
            allowed,reason=decide(signal_rows or [],exp,day,entry,filters)
            if not allowed:record_skip(skips,day,reason);continue
        chain=[r for r in eligible if r['expiry']==exp]
        def nearest(kind,target):
            choices=[r for r in chain if r['instrument_type']==kind]
            return min(choices,key=lambda r:(abs(float(r['strike_price'])-target),float(r['strike_price']))) if choices else None
        call=nearest('CE',price+offset);put=nearest('PE',price-offset)
        if not call or not put:record_skip(skips,day,'Missing contract definitions');continue
        legs=[(call,-1),(put,-1)]
        if wings:
            c=nearest('CE',float(call['strike_price'])+wings);p=nearest('PE',float(put['strike_price'])-wings)
            if not c or not p or float(c['strike_price'])<=float(call['strike_price']) or float(p['strike_price'])>=float(put['strike_price']):record_skip(skips,day,'Protective strikes unavailable');continue
            legs.extend([(c,1),(p,1)])
        fills=[];gross=slippage=turnover=0.;reason=None;cost_parts={}
        for r,side in legs:
            data=series.get(r['instrument_key'],{});a=data.get(day+' '+entry);b=data.get(day+' '+exit)
            if a is None or b is None:reason='Missing exact entry/exit minute';break
            try:
                raw_a=float(a[0]);raw_b=float(b[0])
                if not all(math.isfinite(x) and x>0 for x in (raw_a,raw_b,float(a[4]),float(b[4]))):raise ValueError()
            except (ValueError,TypeError,IndexError):reason='Invalid price or no traded volume';break
            buy_a=raw_a*(1+side*slip_bps/10000);sell_b=raw_b*(1-side*slip_bps/10000)
            gross+=side*(raw_b-raw_a)*quantity
            slippage+=(raw_a+raw_b)*slip_bps/10000*quantity
            turnover+=(buy_a+sell_b)*quantity
            if use_upstox_fees:
                from broker_charges import charge
                for px,action in [(buy_a,'buy' if side==1 else 'sell'),(sell_b,'sell' if side==1 else 'buy')]:
                    for label,value in charge(day,'Options',px,quantity,action).items():cost_parts[label]=cost_parts.get(label,0)+value
            fills.append({'contract':r['trading_symbol'],'key':r['instrument_key'],'side':side,'entry':buy_a,'exit':sell_b,'quantity':quantity})
        if reason:record_skip(skips,day,reason);continue
        charges=sum(cost_parts.values()) if use_upstox_fees else 2*len(legs)*order_fee+turnover*turnover_bps/10000
        trades.append({'Date':day,'Expiry':exp,'Spot signal':price,'Gross':gross,'Slippage':slippage,'Charges':charges,'Net':gross-slippage-charges,'Legs':json.dumps(fills),**cost_parts})
    return pd.DataFrame(trades),pd.DataFrame(skips)
