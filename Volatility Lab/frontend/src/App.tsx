import React, { useState, useEffect, useCallback } from 'react';
import {
  Box,
  Layers,
  Zap,
  Target,
  BarChart2,
  FlaskConical,
  ArrowLeftRight,
} from 'lucide-react';
import { Navbar } from './components/Navbar';
import { TickerRibbon } from './components/TickerRibbon';
import { VolSurface3D } from './components/VolSurface3D';
import { SmileChart } from './components/SmileChart';
import { TermStructureChart } from './components/TermStructureChart';
import { OptionChainTable } from './components/OptionChainTable';
import { TradeRadarView } from './components/TradeRadarView';
import { TradingViewChart } from './components/TradingViewChart';
import { BacktestRunnerView } from './components/BacktestRunnerView';
import { FuturesCarryView } from './components/FuturesCarryView';
import { ContractInspectorModal } from './components/ContractInspectorModal';
import { DrawerInspector } from './components/DrawerInspector';
import { MarketSummary, ExpiryItem, VolSurfacePayload } from './types';

export const App: React.FC = () => {
  // Global State
  const [selectedSymbol, setSelectedSymbol] = useState<'NIFTY' | 'BANKNIFTY'>('NIFTY');
  const [activeTab, setActiveTab] = useState<
    'surface' | 'chain' | 'radar' | 'candles' | 'backtest' | 'carry'
  >('surface');
  const [isDark, setIsDark] = useState(true);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Market & Surface Parameters
  const [surfaceRange, setSurfaceRange] = useState(8.0);
  const [ivMode, setIvMode] = useState('otm');
  const [spaceMode, setSpaceMode] = useState('strike');
  const [includeZeroDte, setIncludeZeroDte] = useState(true);

  // Data States
  const [summary, setSummary] = useState<MarketSummary | null>(null);
  const [currentTime, setCurrentTime] = useState('');
  const [expiries, setExpiries] = useState<ExpiryItem[]>([]);
  const [selectedExpiry, setSelectedExpiry] = useState('');
  const [surfaceData, setSurfaceData] = useState<VolSurfacePayload | null>(null);

  // Contract Inspector Modal State
  const [inspectedContract, setInspectedContract] = useState<any | null>(null);

  // Theme Toggle
  const handleToggleTheme = () => {
    setIsDark((prev) => {
      const next = !prev;
      if (next) {
        document.documentElement.classList.add('dark');
        document.documentElement.classList.remove('light');
      } else {
        document.documentElement.classList.remove('dark');
        document.documentElement.classList.add('light');
      }
      return next;
    });
  };

  // Clock Update (1s ticker)
  useEffect(() => {
    const timer = setInterval(() => {
      const now = new Date();
      setCurrentTime(
        now.toLocaleTimeString('en-GB', {
          timeZone: 'Asia/Kolkata',
          hour12: false,
        }) + ' IST'
      );
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  // Fetch Market Summary
  const refreshMarketSummary = useCallback(() => {
    setIsRefreshing(true);
    fetch('/api/market-summary')
      .then((r) => r.json())
      .then((data: MarketSummary) => {
        setSummary(data);
        setIsRefreshing(false);
      })
      .catch((err) => {
        console.error('Failed to load market summary:', err);
        setIsRefreshing(false);
      });
  }, []);

  // Fetch Expiries for Instrument
  const refreshExpiries = useCallback((symbol: string) => {
    fetch(`/api/expiries?symbol=${symbol}`)
      .then((r) => r.json())
      .then((res: { expiries: ExpiryItem[] }) => {
        const list = res.expiries || [];
        setExpiries(list);
        if (list.length > 0) {
          setSelectedExpiry((prev) => {
            const exists = list.some((e) => e.expiry === prev);
            return exists ? prev : list[0].expiry;
          });
        }
      })
      .catch((err) => console.error('Failed to load expiries:', err));
  }, []);

  // Fetch Vol Surface Payload for Smile & Term Structure
  const refreshSurfaceData = useCallback(
    (symbol: string, range: number, mode: string, space: string, z0: boolean, expiry?: string) => {
      const query = new URLSearchParams({
        symbol,
        mode,
        space,
        range: range.toString(),
        include_0dte: z0.toString(),
      });
      if (expiry) {
        query.append('selected_expiry', expiry);
      }
      fetch(`/api/vol-surface?${query.toString()}`)
        .then((r) => r.json())
        .then((data: VolSurfacePayload) => setSurfaceData(data))
        .catch((err) => console.error('Failed to load surface data:', err));
    },
    []
  );

  // Initial Load & Symbol Change
  useEffect(() => {
    refreshMarketSummary();
    refreshExpiries(selectedSymbol);
  }, [selectedSymbol, refreshMarketSummary, refreshExpiries]);

  // Synchronized Surface Reload when symbol, expiry, or parameters change
  useEffect(() => {
    refreshSurfaceData(
      selectedSymbol,
      surfaceRange,
      ivMode,
      spaceMode,
      includeZeroDte,
      selectedExpiry
    );
  }, [
    selectedSymbol,
    selectedExpiry,
    refreshSurfaceData,
    surfaceRange,
    ivMode,
    spaceMode,
    includeZeroDte,
  ]);

  // WebSocket for Live Market Tick Streaming
  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    let ws: WebSocket | null = null;
    let reconnectTimer: any = null;
    let isMounted = true;

    const connectWs = () => {
      if (!isMounted) return;
      try {
        ws = new WebSocket(wsUrl);
        ws.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data);
            if (msg.type === 'market_tick' && msg.data) {
              setSummary(msg.data);
            }
          } catch (e) {
            // ignore parsing error
          }
        };
        ws.onerror = () => {
          if (ws) ws.close();
        };
        ws.onclose = () => {
          if (isMounted) {
            reconnectTimer = setTimeout(connectWs, 5000);
          }
        };
      } catch (e) {
        // fallback to polling if WS not available
      }
    };

    connectWs();

    const interval = setInterval(refreshMarketSummary, 20000);
    return () => {
      isMounted = false;
      clearInterval(interval);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [refreshMarketSummary]);

  return (
    <div className="min-h-screen bg-[#080c14] text-[#e2e8f0] flex flex-col transition-colors selection:bg-cyan-500/30 selection:text-cyan-200">
      {/* Top Sticky Header with Symbol & Global Expiry Sync */}
      <Navbar
        selectedSymbol={selectedSymbol}
        onSelectSymbol={(sym) => setSelectedSymbol(sym as 'NIFTY' | 'BANKNIFTY')}
        selectedExpiry={selectedExpiry}
        onSelectExpiry={setSelectedExpiry}
        expiries={expiries}
        summary={summary}
        currentTime={currentTime}
        isDark={isDark}
        onToggleTheme={handleToggleTheme}
        onOpenDrawer={() => setIsDrawerOpen(true)}
        onRefresh={refreshMarketSummary}
        isRefreshing={isRefreshing}
      />

      <main className="flex-1 max-w-[1720px] w-full mx-auto px-4 py-4 space-y-4">
        {/* Ticker Ribbon */}
        <TickerRibbon summary={summary} />

        {/* Tab Navigation Segmented Bar */}
        <div className="flex items-center gap-1.5 p-1 rounded-xl bg-slate-900/80 border border-slate-800/80 backdrop-blur-md overflow-x-auto">
          {[
            { id: 'surface', label: 'Vol Surface', icon: Box },
            { id: 'chain', label: 'Option Chain', icon: Zap },
            { id: 'radar', label: 'Trade Radar', icon: Target },
            { id: 'candles', label: 'Candlesticks', icon: BarChart2 },
            { id: 'backtest', label: 'Backtest', icon: FlaskConical },
            { id: 'carry', label: 'Futures Carry', icon: ArrowLeftRight },
          ].map((tab) => {
            const active = activeTab === tab.id;
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as any)}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-semibold whitespace-nowrap transition-all ${
                  active
                    ? 'bg-gradient-to-r from-cyan-500 to-blue-600 text-slate-950 font-bold shadow-lg shadow-cyan-500/20'
                    : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
                }`}
              >
                <Icon className={`w-3.5 h-3.5 ${active ? 'text-slate-950' : 'text-cyan-400'}`} />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>

        {/* Tab Contents: Instant Seamless Switching */}
        {activeTab === 'surface' && (
          <div className="space-y-4 animate-fade-in">
            {/* 3D Surface with Active Expiry Cross-Section Highlight */}
            <VolSurface3D
              data={surfaceData}
              symbol={selectedSymbol}
              isDark={isDark}
              surfaceRange={surfaceRange}
              ivMode={ivMode}
              spaceMode={spaceMode}
              includeZeroDte={includeZeroDte}
              selectedExpiry={selectedExpiry}
            />

            {/* 2D Smiles & Term Structure side-by-side */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <SmileChart data={surfaceData} isDark={isDark} selectedExpiry={selectedExpiry} />
              <TermStructureChart data={surfaceData} isDark={isDark} selectedExpiry={selectedExpiry} />
            </div>
          </div>
        )}

        {activeTab === 'chain' && (
          <div className="animate-fade-in">
            <OptionChainTable
              symbol={selectedSymbol}
              selectedExpiry={selectedExpiry}
              onSelectExpiry={setSelectedExpiry}
              expiries={expiries}
              onInspectContract={setInspectedContract}
            />
          </div>
        )}

        {activeTab === 'radar' && (
          <div className="animate-fade-in">
            <TradeRadarView
              symbol={selectedSymbol}
              selectedExpiry={selectedExpiry}
              expiries={expiries}
              onSelectExpiry={setSelectedExpiry}
            />
          </div>
        )}

        {activeTab === 'candles' && (
          <div className="animate-fade-in">
            <TradingViewChart symbol={selectedSymbol} isDark={isDark} />
          </div>
        )}

        {activeTab === 'backtest' && (
          <div className="animate-fade-in">
            <BacktestRunnerView />
          </div>
        )}

        {activeTab === 'carry' && (
          <div className="animate-fade-in">
            <FuturesCarryView />
          </div>
        )}
      </main>

      {/* Contract Inspector Modal */}
      <ContractInspectorModal
        contract={inspectedContract}
        onClose={() => setInspectedContract(null)}
      />

      {/* Floating Parameters Drawer */}
      <DrawerInspector
        isOpen={isDrawerOpen}
        onClose={() => setIsDrawerOpen(false)}
        surfaceRange={surfaceRange}
        onSetSurfaceRange={setSurfaceRange}
        ivMode={ivMode}
        onSetIvMode={setIvMode}
        spaceMode={spaceMode}
        onSetSpaceMode={setSpaceMode}
        includeZeroDte={includeZeroDte}
        onSetIncludeZeroDte={setIncludeZeroDte}
      />
    </div>
  );
};
