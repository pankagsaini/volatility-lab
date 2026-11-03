import React from 'react';
import { Activity, Sun, Moon, Sliders, RefreshCw } from 'lucide-react';
import { MarketSummary } from '../types';

interface NavbarProps {
  selectedSymbol: string;
  onSelectSymbol: (symbol: string) => void;
  summary: MarketSummary | null;
  currentTime: string;
  isDark: boolean;
  onToggleTheme: () => void;
  onOpenDrawer: () => void;
  onRefresh: () => void;
  isRefreshing: boolean;
}

export const Navbar: React.FC<NavbarProps> = ({
  selectedSymbol,
  onSelectSymbol,
  summary,
  currentTime,
  isDark,
  onToggleTheme,
  onOpenDrawer,
  onRefresh,
  isRefreshing,
}) => {
  const isMarketOpen = summary?.market_open ?? false;

  return (
    <header className="sticky top-0 z-40 w-full border-b border-slate-800 bg-[#080c14]/90 backdrop-blur-md px-4 py-2.5 transition-colors">
      <div className="max-w-[1720px] mx-auto flex items-center justify-between gap-4">
        {/* Left: Branding & Instrument Selector */}
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-cyan-500 to-blue-600 flex items-center justify-center shadow-lg shadow-cyan-500/20">
              <Activity className="w-5 h-5 text-white" />
            </div>
            <div>
              <span className="font-bold text-base tracking-tight text-white flex items-center gap-1.5">
                VOLATILITY LAB
                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
                  PRO SaaS
                </span>
              </span>
            </div>
          </div>

          {/* Instrument Toggle Segmented Control */}
          <div className="flex items-center p-0.5 rounded-lg bg-slate-900 border border-slate-800">
            {['NIFTY', 'BANKNIFTY'].map((sym) => {
              const active = selectedSymbol === sym;
              return (
                <button
                  key={sym}
                  onClick={() => onSelectSymbol(sym)}
                  className={`px-3 py-1 text-xs font-semibold rounded-md transition-all ${
                    active
                      ? 'bg-cyan-500 text-slate-950 shadow-md shadow-cyan-500/30'
                      : 'text-slate-400 hover:text-white'
                  }`}
                >
                  {sym}
                </button>
              );
            })}
          </div>
        </div>

        {/* Right: Market Status, IST Clock, Theme Toggle, Settings Drawer */}
        <div className="flex items-center gap-3">
          {/* Live Status Pill */}
          <div
            className={`flex items-center gap-2 px-2.5 py-1 rounded-full text-xs font-mono font-medium border ${
              isMarketOpen
                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                : 'bg-slate-800/60 text-slate-400 border-slate-700/50'
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                isMarketOpen ? 'bg-emerald-400 animate-pulse' : 'bg-slate-500'
              }`}
            />
            {summary?.market_status || (isMarketOpen ? '● NSE LIVE' : '○ NSE CLOSED')}
          </div>

          {/* IST Clock */}
          <div className="hidden sm:flex items-center px-2.5 py-1 rounded-md bg-slate-900 border border-slate-800 font-mono text-xs text-slate-300">
            {currentTime || '00:00:00 IST'}
          </div>

          {/* Manual Refresh Button */}
          <button
            onClick={onRefresh}
            title="Refresh Market Data"
            className="p-1.5 rounded-lg bg-slate-900 border border-slate-800 text-slate-400 hover:text-cyan-400 transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin text-cyan-400' : ''}`} />
          </button>

          {/* Dark / Light Mode Toggle */}
          <button
            onClick={onToggleTheme}
            title="Toggle Dark/Light Mode"
            className="p-1.5 rounded-lg bg-slate-900 border border-slate-800 text-slate-400 hover:text-white transition-colors"
          >
            {isDark ? <Sun className="w-4 h-4 text-amber-400" /> : <Moon className="w-4 h-4 text-indigo-400" />}
          </button>

          {/* Collapsible Drawer Trigger */}
          <button
            onClick={onOpenDrawer}
            className="flex items-center gap-1.5 px-3 py-1 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-800 text-xs font-medium text-slate-300 hover:text-white transition-colors"
          >
            <Sliders className="w-3.5 h-3.5 text-cyan-400" />
            <span>Parameters</span>
          </button>
        </div>
      </div>
    </header>
  );
};
