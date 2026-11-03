import React, { useEffect, useState } from 'react';
import { Target, Compass, ArrowUpRight, ArrowDownRight, AlertTriangle, CheckCircle2, Calendar } from 'lucide-react';
import { TradeRadar, ExpiryItem } from '../types';

interface TradeRadarViewProps {
  symbol: string;
  selectedExpiry?: string;
  expiries?: ExpiryItem[];
  onSelectExpiry?: (exp: string) => void;
}

export const TradeRadarView: React.FC<TradeRadarViewProps> = ({
  symbol,
  selectedExpiry,
  expiries,
  onSelectExpiry,
}) => {
  const [data, setData] = useState<TradeRadar | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    setLoading(true);

    const query = new URLSearchParams({ symbol });
    if (selectedExpiry) {
      query.append('expiry', selectedExpiry);
    }

    fetch(`/api/trade-radar?${query.toString()}`)
      .then((r) => r.json())
      .then((res: TradeRadar) => {
        if (mounted) {
          setData(res);
          setLoading(false);
        }
      })
      .catch((err) => {
        console.error('Failed to load trade radar:', err);
        if (mounted) setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, [symbol, selectedExpiry]);

  return (
    <div className="space-y-4">
      {/* Expiry Bar if expiries & onSelectExpiry are passed */}
      {expiries && expiries.length > 0 && onSelectExpiry && (
        <div className="glass-panel px-4 py-2.5 rounded-xl border border-slate-800 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Calendar className="w-4 h-4 text-cyan-400" />
            <span className="text-xs font-semibold uppercase text-slate-300">
              Active Focus Expiry:
            </span>
            <select
              value={selectedExpiry || (expiries[0] ? expiries[0].expiry : '')}
              onChange={(e) => onSelectExpiry(e.target.value)}
              className="px-2.5 py-1 text-xs font-mono font-medium rounded-lg bg-slate-900 border border-slate-700 text-cyan-300 focus:outline-none focus:border-cyan-500 cursor-pointer"
            >
              {expiries.map((exp) => (
                <option key={exp.expiry} value={exp.expiry}>
                  {exp.expiry} ({exp.dte.toFixed(1)} DTE)
                </option>
              ))}
            </select>
          </div>
          <div className="text-xs font-mono text-slate-400">
            ATM Spot Ref: ₹{data?.spot ? data.spot.toLocaleString('en-IN') : '—'}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 1. Tactical Decision Card */}
        <div className="glass-panel p-5 rounded-xl border border-slate-800 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-semibold uppercase tracking-wider text-cyan-400 flex items-center gap-1.5">
                <Compass className="w-4 h-4" /> Tactical Volatility Regime
              </span>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-400 font-bold border border-cyan-500/20">
                {data?.expiry ? `${data.expiry} (${data.dte.toFixed(1)}d)` : 'Loading...'}
              </span>
            </div>

            <h3 className="text-lg font-bold text-white mb-2">{data?.bias_title || 'Analyzing Regime...'}</h3>
            <p className="text-xs text-slate-300 leading-relaxed bg-slate-900/80 p-3 rounded-lg border border-slate-800/80">
              {data?.bias_action || 'Evaluating implied volatility skew and term structure...'}
            </p>
          </div>

          <div className="mt-4 pt-3 border-t border-slate-800 grid grid-cols-2 gap-2 text-xs font-mono">
            <div>
              <span className="text-slate-400 block text-[10px]">ATM IV</span>
              <span className="text-sm font-bold text-cyan-300">{data?.atm_iv ? `${data.atm_iv.toFixed(1)}%` : '—'}</span>
            </div>
            <div>
              <span className="text-slate-400 block text-[10px]">ATM Straddle LTP</span>
              <span className="text-sm font-bold text-white">₹{data?.straddle_ltp ? data.straddle_ltp.toFixed(1) : '—'}</span>
            </div>
          </div>
        </div>

        {/* 2. Expected Move & Range Bounds */}
        <div className="glass-panel p-5 rounded-xl border border-slate-800 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-semibold uppercase tracking-wider text-emerald-400 flex items-center gap-1.5">
                <Target className="w-4 h-4" /> 1-Sigma Expected Move ({data?.expiry || ''})
              </span>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400">
                68.2% Probability
              </span>
            </div>

            <div className="flex items-baseline justify-between mb-3">
              <div className="text-2xl font-bold font-mono text-white">
                ±{data?.expected_move_pts != null ? Math.round(data.expected_move_pts) : '—'} pts
              </div>
              <div className="text-sm font-mono font-semibold text-emerald-400">
                ±{data?.expected_move_pct != null ? data.expected_move_pct.toFixed(2) : '—'}%
              </div>
            </div>

            <div className="space-y-2 text-xs font-mono">
              <div className="flex justify-between items-center p-2 rounded bg-slate-900/60 border border-slate-800">
                <span className="text-slate-400 flex items-center gap-1">
                  <ArrowUpRight className="w-3.5 h-3.5 text-emerald-400" /> Upper Range Bound
                </span>
                <span className="font-bold text-white">
                  ₹{data?.expected_range_high ? data.expected_range_high.toLocaleString('en-IN') : '—'}
                </span>
              </div>

              <div className="flex justify-between items-center p-2 rounded bg-slate-900/60 border border-slate-800">
                <span className="text-slate-400 flex items-center gap-1">
                  <ArrowDownRight className="w-3.5 h-3.5 text-rose-400" /> Lower Range Bound
                </span>
                <span className="font-bold text-white">
                  ₹{data?.expected_range_low ? data.expected_range_low.toLocaleString('en-IN') : '—'}
                </span>
              </div>
            </div>
          </div>

          <div className="mt-3 text-[11px] text-slate-400">
            Straddle BE: ₹{data?.straddle_be_low?.toLocaleString('en-IN')} — ₹{data?.straddle_be_high?.toLocaleString('en-IN')}
          </div>
        </div>

        {/* 3. Synthetic Basis & Signals */}
        <div className="glass-panel p-5 rounded-xl border border-slate-800 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-semibold uppercase tracking-wider text-amber-400 flex items-center gap-1.5">
                <AlertTriangle className="w-4 h-4" /> Put-Call Parity & Basis
              </span>
            </div>

            <div className="mb-3">
              <div className="text-[11px] text-slate-400">Synthetic Futures Basis (C - P + K - S)</div>
              <div className="text-xl font-bold font-mono text-cyan-400">
                {data?.synthetic_basis_pts != null
                  ? `${data.synthetic_basis_pts > 0 ? '+' : ''}${data.synthetic_basis_pts.toFixed(1)} pts`
                  : '—'}
              </div>
            </div>

            <div className="space-y-1.5">
              {data?.notes?.map((note, idx) => (
                <div key={idx} className="flex items-start gap-2 text-xs text-slate-300">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 mt-0.5 shrink-0" />
                  <span>{note}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="mt-4 pt-3 border-t border-slate-800 text-[10px] text-slate-500 font-mono">
            Refreshed automatically every 20s from live option quotes.
          </div>
        </div>
      </div>
    </div>
  );
};
