import React from 'react';
import { X, Sliders, Check } from 'lucide-react';

interface DrawerInspectorProps {
  isOpen: boolean;
  onClose: () => void;
  surfaceRange: number;
  onSetSurfaceRange: (val: number) => void;
  ivMode: string;
  onSetIvMode: (mode: string) => void;
  spaceMode: string;
  onSetSpaceMode: (mode: string) => void;
  includeZeroDte: boolean;
  onSetIncludeZeroDte: (val: boolean) => void;
}

export const DrawerInspector: React.FC<DrawerInspectorProps> = ({
  isOpen,
  onClose,
  surfaceRange,
  onSetSurfaceRange,
  ivMode,
  onSetIvMode,
  spaceMode,
  onSetSpaceMode,
  includeZeroDte,
  onSetIncludeZeroDte,
}) => {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 overflow-hidden">
      {/* Backdrop */}
      <div
        onClick={onClose}
        className="absolute inset-0 bg-slate-950/60 backdrop-blur-sm transition-opacity"
      />

      <div className="fixed inset-y-0 right-0 max-w-full flex pl-10">
        <div className="w-screen max-w-sm glass-panel bg-[#0d1524] border-l border-slate-800 p-6 flex flex-col justify-between shadow-2xl">
          <div>
            {/* Header */}
            <div className="flex items-center justify-between pb-4 border-b border-slate-800">
              <div className="flex items-center gap-2">
                <Sliders className="w-4 h-4 text-cyan-400" />
                <h3 className="text-sm font-bold text-white uppercase tracking-wider">
                  Model & Surface Parameters
                </h3>
              </div>
              <button
                onClick={onClose}
                className="p-1 rounded-lg text-slate-400 hover:text-white transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Parameter Controls */}
            <div className="space-y-6 mt-6">
              {/* Strike Range */}
              <div>
                <div className="flex justify-between items-center text-xs font-mono mb-2">
                  <span className="text-slate-300 font-medium">Strike Window Around ATM:</span>
                  <span className="text-cyan-400 font-bold">±{surfaceRange}%</span>
                </div>
                <input
                  type="range"
                  min="4"
                  max="12"
                  step="1"
                  value={surfaceRange}
                  onChange={(e) => onSetSurfaceRange(Number(e.target.value))}
                  className="w-full accent-cyan-400 cursor-pointer"
                />
                <div className="flex justify-between text-[10px] font-mono text-slate-500 mt-1">
                  <span>±4% (Tight ATM)</span>
                  <span>±8% (Optimal)</span>
                  <span>±12% (Wide Skew)</span>
                </div>
              </div>

              {/* IV Mode */}
              <div>
                <label className="text-xs font-mono text-slate-300 block mb-2 font-medium">
                  Implied Volatility Source:
                </label>
                <div className="grid grid-cols-4 gap-1 p-1 rounded-lg bg-slate-900 border border-slate-800">
                  {[
                    { id: 'otm', label: 'OTM Skew' },
                    { id: 'call', label: 'Call IV' },
                    { id: 'put', label: 'Put IV' },
                    { id: 'mid', label: 'Mid IV' },
                  ].map((m) => (
                    <button
                      key={m.id}
                      onClick={() => onSetIvMode(m.id)}
                      className={`py-1 text-[11px] font-mono uppercase rounded transition-all ${
                        ivMode === m.id
                          ? 'bg-cyan-500 text-slate-950 font-bold shadow'
                          : 'text-slate-400 hover:text-white'
                      }`}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Space Mode */}
              <div>
                <label className="text-xs font-mono text-slate-300 block mb-2 font-medium">
                  Horizontal Dimension:
                </label>
                <div className="grid grid-cols-2 gap-1 p-1 rounded-lg bg-slate-900 border border-slate-800">
                  {[
                    { id: 'strike', label: 'Strike (₹)' },
                    { id: 'moneyness', label: 'Moneyness (K/S)' },
                  ].map((s) => (
                    <button
                      key={s.id}
                      onClick={() => onSetSpaceMode(s.id)}
                      className={`py-1 text-xs font-mono rounded transition-all ${
                        spaceMode === s.id
                          ? 'bg-cyan-500 text-slate-950 font-bold'
                          : 'text-slate-400 hover:text-white'
                      }`}
                    >
                      {s.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* 0-DTE Switch */}
              <div className="flex items-center justify-between p-3 rounded-lg bg-slate-900/60 border border-slate-800">
                <div>
                  <div className="text-xs font-semibold text-white">Include 0-DTE Expiries</div>
                  <div className="text-[10px] text-slate-400">Include intraday expiring contracts</div>
                </div>
                <button
                  onClick={() => onSetIncludeZeroDte(!includeZeroDte)}
                  className={`w-10 h-6 flex items-center rounded-full p-1 transition-colors ${
                    includeZeroDte ? 'bg-cyan-500' : 'bg-slate-700'
                  }`}
                >
                  <div
                    className={`bg-white w-4 h-4 rounded-full shadow-md transform transition-transform ${
                      includeZeroDte ? 'translate-x-4' : 'translate-x-0'
                    }`}
                  />
                </button>
              </div>
            </div>
          </div>

          {/* Footer */}
          <div className="pt-4 border-t border-slate-800">
            <button
              onClick={onClose}
              className="w-full py-2.5 rounded-lg bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold text-xs uppercase tracking-wider transition-colors shadow-lg shadow-cyan-500/20"
            >
              Apply Parameters
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
