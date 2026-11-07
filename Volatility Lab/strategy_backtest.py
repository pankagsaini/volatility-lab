import numpy as np
"""Multi-leg options strategy backtest engine with SOM margin floor, quiet-minute carry-forward, and 6 Trade Scenario Presets."""
from datetime import datetime, timedelta
import json
import math
import pandas as pd
from backtest_common import record_skip, dated_lot_size
from broker_charges import charge

TEMPLATES = {
    'Short straddle': [
        {'Side': 'Sell', 'Type': 'CE', 'Offset': 0, 'Ratio': 1, 'kind': 'CE', 'side': -1, 'offset': 0, 'ratio': 1},
        {'Side': 'Sell', 'Type': 'PE', 'Offset': 0, 'Ratio': 1, 'kind': 'PE', 'side': -1, 'offset': 0, 'ratio': 1},
    ],
    'Short strangle': [
        {'Side': 'Sell', 'Type': 'CE', 'Offset': 200, 'Ratio': 1, 'kind': 'CE', 'side': -1, 'offset': 200, 'ratio': 1},
        {'Side': 'Sell', 'Type': 'PE', 'Offset': 200, 'Ratio': 1, 'kind': 'PE', 'side': -1, 'offset': 200, 'ratio': 1},
    ],
    'Iron butterfly': [
        {'Side': 'Sell', 'Type': 'CE', 'Offset': 0, 'Ratio': 1, 'kind': 'CE', 'side': -1, 'offset': 0, 'ratio': 1},
        {'Side': 'Sell', 'Type': 'PE', 'Offset': 0, 'Ratio': 1, 'kind': 'PE', 'side': -1, 'offset': 0, 'ratio': 1},
        {'Side': 'Buy', 'Type': 'CE', 'Offset': 300, 'Ratio': 1, 'kind': 'CE', 'side': 1, 'offset': 300, 'ratio': 1},
        {'Side': 'Buy', 'Type': 'PE', 'Offset': 300, 'Ratio': 1, 'kind': 'PE', 'side': 1, 'offset': 300, 'ratio': 1},
    ],
    'Iron condor': [
        {'Side': 'Sell', 'Type': 'CE', 'Offset': 200, 'Ratio': 1, 'kind': 'CE', 'side': -1, 'offset': 200, 'ratio': 1},
        {'Side': 'Sell', 'Type': 'PE', 'Offset': 200, 'Ratio': 1, 'kind': 'PE', 'side': -1, 'offset': 200, 'ratio': 1},
        {'Side': 'Buy', 'Type': 'CE', 'Offset': 500, 'Ratio': 1, 'kind': 'CE', 'side': 1, 'offset': 500, 'ratio': 1},
        {'Side': 'Buy', 'Type': 'PE', 'Offset': 500, 'Ratio': 1, 'kind': 'PE', 'side': 1, 'offset': 500, 'ratio': 1},
    ],
    'Scenario 1 · VRP Harvest Iron Condor (Calm HV)': [
        {'Side': 'Sell', 'Type': 'CE', 'Offset': 200, 'Ratio': 1, 'kind': 'CE', 'side': -1, 'offset': 200, 'ratio': 1},
        {'Side': 'Sell', 'Type': 'PE', 'Offset': 200, 'Ratio': 1, 'kind': 'PE', 'side': -1, 'offset': 200, 'ratio': 1},
        {'Side': 'Buy', 'Type': 'CE', 'Offset': 500, 'Ratio': 1, 'kind': 'CE', 'side': 1, 'offset': 500, 'ratio': 1},
        {'Side': 'Buy', 'Type': 'PE', 'Offset': 500, 'Ratio': 1, 'kind': 'PE', 'side': 1, 'offset': 500, 'ratio': 1},
    ],
    'Scenario 2 · Gamma Expansion Long Straddle (Compressed IV)': [
        {'Side': 'Buy', 'Type': 'CE', 'Offset': 0, 'Ratio': 1, 'kind': 'CE', 'side': 1, 'offset': 0, 'ratio': 1},
        {'Side': 'Buy', 'Type': 'PE', 'Offset': 0, 'Ratio': 1, 'kind': 'PE', 'side': 1, 'offset': 0, 'ratio': 1},
    ],
    'Scenario 3 · Post-Event Front-Week Crush (Iron Butterfly)': [
        {'Side': 'Sell', 'Type': 'CE', 'Offset': 0, 'Ratio': 1, 'kind': 'CE', 'side': -1, 'offset': 0, 'ratio': 1},
        {'Side': 'Sell', 'Type': 'PE', 'Offset': 0, 'Ratio': 1, 'kind': 'PE', 'side': -1, 'offset': 0, 'ratio': 1},
        {'Side': 'Buy', 'Type': 'CE', 'Offset': 350, 'Ratio': 1, 'kind': 'CE', 'side': 1, 'offset': 350, 'ratio': 1},
        {'Side': 'Buy', 'Type': 'PE', 'Offset': 350, 'Ratio': 1, 'kind': 'PE', 'side': 1, 'offset': 350, 'ratio': 1},
    ],
    'Scenario 4 · Steep Put Skew Ratio Spread (RR25 ≥ +2.5 pp)': [
        {'Side': 'Sell', 'Type': 'PE', 'Offset': 150, 'Ratio': 2, 'kind': 'PE', 'side': -1, 'offset': 150, 'ratio': 2},
        {'Side': 'Buy', 'Type': 'PE', 'Offset': 450, 'Ratio': 2, 'kind': 'PE', 'side': 1, 'offset': 450, 'ratio': 2},
        {'Side': 'Sell', 'Type': 'CE', 'Offset': 250, 'Ratio': 1, 'kind': 'CE', 'side': -1, 'offset': 250, 'ratio': 1},
        {'Side': 'Buy', 'Type': 'CE', 'Offset': 550, 'Ratio': 1, 'kind': 'CE', 'side': 1, 'offset': 550, 'ratio': 1},
    ],
    'Scenario 5 · Intraday Theta Decay Iron Butterfly (09:20–15:15)': [
        {'Side': 'Sell', 'Type': 'CE', 'Offset': 0, 'Ratio': 1, 'kind': 'CE', 'side': -1, 'offset': 0, 'ratio': 1},
        {'Side': 'Sell', 'Type': 'PE', 'Offset': 0, 'Ratio': 1, 'kind': 'PE', 'side': -1, 'offset': 0, 'ratio': 1},
        {'Side': 'Buy', 'Type': 'CE', 'Offset': 250, 'Ratio': 1, 'kind': 'CE', 'side': 1, 'offset': 250, 'ratio': 1},
        {'Side': 'Buy', 'Type': 'PE', 'Offset': 250, 'Ratio': 1, 'kind': 'PE', 'side': 1, 'offset': 250, 'ratio': 1},
    ],
    'Scenario 6 · Synthetic Basis Skewed Condor (Call-Rich Parity)': [
        {'Side': 'Sell', 'Type': 'CE', 'Offset': 150, 'Ratio': 2, 'kind': 'CE', 'side': -1, 'offset': 150, 'ratio': 2},
        {'Side': 'Buy', 'Type': 'CE', 'Offset': 450, 'Ratio': 2, 'kind': 'CE', 'side': 1, 'offset': 450, 'ratio': 2},
        {'Side': 'Sell', 'Type': 'PE', 'Offset': 250, 'Ratio': 1, 'kind': 'PE', 'side': -1, 'offset': 250, 'ratio': 1},
        {'Side': 'Buy', 'Type': 'PE', 'Offset': 550, 'Ratio': 1, 'kind': 'PE', 'side': 1, 'offset': 550, 'ratio': 1},
    ],
}

SCENARIO_PRESETS = {
    'Scenario 1 · VRP Harvest Iron Condor (Calm HV)': {
        'hypothesis': 'Sell defined-risk wings when Volatility Radar shows RICH VOLATILITY (IV-RV Spread ≥ +2.0 pp) while trailing HV30 sits in a calm/normal regime (8%–16%) and IV trades above GARCH FRV.',
        'streamlit_triggers': '◈ Volatility Radar: RICH VOLATILITY · Vol Regime: IV-RV Spread ≥ +2.0 pp & DTE Rank ≥ 75%ile · Term Structure: IV - GARCH FRV ≥ +2.0 pp · Parity Normal',
        'entry': '09:20',
        'exit': '15:15',
        'dte': (2, 10),
        'expiry_rank': 'Nearest',
        'bias': 'Neutral',
        'stop': 3000.0,
        'target': 4500.0,
        'filters': [('IV-RV Spread', 2.0, 15.0), ('HV30', 8.0, 16.0)],
    },
    'Scenario 2 · Gamma Expansion Long Straddle (Compressed IV)': {
        'hypothesis': 'Buy ATM gamma convexity when Volatility Radar shows COMPRESSED VOLATILITY (IV-RV Spread ≤ -1.5 pp) and Term Structure shows IV discounted below the GARCH/EWMA forward variance floor.',
        'streamlit_triggers': '◈ Volatility Radar: COMPRESSED VOLATILITY · Term Structure: IV - GARCH FRV ≤ -2.0 pp · Intraday Straddle: Breakeven ±% < 1.15%',
        'entry': '09:25',
        'exit': '15:15',
        'dte': (3, 7),
        'expiry_rank': 'Nearest',
        'bias': 'Neutral',
        'stop': 2200.0,
        'target': 5500.0,
        'filters': [('IV-RV Spread', -15.0, -1.5)],
    },
    'Scenario 3 · Post-Event Front-Week Crush (Iron Butterfly)': {
        'hypothesis': 'Capture rapid front-week IV mean-reversion when a scheduled macro/earnings catalyst inflates the nearest weekly expiry ≥ +3.0 pp above HV30 and Next-Week IV (backwardation kink).',
        'streamlit_triggers': 'Term Structure & Calendars: Front IV > Next IV by +2.5 pp & IV - GARCH FRV ≥ +3.0 pp · Macro Event Calendar: Catalyst listed before front expiry',
        'entry': '09:30',
        'exit': '15:15',
        'dte': (1, 5),
        'expiry_rank': 'Nearest',
        'bias': 'Neutral',
        'stop': 3500.0,
        'target': 5000.0,
        'filters': [('IV-RV Spread', 3.0, 20.0)],
    },
    'Scenario 4 · Steep Put Skew Ratio Spread (RR25 ≥ +2.5 pp)': {
        'hypothesis': 'Monetize overpriced 25-delta put protection when Smile & Skew shows RR25 risk reversal ≥ +2.50 pp (≥85th percentile) by selling 2x 25Δ puts against 1x near-ATM put and 1x tail-hedge wing.',
        'streamlit_triggers': 'Smile & Skew: RR25 risk reversal ≥ +2.50 pp · Vol Regime & Maturity Ranking: 25 Delta reversal ≥ 85%ile',
        'entry': '09:20',
        'exit': '15:15',
        'dte': (3, 14),
        'expiry_rank': 'Nearest',
        'bias': 'Bullish',
        'stop': 2800.0,
        'target': 4200.0,
        'filters': [('25 Delta reversal', 2.5, 12.0), ('IV-RV Spread', 1.0, 15.0)],
    },
    'Scenario 5 · Intraday Theta Decay Iron Butterfly (09:20–15:15)': {
        'hypothesis': 'Harvest intraday theta and post-open vega compression when 1-minute Cloud captures show ATM Straddle Price drifting below the 09:20 IST open and Net Delta bounded within [-0.12, +0.12].',
        'streamlit_triggers': 'Intraday Straddle & Greeks: Straddle Price (₹) below opening high · Implied Volatility (%) flat/falling · |Net Delta| ≤ 0.12',
        'entry': '09:20',
        'exit': '15:15',
        'dte': (1, 6),
        'expiry_rank': 'Nearest',
        'bias': 'Neutral',
        'stop': 2500.0,
        'target': 4000.0,
        'filters': [('IV-RV Spread', 1.0, 15.0)],
    },
    'Scenario 6 · Synthetic Basis Skewed Condor (Call-Rich Parity)': {
        'hypothesis': 'Use Put-Call Parity Basis (Syn - Month Future LTP) as an execution gate and skew short call strikes closer (+150) and put strikes wider (-250) when call demand inflates IV alongside positive VRP.',
        'streamlit_triggers': '◈ Volatility Radar: Parity Normal (Wait if ⚠️ Basis Dislocation > ±30 pts NIFTY / ±75 pts BANKNIFTY) · Term Structure: ATM synthetic future vs Month future LTP',
        'entry': '09:25',
        'exit': '15:15',
        'dte': (2, 12),
        'expiry_rank': 'Nearest',
        'bias': 'Bearish',
        'stop': 2800.0,
        'target': 4200.0,
        'filters': [('IV-RV Spread', 1.5, 15.0), ('25 Delta reversal', -6.0, 0.5)],
    },
}


def _normalize_leg(leg: dict) -> dict:
    """Ensure every leg dict carries both UI keys (Side/Type/Offset/Ratio) and engine keys (side/kind/offset/ratio)."""
    if 'Side' in leg and 'side' not in leg:
        side_num = 1 if str(leg['Side']).strip().lower() == 'buy' else -1
        side_str = 'Buy' if side_num > 0 else 'Sell'
    else:
        side_raw = leg.get('side', -1)
        if isinstance(side_raw, str):
            side_num = 1 if side_raw.strip().lower() == 'buy' else -1
        else:
            side_num = 1 if int(side_raw) > 0 else -1
        side_str = leg.get('Side', 'Buy' if side_num > 0 else 'Sell')
    kind_str = str(leg.get('kind') or leg.get('Type') or 'CE').upper()
    offset_val = float(leg.get('offset', leg.get('Offset', 0)))
    ratio_val = int(leg.get('ratio', leg.get('Ratio', 1)))
    return {
        'Side': side_str,
        'Type': kind_str,
        'Offset': int(offset_val) if offset_val == int(offset_val) else offset_val,
        'Ratio': ratio_val,
        'kind': kind_str,
        'side': side_num,
        'offset': offset_val,
        'ratio': ratio_val,
    }


def template(name):
    if name not in TEMPLATES:
        raise ValueError(f'Unsupported strategy template: {name!r}')
    return [_normalize_leg(leg) for leg in TEMPLATES[name]]


def scenario_preset(name):
    """Return scenario metadata + default filter/timing configuration when `name` is one of the 6 trade scenarios."""
    return dict(SCENARIO_PRESETS[name]) if name in SCENARIO_PRESETS else None


def validate(settings: dict) -> bool:
    """Validate options strategy builder settings before submitting to background job worker."""
    if not isinstance(settings, dict):
        raise ValueError('Settings must be a dictionary.')
    start = str(settings.get('start', ''))
    end = str(settings.get('end', ''))
    if not start or not end or start > end:
        raise ValueError('Choose From <= To date range.')
    entry = str(settings.get('entry', '09:20'))
    exit_t = str(settings.get('exit', '15:15'))
    if entry >= exit_t:
        raise ValueError('Entry time must be earlier than Exit time.')
    if int(settings.get('lots', 1)) < 1:
        raise ValueError('Strategy lots must be at least 1.')
    dte = settings.get('dte', (1, 35))
    if isinstance(dte, (list, tuple)) and len(dte) == 2 and dte[0] > dte[1]:
        raise ValueError('Minimum DTE cannot exceed maximum DTE.')
    return True


def valid_price(bar, index=0, fill=False):
    if bar is None or len(bar) <= max(index, 4):
        return False
    try:
        px = float(bar[index])
        vol = float(bar[4])
        if not (math.isfinite(px) and px > 0 and math.isfinite(vol) and vol >= 0):
            return False
        return vol > 0 if fill else True
    except (TypeError, ValueError):
        return False


def estimate_exchange_margin(legs, spot_price, symbol='NIFTY'):
    """Scenario stress loss + NSE Short Option Minimum (5% SOM) and defined-risk spread floor."""
    psr = 0.08 if symbol == 'NIFTY' else 0.10
    shifts = [-0.15, -psr, -psr / 2.0, 0.0, psr / 2.0, psr, 0.15]
    test_spots = {spot_price * (1.0 + s) for s in shifts} | {float(leg['strike']) for leg in legs}
    net_credit = sum(-leg['side'] * float(leg['entry']) * int(leg['quantity']) for leg in legs)
    worst_loss = 0.0
    for s_px in test_spots:
        payoff = 0.0
        for leg in legs:
            intr = max(s_px - float(leg['strike']), 0.0) if leg['kind'] == 'CE' else max(float(leg['strike']) - s_px, 0.0)
            payoff += leg['side'] * intr * int(leg['quantity'])
        loss = max(0.0, -(payoff + net_credit))
        if loss > worst_loss:
            worst_loss = loss
    net_calls = sum(leg['side'] * int(leg['quantity']) for leg in legs if leg['kind'] == 'CE')
    net_puts = sum(leg['side'] * int(leg['quantity']) for leg in legs if leg['kind'] == 'PE')
    short_qty = max(
        sum(int(leg['quantity']) for leg in legs if leg['kind'] == 'CE' and leg['side'] < 0),
        sum(int(leg['quantity']) for leg in legs if leg['kind'] == 'PE' and leg['side'] < 0),
        0,
    )
    if net_calls < 0 or net_puts < 0:
        # Undefined-risk naked short option: apply 5% Short Option Minimum (SOM) floor
        som_floor = 0.05 * spot_price * short_qty
        exposure_add = 0.02 * spot_price * short_qty
        return float(max(worst_loss + exposure_add, som_floor))
    if short_qty == 0:
        # Pure long option structure: capital required is the net entry debit paid
        return float(max(0.0, -net_credit))
    # Defined-risk spread: apply 1.5% spread margin floor alongside max intrinsic loss
    spread_floor = 0.015 * spot_price * short_qty
    return float(max(worst_loss, spread_floor))


def simulate(s, contracts, ticks, signals=None, callback=None):
    """Simulate multi-leg options strategy with last_valid_close monitoring carry-forward and O(1) loop fee tracking."""
    symbol = s.get('symbol', 'NIFTY')
    spot_key = s.get('spot_key', 'NSE_INDEX|Nifty 50' if symbol == 'NIFTY' else 'NSE_INDEX|Nifty Bank')
    entry_time = s.get('entry', '09:20')
    exit_time = s.get('exit', '15:15')
    slip_bps = float(s.get('slip_bps', 5.0))
    stop_loss = s.get('stop') if s.get('stop') and float(s.get('stop', 0)) > 0 else None
    profit_target = s.get('target') if s.get('target') and float(s.get('target', 0)) > 0 else None
    use_upstox_fees = bool(s.get('use_upstox_fees', True))
    order_fee = float(s.get('order_fee', 20.0))
    raw_legs = s.get('legs') or template(s.get('template', s.get('name', 'Short straddle')) if s.get('template', s.get('name', 'Short straddle')) in TEMPLATES else 'Short straddle')
    legs_spec = [_normalize_leg(item) for item in raw_legs]
    dte_tuple = s.get('dte')
    min_dte = int(dte_tuple[0]) if isinstance(dte_tuple, (list, tuple)) and len(dte_tuple) == 2 else int(s.get('min_dte', 1))
    max_dte = int(dte_tuple[1]) if isinstance(dte_tuple, (list, tuple)) and len(dte_tuple) == 2 else int(s.get('max_dte', 35))
    filters = s.get('filters') or ()
    weekdays_allowed = set(s.get('weekdays', ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']))

    spot_bars = ticks.get(spot_key, {})
    days = sorted({str(ts)[:10] for ts in spot_bars if str(s['start']) <= str(ts)[:10] <= str(s['end'])})
    trades, skips = [], []

    for idx_day, day in enumerate(days):
        if callback:
            callback(idx_day, len(days))
        if weekdays_allowed and pd.Timestamp(day).strftime('%a') not in weekdays_allowed:
            continue
        decision_ts = (datetime.fromisoformat(f'{day} {entry_time}') - timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M')
        spot_bar = spot_bars.get(decision_ts)
        if not valid_price(spot_bar, 3, fill=False):
            record_skip(skips, day, 'Missing preceding spot minute')
            continue
        spot_px = float(spot_bar[3])

        eligible = [c for c in contracts if min_dte <= (pd.Timestamp(c['expiry']) - pd.Timestamp(day)).days <= max_dte]
        if not eligible:
            record_skip(skips, day, 'No eligible expiry in catalogue')
            continue
        expiries_sorted = sorted({c['expiry'] for c in eligible})
        rank_map = {'Nearest': 0, 'Next': 1, 'Next+1': 2}
        rank_idx = rank_map.get(str(s.get('expiry_rank', 'Nearest')), 0)
        if rank_idx >= len(expiries_sorted):
            record_skip(skips, day, 'Requested expiry rank unavailable')
            continue
        exp = expiries_sorted[rank_idx]

        if filters:
            from backtest_signals import decide
            allowed, sig_reason = decide(signals or [], exp, day, entry_time, filters)
            if not allowed:
                record_skip(skips, day, sig_reason)
                continue

        chain = [c for c in eligible if c['expiry'] == exp]

        def pick_contract(kind, target_strike):
            cands = [c for c in chain if c.get('instrument_type') == kind]
            return min(cands, key=lambda c: (abs(float(c['strike_price']) - target_strike), float(c['strike_price']))) if cands else None

        active_legs = []
        skip_reason = None
        entry_stamp = f'{day} {entry_time}'
        lots_mult = max(1, int(s.get('lots', 1)))
        for spec in legs_spec:
            raw_off = float(spec.get('offset', 0.0))
            if symbol == 'BANKNIFTY' and raw_off % 100 != 0:
                raw_off = round(raw_off / 50.0) * 100.0
            target_k = spot_px + (raw_off if spec['kind'] == 'CE' else -raw_off)
            contract = pick_contract(spec['kind'], target_k)
            if not contract:
                skip_reason = 'Missing contract definitions'
                break
            key = contract['instrument_key']
            eb = ticks.get(key, {}).get(entry_stamp)
            if not valid_price(eb, 0, fill=True):
                skip_reason = 'Missing exact entry minute or zero volume'
                break
            raw_entry = float(eb[0])
            side = int(spec['side'])
            qty = int(contract.get('lot_size') or s.get('quantity') or dated_lot_size(symbol, exp)) * int(spec.get('ratio', 1)) * lots_mult
            fill_entry = raw_entry * (1.0 + side * slip_bps / 10000.0)
            active_legs.append({
                'contract': contract.get('trading_symbol', key),
                'key': key,
                'kind': spec['kind'],
                'strike': float(contract['strike_price']),
                'side': side,
                'quantity': qty,
                'raw_entry': raw_entry,
                'entry': fill_entry,
                'last_close': float(eb[3]) if valid_price(eb, 3, fill=False) else raw_entry,
            })
        if skip_reason:
            record_skip(skips, day, skip_reason)
            continue

        user_margin = float(s.get('margin_per_lot') or 0.0)
        if user_margin > 0:
            cap_req = user_margin * lots_mult
        elif s.get('estimate_margin', True):
            cap_req = estimate_exchange_margin(active_legs, spot_px, symbol=symbol)
        else:
            cap_req = 0.0
        cap_alloc = float(s['capital_allocated']) if s.get('capital_allocated') else None
        if cap_alloc is not None and cap_alloc > 0 and cap_req > cap_alloc + 0.005:
            record_skip(skips, day, 'Insufficient allocated capital for exchange margin')
            continue

        # Precompute fixed entry fee once outside the 1-minute monitoring loop
        entry_fee_total = 0.0
        for leg in active_legs:
            if use_upstox_fees:
                act = 'buy' if leg['side'] > 0 else 'sell'
                entry_fee_total += sum(charge(day, 'Options', leg['entry'], leg['quantity'], act).values())
            else:
                entry_fee_total += order_fee

        cursor = datetime.fromisoformat(entry_stamp)
        end_dt = datetime.fromisoformat(f'{day} {exit_time}')
        exit_dt = end_dt
        exit_reason = 'Scheduled time exit'

        while cursor < end_dt:
            cursor_str = cursor.strftime('%Y-%m-%d %H:%M')
            unreal_gross = 0.0
            for leg in active_legs:
                b = ticks.get(leg['key'], {}).get(cursor_str)
                if valid_price(b, 3, fill=False):
                    leg['last_close'] = float(b[3])
                mark_px = leg['last_close']
                est_exit = mark_px * (1.0 - leg['side'] * slip_bps / 10000.0)
                unreal_gross += leg['side'] * (est_exit - leg['entry']) * leg['quantity']
            approx_net = unreal_gross - 2.0 * entry_fee_total
            if (stop_loss is not None and approx_net <= -abs(float(stop_loss))) or (
                profit_target is not None and approx_net >= abs(float(profit_target))
            ):
                exit_dt = cursor + timedelta(minutes=1)
                exit_reason = 'Stop loss' if (stop_loss is not None and approx_net <= -abs(float(stop_loss))) else 'Profit target'
                break
            cursor += timedelta(minutes=1)

        # Find executable exit minute (requiring volume > 0 on actual exit fill)
        fill_dt = exit_dt
        while fill_dt <= end_dt:
            fill_str = fill_dt.strftime('%Y-%m-%d %H:%M')
            if all(valid_price(ticks.get(leg['key'], {}).get(fill_str), 0, fill=True) for leg in active_legs):
                break
            fill_dt += timedelta(minutes=1)
        else:
            record_skip(skips, day, 'Missing executable exit minute with positive volume')
            continue

        fill_str = fill_dt.strftime('%Y-%m-%d %H:%M')
        gross = slippage = 0.0
        cost_parts = {}
        fills_out = []
        for leg in active_legs:
            xb = ticks[leg['key']][fill_str]
            raw_exit = float(xb[0])
            side = leg['side']
            qty = leg['quantity']
            fill_exit = raw_exit * (1.0 - side * slip_bps / 10000.0)
            gross += side * (raw_exit - leg['raw_entry']) * qty
            slippage += (leg['raw_entry'] + raw_exit) * (slip_bps / 10000.0) * qty
            if use_upstox_fees:
                for px, act in [(leg['entry'], 'buy' if side > 0 else 'sell'), (fill_exit, 'sell' if side > 0 else 'buy')]:
                    for k_fee, v_fee in charge(day, 'Options', px, qty, act).items():
                        cost_parts[k_fee] = cost_parts.get(k_fee, 0.0) + v_fee
            fills_out.append({
                'contract': leg['contract'],
                'key': leg['key'],
                'side': side,
                'entry': leg['entry'],
                'exit': fill_exit,
                'quantity': qty,
            })
        charges_total = sum(cost_parts.values()) if use_upstox_fees else 2.0 * len(active_legs) * order_fee
        net_pnl = gross - slippage - charges_total
        premium_val = abs(sum(-leg['side'] * leg['entry'] * leg['quantity'] for leg in active_legs))
        total_cost_val = slippage + charges_total
        trades.append({
            'Date': day,
            'Expiry': exp,
            'Entry time': entry_time,
            'Exit time': fill_dt.strftime('%H:%M'),
            'Exit reason': exit_reason,
            'Spot signal': spot_px,
            'Capital required': cap_req,
            'Premium': premium_val,
            'Gross': gross,
            'Slippage': slippage,
            'Charges': charges_total,
            'Cost': total_cost_val,
            'Net': net_pnl,
            'Net / capital %': (net_pnl / cap_req * 100.0) if cap_req > 0 else 0.0,
            'Legs': json.dumps(fills_out),
            **cost_parts,
        })
    return pd.DataFrame(trades), pd.DataFrame(skips)



def summarize_performance(trades: pd.DataFrame, annualization: float = 245.0) -> dict:
    """Compute guarded Sharpe, Sortino, Max Drawdown, Win Rate, and PnL Attribution."""
    if trades is None or trades.empty or 'Net' not in trades.columns:
        return {
            'trades': 0, 'win_rate_pct': 0.0, 'avg_capital_inr': 0.0, 'total_premium_inr': 0.0,
            'gross_pnl': 0.0, 'slippage': 0.0, 'charges': 0.0, 'net_pnl': 0.0,
            'roi_pct': 0.0, 'max_drawdown_inr': 0.0, 'max_drawdown_pct': 0.0,
            'sharpe': 0.0, 'sortino': 0.0,
        }
    net = pd.to_numeric(trades['Net'], errors='coerce').dropna().to_numpy(dtype=float)
    n = len(net)
    if n == 0:
        return {
            'trades': 0, 'win_rate_pct': 0.0, 'avg_capital_inr': 0.0, 'total_premium_inr': 0.0,
            'gross_pnl': 0.0, 'slippage': 0.0, 'charges': 0.0, 'net_pnl': 0.0,
            'roi_pct': 0.0, 'max_drawdown_inr': 0.0, 'max_drawdown_pct': 0.0,
            'sharpe': 0.0, 'sortino': 0.0,
        }
    cum = np.cumsum(net)
    peaks = np.maximum.accumulate(np.insert(cum, 0, 0.0))[1:]
    dd_inr = peaks - cum
    max_dd_inr = float(np.max(dd_inr)) if len(dd_inr) else 0.0
    cap_series = pd.to_numeric(trades.get('Capital required', pd.Series([0.0])), errors='coerce').dropna()
    avg_cap = float(cap_series.mean()) if len(cap_series) else 0.0
    max_cap = float(cap_series.max()) if len(cap_series) else 0.0
    denom = max_cap + max(0.0, float(np.max(peaks)))
    max_dd_pct = float(max_dd_inr / denom * 100.0) if denom > 0 else 0.0

    mean_pnl = float(np.mean(net))
    std_pnl = float(np.std(net, ddof=1)) if n > 1 else 0.0
    sharpe = float((mean_pnl / std_pnl) * math.sqrt(annualization)) if std_pnl > 1e-9 else 0.0

    downside = np.minimum(0.0, net)
    down_dev = float(math.sqrt(np.mean(np.square(downside))))
    sortino = float((mean_pnl / down_dev) * math.sqrt(annualization)) if down_dev > 1e-9 else (sharpe if mean_pnl > 0 else 0.0)

    net_sum = float(net.sum())
    return {
        'trades': int(n),
        'win_rate_pct': float((net > 0).sum() / n * 100.0),
        'avg_capital_inr': avg_cap,
        'total_premium_inr': float(pd.to_numeric(trades.get('Premium', pd.Series([0.0])), errors='coerce').sum()),
        'gross_pnl': float(pd.to_numeric(trades.get('Gross', pd.Series([0.0])), errors='coerce').sum()),
        'slippage': float(pd.to_numeric(trades.get('Slippage', pd.Series([0.0])), errors='coerce').sum()),
        'charges': float(pd.to_numeric(trades.get('Charges', pd.Series([0.0])), errors='coerce').sum()),
        'net_pnl': net_sum,
        'roi_pct': float(net_sum / avg_cap * 100.0) if avg_cap > 0 else 0.0,
        'max_drawdown_inr': max_dd_inr,
        'max_drawdown_pct': max_dd_pct,
        'sharpe': sharpe,
        'sortino': sortino,
    }
