import React from 'react';
import { TrendingUp, TrendingDown, Database, ShieldCheck } from 'lucide-react';
import { MarketSummary } from '../types';

interface TickerRibbonProps {
  summary: MarketSummary | null;
}

export const TickerRibbon: React.FC<TickerRibbonProps> = ({ summary }) => {
  const nifty = summary?.nifty;
  const banknifty = summary?.banknifty;
  const vix = summary?.india_vix;
  const cloud = summary?.cloud_sync;

  const formatPrice = (val: number | null | undefined) =>
    val != null ? val.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';

  const formatChange = (ch: number | null | undefined, pch: number | null | undefined) => {
    if (ch == null || pch == null) return null;
    const isPos = ch >= 0;
    const Icon = isPos ? TrendingUp : TrendingDown;
    const color = isPos ? 'text-emerald-400' : 'text-rose-400';
    return (
      <span className={`inline-flex items-center gap-1 text-xs font-mono font-medium ${color}`}>
        <Icon className="w-3.5 h-3.5" />
        {isPos ? '+' : ''}{ch.toFixed(2)} ({isPos ? '+' : ''}{pch.toFixed(2)}%)
      </span>
    );
  };

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {/* NIFTY 50 Card */}
      <div className="glass-panel p-3.5 rounded-xl border border-slate-800/80 hover:border-slate-700 transition-all">
        <div className="flex items-center justify-between text-[11px] font-medium uppercase tracking-wider text-slate-400 mb-1">
          <span>NIFTY 50 Spot</span>
          {nifty?.future_price != null && (
            <span className="font-mono text-[10px] text-cyan-400 font-semibold">
              Fut: ₹{nifty.future_price.toLocaleString('en-IN')}
            </span>
          )}
        </div>
        <div className="flex items-baseline justify-between gap-2">
          <div className="text-xl font-bold font-mono tracking-tight text-white">
            {formatPrice(nifty?.price)}
          </div>
          <div>{formatChange(nifty?.change, nifty?.pchange)}</div>
        </div>
        {nifty?.synthetic_future != null && (
          <div className="flex items-center justify-between mt-1 text-[10px] font-mono text-slate-500">
            <span>Syn Fut: <b className="text-purple-400">₹{nifty.synthetic_future.toLocaleString('en-IN')}</b></span>
            <span>Basis: <b className={nifty.synthetic_basis != null && nifty.synthetic_basis >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
              {nifty.synthetic_basis != null ? `${nifty.synthetic_basis > 0 ? '+' : ''}${nifty.synthetic_basis.toFixed(1)}` : '—'}
            </b></span>
          </div>
        )}
      </div>

      {/* BANKNIFTY Card */}
      <div className="glass-panel p-3.5 rounded-xl border border-slate-800/80 hover:border-slate-700 transition-all">
        <div className="flex items-center justify-between text-[11px] font-medium uppercase tracking-wider text-slate-400 mb-1">
          <span>BANKNIFTY Spot</span>
          {banknifty?.future_price != null && (
            <span className="font-mono text-[10px] text-indigo-400 font-semibold">
              Fut: ₹{banknifty.future_price.toLocaleString('en-IN')}
            </span>
          )}
        </div>
        <div className="flex items-baseline justify-between gap-2">
          <div className="text-xl font-bold font-mono tracking-tight text-white">
            {formatPrice(banknifty?.price)}
          </div>
          <div>{formatChange(banknifty?.change, banknifty?.pchange)}</div>
        </div>
        {banknifty?.synthetic_future != null && (
          <div className="flex items-center justify-between mt-1 text-[10px] font-mono text-slate-500">
            <span>Syn Fut: <b className="text-purple-400">₹{banknifty.synthetic_future.toLocaleString('en-IN')}</b></span>
            <span>Basis: <b className={banknifty.synthetic_basis != null && banknifty.synthetic_basis >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
              {banknifty.synthetic_basis != null ? `${banknifty.synthetic_basis > 0 ? '+' : ''}${banknifty.synthetic_basis.toFixed(1)}` : '—'}
            </b></span>
          </div>
        )}
      </div>

      {/* INDIA VIX Card */}
      <div className="glass-panel p-3.5 rounded-xl border border-slate-800/80 hover:border-slate-700 transition-all">
        <div className="text-[11px] font-medium uppercase tracking-wider text-slate-400 mb-1">
          INDIA VIX Volatility
        </div>
        <div className="flex items-baseline justify-between gap-2">
          <div className="text-xl font-bold font-mono tracking-tight text-cyan-400">
            {vix?.price != null ? vix.price.toFixed(2) : '—'}
          </div>
          <div>{formatChange(vix?.change, vix?.pchange)}</div>
        </div>
      </div>

      {/* Cloud Sync Health */}
      <div className="glass-panel p-3.5 rounded-xl border border-slate-800/80 hover:border-slate-700 transition-all">
        <div className="flex items-center justify-between text-[11px] font-medium uppercase tracking-wider text-slate-400 mb-1">
          <span>Cloud Archive Mirror</span>
          <ShieldCheck className="w-3.5 h-3.5 text-cyan-400" />
        </div>
        <div className="flex items-baseline justify-between gap-2">
          <div className="text-sm font-semibold text-slate-200">
            {cloud?.status || 'Active Sync'}
          </div>
          <div className="text-xs font-mono text-cyan-400 flex items-center gap-1">
            <Database className="w-3 h-3" />
            <span>{cloud?.today_count ?? 0} snapshots</span>
          </div>
        </div>
      </div>
    </div>
  );
};
