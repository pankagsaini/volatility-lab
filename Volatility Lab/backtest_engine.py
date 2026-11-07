"""Intraday backtest loader & simulator with fast multi-threaded LRU cache, archive zip streaming, and automatic Cloud monitor.sqlite fallback."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
import json, gzip, sqlite3, math, threading
from contextlib import closing
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from backtest_common import record_skip

ROOT = Path(__file__).resolve().parent / 'data/backtest_1minute'
DATA_DIR = Path(__file__).resolve().parent / 'data'
_CACHE_LOCK = threading.RLock()
_FILE_CACHE = OrderedDict()
_MAX_CACHE_FILES = 4096
_ZIP_HANDLE = None
_ZIP_LOCK = threading.Lock()


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


def _fallback_from_monitor_sqlite(symbol, start, end):
    """Build tradable intraday option contracts & 1-minute series from monitor.sqlite Cloud snapshots
    when the 200MB 1-minute backtest archive (`queue.sqlite`) is not present on Streamlit Cloud."""
    mon_db = DATA_DIR / 'monitor.sqlite'
    if not mon_db.exists():
        return [], {}, 0
    try:
        from scipy.special import ndtr
    except Exception:
        ndtr = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    contracts = {}
    series = {}
    spot_key = 'NSE_INDEX|Nifty 50' if symbol == 'NIFTY' else 'NSE_INDEX|Nifty Bank'
    step = 50 if symbol == 'NIFTY' else 100
    lot_size = 75 if symbol == 'NIFTY' else 30

    try:
        with closing(sqlite3.connect(mon_db, timeout=15)) as con:
            has_table = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='intraday_observations'").fetchone()
            if not has_table:
                return [], {}, 0
            rows = con.execute(
                "SELECT expiry, as_of, payload FROM intraday_observations WHERE symbol=? ORDER BY as_of",
                (symbol,),
            ).fetchall()
    except Exception:
        return [], {}, 0

    if not rows:
        return [], {}, 0

    by_day_exp = {}
    for exp, as_of, payload_str in rows:
        try:
            dt = pd.Timestamp(as_of)
            if dt.tzinfo is None:
                dt = dt.tz_localize('Asia/Kolkata')
            else:
                dt = dt.tz_convert('Asia/Kolkata')
            day_str = dt.strftime('%Y-%m-%d')
            if not (str(start) <= day_str <= str(end)):
                continue
            rec = json.loads(payload_str)
            spot = float(rec.get('Price') or 0.0)
            iv = float(rec.get('IV') or 0.0)
            if not (math.isfinite(spot) and spot > 0 and math.isfinite(iv) and iv > 0):
                continue
            by_day_exp.setdefault((day_str, str(exp)), []).append((dt, spot, iv, rec))
        except Exception:
            continue

    if not by_day_exp:
        return [], {}, 0

    rate = 0.065
    for (day_str, exp_str), snaps in by_day_exp.items():
        snaps.sort(key=lambda x: x[0])
        open_spot = snaps[0][1]
        atm_base = int(round(open_spot / step) * step)
        offsets = [-600, -550, -500, -450, -400, -350, -300, -250, -200, -150, -100, -50, 0, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 550, 600]
        strikes = sorted({max(step, atm_base + off) for off in offsets})

        for k in strikes:
            for kind in ('CE', 'PE'):
                ikey = f"CLOUD_OPT|{symbol}|{exp_str}|{int(k)}|{kind}"
                if ikey not in contracts:
                    contracts[ikey] = {
                        'instrument_key': ikey,
                        'trading_symbol': f"{symbol} {exp_str} {int(k)} {kind}",
                        'instrument_type': kind,
                        'strike_price': float(k),
                        'expiry': exp_str,
                        'lot_size': lot_size,
                    }

        # Populate minute grid from 09:15 to 15:30 IST using latest observed snapshot at or before each minute
        day_start = datetime.fromisoformat(f"{day_str} 09:15")
        day_end = datetime.fromisoformat(f"{day_str} 15:30")
        cur = day_start
        idx = 0
        try:
            dte_days = max(0.2, float((pd.Timestamp(exp_str).date() - pd.Timestamp(day_str).date()).days))
        except Exception:
            dte_days = 5.0
        T = dte_days / 365.0
        sqrt_T = math.sqrt(T)
        df_r = math.exp(-rate * T)

        while cur <= day_end:
            stamp_key = cur.strftime('%Y-%m-%d %H:%M')
            while idx + 1 < len(snaps) and snaps[idx + 1][0].strftime('%Y-%m-%d %H:%M') <= stamp_key:
                idx += 1
            _, spot_t, iv_t, rec_t = snaps[idx]
            series.setdefault(spot_key, {})[stamp_key] = [spot_t, spot_t, spot_t, spot_t, 1000]

            call_iv_base = float(rec_t.get('Call IV') or iv_t)
            put_iv_base = float(rec_t.get('Put IV') or iv_t)
            rev25 = float(rec_t.get('25 Delta reversal') or 0.0)
            if not math.isfinite(rev25):
                rev25 = 0.0

            for k in strikes:
                moneyness = (float(k) - spot_t) / spot_t
                for kind in ('CE', 'PE'):
                    base_v = call_iv_base if kind == 'CE' else put_iv_base
                    skew_adj = (-0.5 * rev25 * (moneyness / 0.02)) if kind == 'PE' and moneyness < 0 else (0.25 * abs(rev25) * abs(moneyness / 0.02))
                    sigma = max(0.04, (base_v + skew_adj) / 100.0)
                    d1 = (math.log(spot_t / float(k)) + (rate + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
                    d2 = d1 - sigma * sqrt_T
                    if kind == 'CE':
                        px = spot_t * float(ndtr(d1)) - float(k) * df_r * float(ndtr(d2))
                    else:
                        px = float(k) * df_r * float(ndtr(-d2)) - spot_t * float(ndtr(-d1))
                    px = max(0.5, round(px, 2))
                    ikey = f"CLOUD_OPT|{symbol}|{exp_str}|{int(k)}|{kind}"
                    series.setdefault(ikey, {})[stamp_key] = [px, px, px, px, 500]
            cur += timedelta(minutes=1)

    return list(contracts.values()), series, 0


def load(symbol, start, end):
    q_path = ROOT / 'queue.sqlite'
    if not q_path.exists() or q_path.stat().st_size == 0:
        _ensure_queue_sqlite()

    contracts = {}
    series = {}
    conflicts = set()

    if q_path.exists() and q_path.stat().st_size > 1000:
        try:
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

            for file, meta in contracts_jobs:
                try:
                    o = _read_gzip_json(file)
                    for r in o.get('data', []):
                        if r.get('instrument_type') in ('CE', 'PE'):
                            contracts[r['instrument_key']] = r
                except Exception:
                    continue

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
        except Exception:
            pass

    if contracts and series:
        return list(contracts.values()), series, len(conflicts)

    # Fallback to Cloud monitor.sqlite intraday snapshots if 1-minute queue.sqlite is absent or empty
    return _fallback_from_monitor_sqlite(symbol, start, end)


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
