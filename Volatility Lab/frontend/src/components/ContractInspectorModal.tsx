import React from 'react';
import { X, Layers, Activity, TrendingUp, DollarSign } from 'lucide-react';

interface ContractInspectorProps {
  contract: {
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
  } | null;
  onClose: () => void;
}

export const ContractInspectorModal: React.FC<ContractInspectorProps> = ({ contract, onClose }) => {
  if (!contract) return null;

  const isCall = contract.type === 'CE';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/70 backdrop-blur-md animate-fade-in">
      <div className="glass-panel w-full max-w-lg p-6 rounded-2xl border border-slate-700 shadow-2xl relative">
        {/* Close Button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 p-1.5 rounded-lg bg-slate-800 text-slate-400 hover:text-white transition-colors"
        >
          <X className="w-4 h-4" />
        </button>

        {/* Modal Title */}
        <div className="flex items-center gap-2.5 mb-4">
          <div
            className={`w-8 h-8 rounded-lg flex items-center justify-center font-bold text-xs ${
              isCall ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'
            }`}
          >
            {contract.type}
          </div>
          <div>
            <h3 className="text-base font-bold text-white font-mono">
              {contract.symbol} {contract.strike} {contract.type}
            </h3>
            <span className="text-xs text-slate-400">Expiry: {contract.expiry}</span>
          </div>
        </div>

        {/* Price & IV Highlight */}
        <div className="grid grid-cols-2 gap-3 mb-4 p-3 rounded-xl bg-slate-900/80 border border-slate-800 font-mono">
          <div>
            <span className="text-[10px] text-slate-400 uppercase block">Last Traded Price</span>
            <span className={`text-xl font-bold ${isCall ? 'text-emerald-400' : 'text-rose-400'}`}>
              ₹{contract.ltp ? contract.ltp.toFixed(2) : '—'}
            </span>
          </div>
          <div>
            <span className="text-[10px] text-slate-400 uppercase block">Implied Volatility</span>
            <span className="text-xl font-bold text-cyan-300">
              {contract.iv ? `${contract.iv.toFixed(1)}%` : '—'}
            </span>
          </div>
        </div>

        {/* Greek Matrix */}
        <div className="space-y-2 mb-6">
          <h4 className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            Black-Scholes Greek Sensitivities
          </h4>
          <div className="grid grid-cols-2 gap-2 text-xs font-mono">
            <div className="p-2.5 rounded-lg bg-slate-900/50 border border-slate-800 flex justify-between">
              <span className="text-slate-400">Delta (Δ):</span>
              <span className="font-bold text-white">{contract.delta ? contract.delta.toFixed(3) : '—'}</span>
            </div>
            <div className="p-2.5 rounded-lg bg-slate-900/50 border border-slate-800 flex justify-between">
              <span className="text-slate-400">Theta (Θ/day):</span>
              <span className="font-bold text-rose-400">{contract.theta ? contract.theta.toFixed(2) : '—'}</span>
            </div>
            <div className="p-2.5 rounded-lg bg-slate-900/50 border border-slate-800 flex justify-between">
              <span className="text-slate-400">Open Interest:</span>
              <span className="font-bold text-white">{contract.oi?.toLocaleString('en-IN') ?? '—'}</span>
            </div>
            <div className="p-2.5 rounded-lg bg-slate-900/50 border border-slate-800 flex justify-between">
              <span className="text-slate-400">Traded Volume:</span>
              <span className="font-bold text-white">{contract.volume?.toLocaleString('en-IN') ?? '—'}</span>
            </div>
          </div>
        </div>

        {/* Quick Decision Action */}
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs font-medium rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors"
          >
            Close Inspector
          </button>
        </div>
      </div>
    </div>
  );
};
