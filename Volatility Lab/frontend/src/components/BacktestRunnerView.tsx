import React, { useEffect, useState } from 'react';
import { Play, CheckCircle, Percent, DollarSign, ArrowDown, Activity } from 'lucide-react';
import { BacktestRun } from '../types';

export const BacktestRunnerView: React.FC = () => {
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<BacktestRun | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    setLoading(true);

    fetch('/api/backtest-summary')
      .then((r) => r.json())
      .then((res: { runs: BacktestRun[] }) => {
        if (mounted) {
          setRuns(res.runs || []);
          if (res.runs && res.runs.length > 0) {
            setSelectedRun(res.runs[0]);
          }
          setLoading(false);
        }
      })
      .catch((err) => {
        console.error('Failed to load backtest runs:', err);
        if (mounted) setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, []);

  return (
    <div className="space-y-4">
      {/* Strategy selector and top metrics banner */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <div className="glass-panel p-4 rounded-xl border border-slate-800">
          <div className="text-[11px] font-medium uppercase text-slate-400 mb-1">Total Verified Trades</div>
          <div className="text-2xl font-bold font-mono text-white">
            {selectedRun?.total_trades ?? 0}
          </div>
        </div>

        <div className="glass-panel p-4 rounded-xl border border-slate-800">
          <div className="text-[11px] font-medium uppercase text-slate-400 mb-1">Win Rate %</div>
          <div className="text-2xl font-bold font-mono text-emerald-400">
            {selectedRun?.win_rate_pct != null ? `${selectedRun.win_rate_pct}%` : '—'}
          </div>
        </div>

        <div className="glass-panel p-4 rounded-xl border border-slate-800">
          <div className="text-[11px] font-medium uppercase text-slate-400 mb-1">Total Net P&L</div>
          <div
            className={`text-2xl font-bold font-mono ${
              (selectedRun?.total_net_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'
            }`}
          >
            ₹{selectedRun?.total_net_pnl ? selectedRun.total_net_pnl.toLocaleString('en-IN') : '0.00'}
          </div>
        </div>

        <div className="glass-panel p-4 rounded-xl border border-slate-800">
          <div className="text-[11px] font-medium uppercase text-slate-400 mb-1">Engine & Strategy</div>
          <div className="text-base font-bold text-cyan-400 truncate">
            {selectedRun?.name || 'Multi-leg Volatility'}
          </div>
        </div>
      </div>

      {/* Main Backtest View: Strategy List + Executions Table */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Left: Strategy Run List */}
        <div className="glass-panel p-4 rounded-xl border border-slate-800">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3">
            Historical Backtest Reports
          </h3>
          <div className="space-y-2">
            {runs.map((r) => {
              const active = selectedRun?.id === r.id;
              return (
                <div
                  key={r.id}
                  onClick={() => setSelectedRun(r)}
                  className={`p-3 rounded-lg border cursor-pointer transition-all ${
                    active
                      ? 'bg-cyan-500/10 border-cyan-500/40 text-white'
                      : 'bg-slate-900/50 border-slate-800/80 hover:bg-slate-800/50 text-slate-300'
                  }`}
                >
                  <div className="flex justify-between items-center mb-1">
                    <span className="font-bold text-sm text-cyan-300">{r.name}</span>
                    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">
                      {r.symbol}
                    </span>
                  </div>
                  <div className="flex justify-between items-center text-xs font-mono text-slate-400">
                    <span>{r.total_trades} trades ({r.win_rate_pct}% WR)</span>
                    <span className={r.total_net_pnl >= 0 ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold'}>
                      ₹{r.total_net_pnl.toLocaleString('en-IN')}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right: Detailed Trade Log */}
        <div className="lg:col-span-2 glass-panel p-4 rounded-xl border border-slate-800">
          <div className="flex justify-between items-center mb-3">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Verified Executed Trades ({selectedRun?.name})
            </h3>
            <span className="text-[10px] font-mono text-cyan-400">
              Span: {selectedRun?.start} → {selectedRun?.end}
            </span>
          </div>

          <div className="overflow-x-auto max-h-[420px] rounded-lg border border-slate-800">
            <table className="w-full text-xs font-mono text-right">
              <thead className="sticky top-0 bg-slate-950 text-[10px] uppercase text-slate-400 border-b border-slate-800">
                <tr>
                  <th className="py-2 px-3 text-left">Date / Expiry</th>
                  <th className="py-2 px-3 text-left">Signal Type</th>
                  <th className="py-2 px-3">Gross</th>
                  <th className="py-2 px-3">Slippage</th>
                  <th className="py-2 px-3">Charges</th>
                  <th className="py-2 px-3 text-emerald-400 font-bold">Net P&L</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {selectedRun?.trades?.map((t, idx) => {
                  const net = floatOrZero(t.Net || t.Gross);
                  return (
                    <tr key={idx} className="hover:bg-cyan-500/5 transition-colors">
                      <td className="py-2 px-3 text-left text-slate-300">
                        {t.Date || t.Entry || '—'}
                        {t.Expiry ? ` (${t.Expiry})` : ''}
                      </td>
                      <td className="py-2 px-3 text-left text-cyan-300">
                        {t['Exit reason'] || t.Direction || 'Rule Exit'}
                      </td>
                      <td className="py-2 px-3 text-slate-300">₹{floatOrZero(t.Gross).toFixed(1)}</td>
                      <td className="py-2 px-3 text-rose-300">₹{floatOrZero(t.Slippage).toFixed(1)}</td>
                      <td className="py-2 px-3 text-amber-300">₹{floatOrZero(t.Charges).toFixed(1)}</td>
                      <td
                        className={`py-2 px-3 font-bold ${
                          net >= 0 ? 'text-emerald-400' : 'text-rose-400'
                        }`}
                      >
                        ₹{net.toFixed(2)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
};

function floatOrZero(val: any): number {
  const n = parseFloat(val);
  return isNaN(n) ? 0.0 : n;
}
