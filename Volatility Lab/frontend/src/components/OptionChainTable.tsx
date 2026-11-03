import React, { useEffect, useState } from 'react';
import { Table, Zap, Info } from 'lucide-react';
import { OptionChainPayload, ExpiryItem } from '../types';

interface OptionChainTableProps {
  symbol: string;
  selectedExpiry: string;
  onSelectExpiry: (exp: string) => void;
  expiries: ExpiryItem[];
  onInspectContract: (contract: {
    symbol: string;
    strike: number;
    type: 'CE' | 'PE';
    expiry: string;
    ltp: number;
    iv: number;
    delta: number;
    theta: number;
    volume: number;
    oi: number;
  }) => void;
}

export const OptionChainTable: React.FC<OptionChainTableProps> = ({
  symbol,
  selectedExpiry,
  onSelectExpiry,
  expiries,
  onInspectContract,
}) => {
  const [data, setData] = useState<OptionChainPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    setLoading(true);

    const query = new URLSearchParams({ symbol });
    if (selectedExpiry) query.append('expiry', selectedExpiry);

    fetch(`/api/option-chain?${query.toString()}`)
      .then((r) => r.json())
      .then((res: OptionChainPayload) => {
        if (mounted) {
          setData(res);
          setLoading(false);
        }
      })
      .catch((err) => {
        console.error('Failed to load option chain:', err);
        if (mounted) setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, [symbol, selectedExpiry]);

  return (
    <div className="glass-panel p-4 rounded-xl border border-slate-800">
      {/* Header bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 mb-4 pb-3 border-b border-slate-800">
        <div className="flex items-center gap-3">
          <Zap className="w-4 h-4 text-cyan-400" />
          <h2 className="text-sm font-semibold tracking-wide text-white uppercase">
            Option Chain — {symbol}
          </h2>

          {/* Expiry Dropdown */}
          <select
            value={selectedExpiry}
            onChange={(e) => onSelectExpiry(e.target.value)}
            className="px-2.5 py-1 text-xs font-mono font-medium rounded-lg bg-slate-900 border border-slate-700 text-cyan-300 focus:outline-none focus:border-cyan-500"
          >
            {expiries.map((exp) => (
              <option key={exp.expiry} value={exp.expiry}>
                {exp.expiry} ({exp.dte.toFixed(1)} DTE)
              </option>
            ))}
          </select>
        </div>

        {/* Institutional Metrics Banner */}
        <div className="flex items-center gap-4 text-xs font-mono">
          <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-slate-900 border border-slate-800">
            <span className="text-slate-400">PCR:</span>
            <span className={`font-bold ${data && data.pcr > 1.0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              {data?.pcr != null ? data.pcr.toFixed(2) : '—'}
            </span>
          </div>

          <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-slate-900 border border-slate-800">
            <span className="text-slate-400">Max Pain:</span>
            <span className="font-bold text-amber-400">
              ₹{data?.max_pain != null ? data.max_pain.toLocaleString('en-IN') : '—'}
            </span>
          </div>

          <div className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-slate-900 border border-slate-800">
            <span className="text-slate-400">ATM Straddle:</span>
            <span className="font-bold text-cyan-400">
              ₹{data?.straddle_ltp != null ? data.straddle_ltp.toFixed(1) : '—'}
            </span>
          </div>
        </div>
      </div>

      {/* Paired Table */}
      <div className="relative overflow-x-auto max-h-[620px] rounded-lg border border-slate-800">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-950/70 z-20 backdrop-blur-sm">
            <span className="text-xs font-mono text-cyan-400 animate-pulse">
              Loading Real-Time Option Chain & Greeks...
            </span>
          </div>
        )}

        <table className="w-full text-xs font-mono text-right border-collapse select-none">
          <thead className="sticky top-0 z-10 bg-slate-950/95 text-[11px] text-slate-400 uppercase tracking-wider border-b border-slate-800">
            <tr>
              <th colSpan={6} className="py-1.5 px-3 text-center bg-emerald-950/30 text-emerald-400 border-r border-slate-800">
                CALLS (CE)
              </th>
              <th className="py-1.5 px-4 text-center bg-slate-900 font-bold text-white border-r border-slate-800">
                STRIKE
              </th>
              <th colSpan={6} className="py-1.5 px-3 text-center bg-rose-950/30 text-rose-400">
                PUTS (PE)
              </th>
            </tr>
            <tr className="border-b border-slate-800 bg-slate-900/60 text-[10px]">
              {/* Call Columns */}
              <th className="py-1 px-2">Delta</th>
              <th className="py-1 px-2">Theta</th>
              <th className="py-1 px-2">IV (%)</th>
              <th className="py-1 px-2">Volume</th>
              <th className="py-1 px-2">OI</th>
              <th className="py-1 px-2 text-emerald-400 border-r border-slate-800">LTP</th>

              {/* Strike Column */}
              <th className="py-1 px-4 text-center bg-slate-900 text-slate-300 border-r border-slate-800">₹</th>

              {/* Put Columns */}
              <th className="py-1 px-2 text-rose-400 text-left">LTP</th>
              <th className="py-1 px-2">OI</th>
              <th className="py-1 px-2">Volume</th>
              <th className="py-1 px-2">IV (%)</th>
              <th className="py-1 px-2">Theta</th>
              <th className="py-1 px-2">Delta</th>
            </tr>
          </thead>

          <tbody className="divide-y divide-slate-800/60">
            {!data?.rows?.length && (
              <tr>
                <td colSpan={13} className="py-12 text-center text-slate-500 text-xs font-mono">
                  {loading ? 'Loading option chain...' : 'No option chain data available for this expiry.'}
                </td>
              </tr>
            )}
            {data?.rows?.map((row) => {
              const isAtm = row.is_atm;
              return (
                <tr
                  key={row.strike}
                  className={`transition-colors hover:bg-cyan-500/10 ${
                    isAtm ? 'bg-cyan-500/15 font-semibold border-y-2 border-cyan-400' : ''
                  }`}
                >
                  {/* Call Greeks */}
                  <td className="py-1.5 px-2 text-slate-400">{row.call.delta != null ? row.call.delta.toFixed(2) : '—'}</td>
                  <td className="py-1.5 px-2 text-slate-400">{row.call.theta != null ? row.call.theta.toFixed(1) : '—'}</td>
                  <td className="py-1.5 px-2 text-cyan-300">{row.call.iv != null ? `${row.call.iv.toFixed(1)}%` : '—'}</td>
                  <td className="py-1.5 px-2 text-slate-400">{row.call.volume != null ? row.call.volume.toLocaleString('en-IN') : '—'}</td>
                  <td className="py-1.5 px-2 text-slate-300">{row.call.oi != null ? row.call.oi.toLocaleString('en-IN') : '—'}</td>
                  <td
                    onClick={() =>
                      onInspectContract({
                        symbol,
                        strike: row.strike,
                        type: 'CE',
                        expiry: data.expiry,
                        ...row.call,
                      })
                    }
                    className="py-1.5 px-2 font-bold text-emerald-400 border-r border-slate-800 cursor-pointer hover:underline"
                    title="Click to inspect Call Contract"
                  >
                    {row.call.ltp != null ? row.call.ltp.toFixed(2) : '—'}
                  </td>

                  {/* Strike Column */}
                  <td className={`py-1.5 px-4 text-center border-r border-slate-800 ${isAtm ? 'text-cyan-300 font-bold bg-cyan-900/40' : 'bg-slate-900/80 text-white'}`}>
                    {row.strike.toLocaleString('en-IN')}
                    {isAtm && <span className="ml-1 text-[9px] text-cyan-300 uppercase">ATM</span>}
                  </td>

                  {/* Put Greeks */}
                  <td
                    onClick={() =>
                      onInspectContract({
                        symbol,
                        strike: row.strike,
                        type: 'PE',
                        expiry: data.expiry,
                        ...row.put,
                      })
                    }
                    className="py-1.5 px-2 font-bold text-rose-400 text-left cursor-pointer hover:underline"
                    title="Click to inspect Put Contract"
                  >
                    {row.put.ltp != null ? row.put.ltp.toFixed(2) : '—'}
                  </td>
                  <td className="py-1.5 px-2 text-slate-300">{row.put.oi != null ? row.put.oi.toLocaleString('en-IN') : '—'}</td>
                  <td className="py-1.5 px-2 text-slate-400">{row.put.volume != null ? row.put.volume.toLocaleString('en-IN') : '—'}</td>
                  <td className="py-1.5 px-2 text-cyan-300">{row.put.iv != null ? `${row.put.iv.toFixed(1)}%` : '—'}</td>
                  <td className="py-1.5 px-2 text-slate-400">{row.put.theta != null ? row.put.theta.toFixed(1) : '—'}</td>
                  <td className="py-1.5 px-2 text-slate-400">{row.put.delta != null ? row.put.delta.toFixed(2) : '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};
