import React, { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, IChartApi, ISeriesApi, CandlestickData, HistogramData } from 'lightweight-charts';
import { Maximize2, BarChart2 } from 'lucide-react';
import { CandlePayload } from '../types';

interface TradingViewChartProps {
  symbol: string;
  isDark: boolean;
}

export const TradingViewChart: React.FC<TradingViewChartProps> = ({ symbol, isDark }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);

  const [candleData, setCandleData] = useState<CandlePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTimeframe, setActiveTimeframe] = useState('1D');

  // Fetch candle data
  useEffect(() => {
    let mounted = true;
    setLoading(true);
    fetch(`/api/candles?symbol=${symbol}`)
      .then((r) => r.json())
      .then((res: CandlePayload) => {
        if (mounted) {
          setCandleData(res);
          setLoading(false);
        }
      })
      .catch((err) => {
        console.error('Failed to load candle data:', err);
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, [symbol]);

  // Initialize and update TradingView Lightweight Chart
  useEffect(() => {
    if (!containerRef.current) return;

    const bgColor = isDark ? '#0f1726' : '#ffffff';
    const textColor = isDark ? '#94a3b8' : '#64748b';
    const gridColor = isDark ? 'rgba(30, 41, 59, 0.5)' : 'rgba(226, 232, 240, 0.8)';
    const borderColor = isDark ? '#1e293b' : '#e2e8f0';

    if (!chartRef.current) {
      const chart = createChart(containerRef.current, {
        width: containerRef.current.clientWidth,
        height: 420,
        layout: {
          background: { type: ColorType.Solid, color: bgColor },
          textColor: textColor,
          fontFamily: 'Inter, sans-serif',
          fontSize: 11,
        },
        grid: {
          vertLines: { color: gridColor },
          horzLines: { color: gridColor },
        },
        crosshair: {
          vertLine: { color: '#00e5ff', width: 1, style: 2, labelBackgroundColor: '#00e5ff' },
          horzLine: { color: '#00e5ff', width: 1, style: 2, labelBackgroundColor: '#00e5ff' },
        },
        rightPriceScale: {
          borderColor: borderColor,
          autoScale: true,
        },
        timeScale: {
          borderColor: borderColor,
          timeVisible: true,
          secondsVisible: false,
        },
      });

      const volumeSeries = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: '', // overlay
      });
      volumeSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });

      const candleSeries = chart.addCandlestickSeries({
        upColor: '#10b981',
        downColor: '#f43f5e',
        borderVisible: false,
        wickUpColor: '#10b981',
        wickDownColor: '#f43f5e',
      });

      chartRef.current = chart;
      candleSeriesRef.current = candleSeries;
      volumeSeriesRef.current = volumeSeries;

      const handleResize = () => {
        if (containerRef.current && chartRef.current) {
          chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
        }
      };
      window.addEventListener('resize', handleResize);
    } else {
      chartRef.current.applyOptions({
        layout: {
          background: { type: ColorType.Solid, color: bgColor },
          textColor: textColor,
        },
        grid: {
          vertLines: { color: gridColor },
          horzLines: { color: gridColor },
        },
        rightPriceScale: { borderColor: borderColor },
        timeScale: { borderColor: borderColor },
      });
    }

    if (candleData && candleData.candles.length > 0) {
      candleSeriesRef.current?.setData(candleData.candles as unknown as CandlestickData[]);
      volumeSeriesRef.current?.setData(candleData.volumes as unknown as HistogramData[]);
      chartRef.current?.timeScale().fitContent();
    }
  }, [candleData, isDark]);

  return (
    <div className="glass-panel p-4 rounded-xl border border-slate-800">
      <div className="flex items-center justify-between mb-3 pb-2 border-b border-slate-800">
        <div className="flex items-center gap-2">
          <BarChart2 className="w-4 h-4 text-cyan-400" />
          <h2 className="text-sm font-semibold tracking-wide text-white uppercase">
            TradingView Candlestick & Volume — {symbol}
          </h2>
          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300">
            {candleData?.count ?? 0} Daily Bars
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* Timeframe pill selectors */}
          <div className="flex items-center p-0.5 rounded bg-slate-900 border border-slate-800 text-xs font-mono">
            {['1D', '1W', '1M'].map((tf) => (
              <button
                key={tf}
                onClick={() => setActiveTimeframe(tf)}
                className={`px-2.5 py-0.5 rounded transition-all ${
                  activeTimeframe === tf
                    ? 'bg-cyan-500 text-slate-950 font-bold'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {tf}
              </button>
            ))}
          </div>

          <button
            onClick={() => chartRef.current?.timeScale().fitContent()}
            title="Reset Zoom"
            className="p-1 rounded bg-slate-900 border border-slate-800 text-slate-400 hover:text-cyan-400 text-xs"
          >
            <Maximize2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      <div className="relative w-full h-[420px]">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-950/60 z-10 backdrop-blur-sm">
            <span className="text-xs font-mono text-cyan-400 animate-pulse">
              Loading Candlestick Feed...
            </span>
          </div>
        )}
        <div ref={containerRef} className="w-full h-full rounded-lg overflow-hidden" />
      </div>
    </div>
  );
};
