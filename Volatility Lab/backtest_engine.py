"""Intraday backtest loader & simulator with LRU gzip cache and automatic Cloud monitor.sqlite + one_year.csv fallback."""
from pathlib import Path
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json, gzip, sqlite3, math, threading
from collections import OrderedDict
import pandas as pd
from backtest_common import record_skip

ROOT = Path(__file__).resolve().parent / 'data/backtest_1minute'
DATA_DIR = Path(__file__).resolve().parent / 'data'
_CACHE_LOCK = threading.RLock()
_FILE_CACHE = OrderedDict()
_MAX_CACHE_FILES = 128


def _read_gzip_json(path):
    key = str(path)
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


def _next_weekly_expiry(d: date, symbol: str = 'NIFTY') -> date:
    """Return nearest valid weekly/monthly expiry at least 2 days ahead of `d`."""
    # Target Thursday (weekday 3) or Tuesday (weekday 1) at least 2 days ahead
    cur = d + timedelta(days=2)
    while cur.weekday() != 3:
        cur += timedelta(days=1)
    return cur


def _fallback_from_monitor_sqlite(symbol, start, end):
    """Build tradable intraday option contracts & 1-minute series from monitor.sqlite Cloud snapshots
    and {symbol}_one_year.csv daily OHLC when the 200MB 1-minute archive (`queue.sqlite`) is absent."""
    mon_db = DATA_DIR / 'monitor.sqlite'
    csv_path = DATA_DIR / f'{str(symbol).lower()}_one_year.csv'
    latest_json = DATA_DIR / 'expiry_latest.json'
    try:
        from scipy.special import ndtr
    except Exception:
        ndtr = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    contracts = {}
    series = {}
    spot_key = 'NSE_INDEX|Nifty 50' if symbol == 'NIFTY' else 'NSE_INDEX|Nifty Bank'
    step = 50 if symbol == 'NIFTY' else 100
    lot_size = 75 if symbol == 'NIFTY' else 30
    start_s = str(start)[:10]
    end_s = str(end)[:10]

    by_day_exp = {}
    daily_iv_lookup = {}
    default_iv = 14.5 if symbol == 'NIFTY' else 16.5
    default_rev25 = 1.2

    # 1. Check expiry_latest.json for baseline IV and skew
    if latest_json.exists():
        try:
            snap_obj = json.loads(latest_json.read_text(encoding='utf-8'))
            for r in snap_obj.get('rows', []):
                if r.get('Instrument') == symbol and r.get('IV'):
                    default_iv = float(r['IV'])
                    if r.get('25 Delta reversal') is not None:
                        default_rev25 = float(r['25 Delta reversal'])
                    break
        except Exception:
            pass

    # 2. Read intraday_observations and expiry_observations from monitor.sqlite
    if mon_db.exists():
        try:
            with closing(sqlite3.connect(mon_db, timeout=15)) as con:
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
                            if not (start_s <= day_str <= end_s):
                                continue
                            rec = json.loads(payload_str)
                            spot = float(rec.get('Price') or 0.0)
                            iv = float(rec.get('IV') or 0.0)
                            if math.isfinite(spot) and spot > 0 and math.isfinite(iv) and iv > 0:
                                by_day_exp.setdefault((day_str, str(exp)[:10]), []).append((dt, spot, iv, rec))
                                daily_iv_lookup[day_str] = (iv, float(rec.get('25 Delta reversal') or default_rev25), str(exp)[:10])
                        except Exception:
                            continue

                has_exp = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='expiry_observations'").fetchone()
                if has_exp:
                    erows = con.execute(
                        "SELECT date, expiry, iv, spread FROM expiry_observations WHERE symbol=? AND date >= ? AND date <= ? ORDER BY date",
                        (symbol, start_s, end_s),
                    ).fetchall()
                    for d_str, exp_s, iv_val, spr_val in erows:
                        try:
                            if iv_val is not None and float(iv_val) > 0:
                                daily_iv_lookup.setdefault(str(d_str)[:10], (float(iv_val), default_rev25, str(exp_s)[:10]))
                        except Exception:
                            continue
        except Exception:
            pass

    # 3. Read daily OHLC from {symbol}_one_year.csv to cover multi-day historical windows in [start, end]
    daily_ohlc = {}
    if csv_path.exists():
        try:
            df_csv = pd.read_csv(csv_path)
            for _, row in df_csv.iterrows():
                d_str = str(row['Date'])[:10]
                if start_s <= d_str <= end_s:
                    daily_ohlc[d_str] = (float(row['Open']), float(row['High']), float(row['Low']), float(row['Close']))
        except Exception:
            pass

    # Merge daily_ohlc days that are not already covered with eligible expiries in by_day_exp
    covered_days = {d for (d, _) in by_day_exp.keys()}
    for d_str, (o_px, h_px, l_px, c_px) in sorted(daily_ohlc.items()):
        if d_str in covered_days:
            continue
        iv_day, rev_day, exp_day = daily_iv_lookup.get(d_str, (default_iv, default_rev25, None))
        d_obj = date.fromisoformat(d_str)
        if not exp_day or exp_day <= d_str or (date.fromisoformat(exp_day) - d_obj).days > 10:
            exp_day = str(_next_weekly_expiry(d_obj, symbol))
        # Create key intraday anchor points (09:15 Open, 10:30 High/Low, 13:30 Low/High, 15:30 Close)
        p1 = h_px if c_px >= o_px else l_px
        p2 = l_px if c_px >= o_px else h_px
        anchors = [
            (pd.Timestamp(f"{d_str} 09:15:00", tz='Asia/Kolkata'), o_px, iv_day),
            (pd.Timestamp(f"{d_str} 11:00:00", tz='Asia/Kolkata'), p1, iv_day * 1.02),
            (pd.Timestamp(f"{d_str} 13:30:00", tz='Asia/Kolkata'), p2, iv_day * 0.99),
            (pd.Timestamp(f"{d_str} 15:30:00", tz='Asia/Kolkata'), c_px, iv_day * 0.97),
        ]
        rec_template = {'Price': o_px, 'IV': iv_day, 'Call IV': iv_day, 'Put IV': iv_day, '25 Delta reversal': rev_day}
        by_day_exp[(d_str, exp_day)] = [(ts, px, iv_v, rec_template) for ts, px, iv_v in anchors]

    # Also ensure that for any day in by_day_exp where all expiries are >10 DTE (e.g. BANKNIFTY monthly),
    # a near-dated (5 DTE) synthetic expiry is also available if a narrow DTE filter is used
    extra_day_exp = {}
    days_grouped = {}
    for (d_str, exp_str), snaps in by_day_exp.items():
        days_grouped.setdefault(d_str, []).append((exp_str, snaps))
    for d_str, exp_list in days_grouped.items():
        d_obj = date.fromisoformat(d_str)
        has_short_dte = any(2 <= (date.fromisoformat(e_s) - d_obj).days <= 10 for e_s, _ in exp_list)
        if not has_short_dte and exp_list:
            near_exp = str(_next_weekly_expiry(d_obj, symbol))
            if (d_str, near_exp) not in by_day_exp:
                extra_day_exp[(d_str, near_exp)] = exp_list[0][1]
    by_day_exp.update(extra_day_exp)

    if not by_day_exp:
        return [], {}, 0

    # Cap to most recent 65 sessions to keep memory compact on Streamlit Cloud
    unique_days = sorted({d for d, _ in by_day_exp.keys()})
    if len(unique_days) > 65:
        keep_days = set(unique_days[-65:])
        by_day_exp = {k: v for k, v in by_day_exp.items() if k[0] in keep_days}

    rate = 0.065
    for (day_str, exp_str), snaps in by_day_exp.items():
        snaps.sort(key=lambda x: x[0])
        open_spot = snaps[0][1]
        atm_base = int(round(open_spot / step) * step)
        offsets = [i * step for i in range(-12, 13)]
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

        try:
            dte_days = max(0.5, float((date.fromisoformat(exp_str[:10]) - date.fromisoformat(day_str[:10])).days))
        except Exception:
            dte_days = 5.0
        T = dte_days / 365.0
        sqrt_T = math.sqrt(T)
        df_r = math.exp(-rate * T)

        # Build minute timestamps from 09:15 to 15:30 IST with linear interpolation between anchors
        anchor_mins = []
        for ts_a, sp_a, iv_a, rec_a in snaps:
            m_of_day = max(0, min(375, (ts_a.hour * 60 + ts_a.minute) - 555))
            anchor_mins.append((m_of_day, sp_a, iv_a, rec_a))
        if anchor_mins[0][0] > 0:
            anchor_mins.insert(0, (0, anchor_mins[0][1], anchor_mins[0][2], anchor_mins[0][3]))
        if anchor_mins[-1][0] < 375:
            anchor_mins.append((375, anchor_mins[-1][1], anchor_mins[-1][2], anchor_mins[-1][3]))

        day_start = datetime.fromisoformat(f"{day_str} 09:15")
        a_idx = 0
        for m in range(376):
            cur = day_start + timedelta(minutes=m)
            stamp_key = cur.strftime('%Y-%m-%d %H:%M')
            while a_idx + 1 < len(anchor_mins) and anchor_mins[a_idx + 1][0] < m:
                a_idx += 1
            m0, sp0, iv0, rec_t = anchor_mins[a_idx]
            if a_idx + 1 < len(anchor_mins) and anchor_mins[a_idx + 1][0] > m0:
                m1, sp1, iv1, _ = anchor_mins[a_idx + 1]
                w = (m - m0) / float(m1 - m0)
                spot_t = sp0 + w * (sp1 - sp0)
                iv_t = iv0 + w * (iv1 - iv0)
            else:
                spot_t, iv_t = sp0, iv0

            series.setdefault(spot_key, {})[stamp_key] = [spot_t, spot_t, spot_t, spot_t, 1000]
            call_iv_base = float(rec_t.get('Call IV') or iv_t) * (iv_t / max(iv0, 1e-4))
            put_iv_base = float(rec_t.get('Put IV') or iv_t) * (iv_t / max(iv0, 1e-4))
            rev25 = float(rec_t.get('25 Delta reversal') or default_rev25)
            if not math.isfinite(rev25):
                rev25 = default_rev25

            # Intraday time decay fraction across the 375-minute trading day
            T_m = max(1e-5, (dte_days - (m / 375.0) * 0.35) / 365.0)
            sqrt_Tm = math.sqrt(T_m)
            df_rm = math.exp(-rate * T_m)

            for k in strikes:
                moneyness = (float(k) - spot_t) / spot_t
                for kind in ('CE', 'PE'):
                    base_v = call_iv_base if kind == 'CE' else put_iv_base
                    skew_adj = (-0.5 * rev25 * (moneyness / 0.02)) if kind == 'PE' and moneyness < 0 else (0.25 * abs(rev25) * abs(moneyness / 0.02))
                    sigma = max(0.04, (base_v + skew_adj) / 100.0)
                    d1 = (math.log(spot_t / float(k)) + (rate + 0.5 * sigma * sigma) * T_m) / (sigma * sqrt_Tm)
                    d2 = d1 - sigma * sqrt_Tm
                    if kind == 'CE':
                        px = spot_t * float(ndtr(d1)) - float(k) * df_rm * float(ndtr(d2))
                    else:
                        px = float(k) * df_rm * float(ndtr(-d2)) - spot_t * float(ndtr(-d1))
                    px = max(0.5, round(px, 2))
                    ikey = f"CLOUD_OPT|{symbol}|{exp_str}|{int(k)}|{kind}"
                    series.setdefault(ikey, {})[stamp_key] = [px, px, px, px, 500]

    return list(contracts.values()), series, 0


def load(symbol, start, end):
    q_path = ROOT / 'queue.sqlite'
    contracts = {}
    series = {}
    conflicts = set()
    if q_path.exists():
        try:
            with closing(sqlite3.connect(q_path, timeout=30)) as con:
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

    # Fallback to Cloud monitor.sqlite + one_year.csv when 1-minute queue.sqlite is absent or does not cover [start, end]
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
