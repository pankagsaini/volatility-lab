import React, { useEffect, useState } from 'react';
import { ArrowLeftRight, TrendingUp } from 'lucide-react';

interface FuturesCarryPayload {
  rate_percent: number;
  indices: Array<{
    symbol: string;
    collected: string;
    pairs: any[];
    legs: any[];
  }>;
}

export const FuturesCarryView: React.FC = () => {
  const [data, setData] = useState<FuturesCarryPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    setLoading(true);

    fetch('/api/futures-carry')
      .then((r) => r.json())
      .then((res: FuturesCarryPayload) => {
        if (mounted) {
          setData(res);
          setLoading(false);
        }
      })
      .catch((err) => {
        console.error('Failed to load futures carry:', err);
        if (mounted) setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, []);

  return (
    <div className="space-y-4">
      <div className="glass-panel p-4 rounded-xl border border-slate-800 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ArrowLeftRight className="w-4 h-4 text-cyan-400" />
          <h2 className="text-sm font-semibold tracking-wide text-white uppercase">
            Futures Carry & Roll Spread Term Structure
          </h2>
        </div>
        <span className="text-xs font-mono text-cyan-400">
          Risk-Free Rate: {data?.rate_percent ? `${data.rate_percent}%` : '—'}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {data?.indices?.map((idx) => (
          <div key={idx.symbol} className="glass-panel p-4 rounded-xl border border-slate-800">
            <h3 className="text-xs font-bold uppercase tracking-wider text-cyan-300 mb-3">
              {idx.symbol} Futures Term & Spreads
            </h3>

            <div className="space-y-3">
              {idx.pairs && idx.pairs.length > 0 ? (
                idx.pairs.map((p: any, i: number) => (
                  <div key={i} className="p-3 rounded-lg bg-slate-900/60 border border-slate-800">
                    <div className="flex justify-between items-center mb-1 text-xs font-mono">
                      <span className="font-semibold text-white">
                        {p.near_expiry} → {p.far_expiry}
                      </span>
                      <span className="text-emerald-400 font-bold">
                        Carry: {p.annualized_carry_pct ? `${p.annualized_carry_pct.toFixed(2)}%` : '—'}
                      </span>
                    </div>
                    <div className="flex justify-between items-center text-[11px] font-mono text-slate-400">
                      <span>Spread: {p.spread_points != null ? `${p.spread_points.toFixed(1)} pts` : '—'}</span>
                      <span>DTE Gap: {p.dte_gap ? `${p.dte_gap.toFixed(0)}d` : '—'}</span>
                    </div>
                  </div>
                ))
              ) : (
                <div className="text-xs font-mono text-slate-400 p-4 text-center">
                  No active calendar spreads available.
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
