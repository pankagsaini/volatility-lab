"""Intraday loader and simulator; sparse snapshots are marks only and cannot create executable minute fills."""
from volatility_lab.runtime.settings import data_dir as runtime_data_dir
from volatility_lab import PACKAGE_ROOT
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
import json, gzip, sqlite3, math, threading
from collections import OrderedDict
import pandas as pd
from backtest_common import record_skip

ROOT = runtime_data_dir() / 'backtest_1minute'
DATA_DIR = runtime_data_dir()
EXECUTION_MODEL_VERSION = 'v3_prior_minute_required'
_CACHE_LOCK = threading.RLock()
_FILE_CACHE = OrderedDict()
_MAX_CACHE_FILES = 128


def _read_gzip_json(path):
    path = Path(path)
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    with _CACHE_LOCK:
        if key in _FILE_CACHE:
            _FILE_CACHE.move_to_end(key)
            return _FILE_CACHE[key]
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        obj = json.load(f)
    with _CACHE_LOCK:
        _FILE_CACHE[key] = obj
        while len(_FILE_CACHE) > _MAX_CACHE_FILES:
            _FILE_CACHE.popitem(last=False)
    return obj


def _nontradable_mark_bar(price):
    """Represent an observed LTP as a mark only; snapshots lack minute volume and executable depth."""
    try:
        px = float(price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(px) or px <= 0:
        return None
    return [px, px, px, px, 0.0]


def _expected_trading_days(start, end, existing_days=()):
    """Return sorted list of NSE weekday trading dates in [start, end] unioned with any captured session dates."""
    try:
        from market_calendar import HOLIDAYS
    except Exception:
        HOLIDAYS = set()
    s_str, e_str = str(start)[:10], str(end)[:10]
    days = set(existing_days)
    try:
        for dt in pd.date_range(s_str, e_str):
            d_obj = dt.date()
            if d_obj.weekday() < 5 and d_obj not in HOLIDAYS:
                days.add(d_obj.strftime('%Y-%m-%d'))
    except Exception:
        pass
    return sorted(d for d in days if s_str <= d <= e_str)


def _fallback_from_monitor_sqlite(symbol, start, end):
    """Build non-executable marks ONLY from real captured intraday snapshots
    (>= 2 timestamps covering morning entry and afternoon exit) in data/google_archive or data/monitor.sqlite.
    Zero synthetic daily-OHLC-to-1-minute interpolation is performed."""
    mon_db = DATA_DIR / 'monitor.sqlite'
    archive_dir = DATA_DIR / 'google_archive'
    contracts = {}
    series = {}
    spot_key = 'NSE_INDEX|Nifty 50' if symbol == 'NIFTY' else 'NSE_INDEX|Nifty Bank'
    step = 50 if symbol == 'NIFTY' else 100
    lot_size = 65 if symbol == 'NIFTY' else 30
    start_s = str(start)[:10]
    end_s = str(end)[:10]

    # 1. First check if real Upstox .json.gz snapshots exist in data/google_archive for sessions in [start, end]
    archive_by_day = {}
    if archive_dir.is_dir():
        for gz_path in sorted(archive_dir.glob('*_snapshot_*_complete.json.gz')):
            day_prefix = gz_path.name[:10]
            if start_s <= day_prefix <= end_s:
                archive_by_day.setdefault(day_prefix, []).append(gz_path)

    for day_str, gz_list in sorted(archive_by_day.items()):
        if len(gz_list) < 2:
            continue
        parsed_snaps = []
        for gz_p in gz_list:
            try:
                obj = _read_gzip_json(gz_p)
                ts = pd.Timestamp(obj['retrieved_at']).tz_convert('Asia/Kolkata')
                if ts.strftime('%Y-%m-%d') == day_str:
                    parsed_snaps.append((ts, obj))
            except Exception:
                continue
        if len(parsed_snaps) < 2:
            continue
        parsed_snaps.sort(key=lambda x: x[0])
        # Populate real captured option prices from the snapshots across the session minutes
        for ts, obj in parsed_snaps:
            stamp_key = ts.strftime('%Y-%m-%d %H:%M')
            for rec in obj.get('records', []):
                lbl = rec.get('label', '')
                if not lbl.startswith(symbol + '|') or not isinstance(rec.get('data'), list) or not rec['data']:
                    continue
                exp_str = lbl.split('|', 1)[1][:10]
                raw_chain = rec['data']
                spot_px = float(raw_chain[0].get('underlying_spot_price') or 0.0)
                if spot_px > 0:
                    spot_mark = _nontradable_mark_bar(spot_px)
                    if spot_mark is not None:
                        series.setdefault(spot_key, {})[stamp_key] = spot_mark
                for row in raw_chain:
                    k = int(round(float(row['strike_price'])))
                    for kind, opt_key in (('CE', 'call_options'), ('PE', 'put_options')):
                        opt = row.get(opt_key) or {}
                        md = opt.get('market_data') or {}
                        ltp = float(md.get('ltp') or 0.0)
                        if ltp > 0:
                            ikey = f"UPSTOX_OPT|{symbol}|{exp_str}|{k}|{kind}"
                            if ikey not in contracts:
                                contracts[ikey] = {
                                    'instrument_key': ikey,
                                    'trading_symbol': f"{symbol} {exp_str} {k} {kind}",
                                    'instrument_type': kind,
                                    'strike_price': float(k),
                                    'expiry': exp_str,
                                    'lot_size': lot_size,
                                }
                            # Snapshot volume is cumulative/session-level, not minute execution volume.
                            option_mark = _nontradable_mark_bar(ltp)
                            if option_mark is not None:
                                series.setdefault(ikey, {})[stamp_key] = option_mark

    # 2. Check intraday_observations in monitor.sqlite ONLY for sessions with >= 2 real intraday timestamps
    by_day_exp = {}
    if mon_db.exists():
        try:
            with sqlite3.connect(mon_db, timeout=15) as con:
                has_intra = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='intraday_observations'").fetchone()
                if has_intra:
                    rows = con.execute(
                        "SELECT expiry, as_of, payload FROM intraday_observations WHERE symbol=? AND substr(as_of, 1, 10) >= ? AND substr(as_of, 1, 10) <= ? ORDER BY as_of",
                        (symbol, start_s, end_s),
                    ).fetchall()
                    for exp, as_of, payload_str in rows:
                        try:
                            dt = pd.Timestamp(as_of)
                            dt = dt.tz_localize('Asia/Kolkata') if dt.tzinfo is None else dt.tz_convert('Asia/Kolkata')
                            day_str = dt.strftime('%Y-%m-%d')
                            if not (start_s <= day_str <= end_s) or day_str in archive_by_day:
                                continue
                            rec = json.loads(payload_str)
                            spot = float(rec.get('Price') or 0.0)
                            iv = float(rec.get('IV') or 0.0)
                            if math.isfinite(spot) and spot > 0 and math.isfinite(iv) and iv > 0:
                                by_day_exp.setdefault((day_str, str(exp)[:10]), []).append((dt, spot, iv, rec))
                        except Exception:
                            continue
        except Exception:
            pass

    # Filter strictly to (day_str, exp_str) pairs that have >= 2 real captured intraday observations
    by_day_exp = {k: v for k, v in by_day_exp.items() if len(v) >= 2}

    for (day_str, exp_str), snaps in by_day_exp.items():
        snaps.sort(key=lambda x: x[0])
        open_spot = snaps[0][1]
        atm_base = int(round(open_spot / step) * step)
        offsets = [i * step for i in range(-12, 13)]
        strikes = sorted({max(step, atm_base + off) for off in offsets})

        # Check if real chain_quotes are embedded in payload (>= 2 intraday snapshots with real chain quotes)
        cq_snaps = [s for s in snaps if isinstance(s[3].get('chain_quotes'), dict) and s[3]['chain_quotes']]
        has_real_chain_quotes = len(cq_snaps) >= 2
        if has_real_chain_quotes:
            snaps = cq_snaps
            all_ks = sorted({int(k_s) for s in snaps for k_s in s[3]['chain_quotes'].keys()})
            if all_ks:
                strikes = all_ks

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

        # Keep only observed timestamps; never extrapolate or interpolate quote bars.
        anchors_by_minute = {s[0].strftime('%Y-%m-%d %H:%M'): s for s in snaps}
        for stamp_key in sorted(anchors_by_minute):
            _timestamp, spot_px, _iv_px, rec = anchors_by_minute[stamp_key]
            spot_mark = _nontradable_mark_bar(spot_px)
            if spot_mark is not None:
                series.setdefault(spot_key, {})[stamp_key] = spot_mark
            if not has_real_chain_quotes:
                continue
            quotes = rec.get('chain_quotes', {})
            for k in strikes:
                quote = quotes.get(str(int(k)), {})
                for kind in ('CE', 'PE'):
                    px = float(quote.get(kind) or 0.0)
                    if px > 0 and math.isfinite(px):
                        ikey = f"CLOUD_OPT|{symbol}|{exp_str}|{int(k)}|{kind}"
                        # Captured LTPs have no minute execution volume or order-book depth.
                        option_mark = _nontradable_mark_bar(px)
                        if option_mark is not None:
                            series.setdefault(ikey, {})[stamp_key] = option_mark

    return list(contracts.values()), series, 0


def load(symbol, start, end):
    q_path = ROOT / 'queue.sqlite'
    contracts = {}
    series = {}
    conflicts = set()
    if q_path.exists():
        try:
            with sqlite3.connect(q_path, timeout=30) as con:
                has_jobs = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
                jobs = con.execute('SELECT kind,meta,file FROM jobs WHERE state="done" AND kind IN ("contracts","minute")').fetchall() if has_jobs else []
            for kind, metastr, file in jobs:
                meta = json.loads(metastr)
                if meta.get('symbol') != symbol:
                    continue
                if kind == 'minute' and (meta.get('end', '9999') < str(start) or meta.get('start', '0000') > str(end)):
                    continue
                gz_file = ROOT / file
                if not gz_file.exists():
                    continue
                o = _read_gzip_json(gz_file)
                if kind == 'contracts':
                    for r in o.get('data', []):
                        if r.get('instrument_type') in ('CE', 'PE'):
                            contracts[r['instrument_key']] = r
                else:
                    key = meta['key']
                    dest = series.setdefault(key, {})
                    for row in o.get('data', {}).get('candles', []):
                        dt = datetime.fromisoformat(row[0])
                        if dt.tzinfo is None:
                            continue
                        stamp = dt.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime('%Y-%m-%d %H:%M')
                        if not str(start) <= stamp[:10] <= str(end):
                            continue
                        if stamp in dest and dest[stamp] != row[1:]:
                            conflicts.add((key, stamp))
                        dest[stamp] = row[1:]
            for key, stamp in conflicts:
                series[key].pop(stamp, None)
        except Exception:
            pass

    spot_key = 'NSE_INDEX|Nifty 50' if symbol == 'NIFTY' else 'NSE_INDEX|Nifty Bank'
    series = {k: v for k, v in series.items() if v}
    if contracts and series.get(spot_key):
        return list(contracts.values()), series, len(conflicts)

    return _fallback_from_monitor_sqlite(symbol, start, end)


def simulate(contracts,series,spotkey,start,end,entry='09:20',exit='15:15',offset=0,wings=0,quantity=1,slip_bps=5,order_fee=25,turnover_bps=0,min_dte=1,max_dte=35,use_upstox_fees=True,signal_rows=None,filters=()):
 if entry>=exit or not 0<=slip_bps<10000 or quantity<=0 or offset<0 or wings<0:raise ValueError('Invalid strategy settings')
 trades=[];skips=[];spot=series.get(spotkey,{})
 captured_days={s[:10] for s in spot if str(start)<=s[:10]<=str(end)}
 days=_expected_trading_days(start,end,captured_days)
 for day in days:
  if day not in captured_days:
   record_skip(skips,day,'No real captured intraday option data for session');continue
  decision=(datetime.fromisoformat(day+' '+entry)-timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M')
  bar=spot.get(decision)
  try:
   price=float(bar[3])
  except (IndexError, KeyError, TypeError, ValueError, OverflowError):
   price=math.nan
  if not math.isfinite(price) or price<=0:record_skip(skips,day,'Missing preceding spot minute');continue
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
    raw_a=float(a[0]);raw_b=float(b[0]);volume_a=float(a[4]);volume_b=float(b[4])
    if not all(math.isfinite(x) and x>0 for x in (raw_a,raw_b)):raise ValueError()
    if not all(math.isfinite(x) and x>=quantity for x in (volume_a,volume_b)):
     reason='Insufficient traded minute volume';break
   except (ValueError,TypeError,IndexError):reason='Invalid price or missing minute volume';break
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
  trades.append({'Date':day,'Expiry':exp,'Spot signal':price,'Gross':gross,'Slippage':slippage,'Charges':charges,'Net':gross-slippage-charges,'Legs':json.dumps(fills),'Execution model':EXECUTION_MODEL_VERSION,'Fee model':'upstox_statutory' if use_upstox_fees else 'legacy_order_fee',**cost_parts})
 return pd.DataFrame(trades),pd.DataFrame(skips)
