import React, { useEffect, useRef } from 'react';
import { TrendingUp } from 'lucide-react';
import { VolSurfacePayload } from '../types';

interface SmileChartProps {
  data: VolSurfacePayload | null;
  isDark: boolean;
}

export const SmileChart: React.FC<SmileChartProps> = ({ data, isDark }) => {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || !data || !data.smile_curves) return;
    const Plotly = (window as any).Plotly;
    if (!Plotly) return;

    const bgColor = isDark ? '#0f1726' : '#ffffff';
    const textColor = isDark ? '#e2e8f0' : '#1e293b';
    const gridColor = isDark ? '#1e293b' : '#e2e8f0';

    const colors = ['#00e5ff', '#10b981', '#f59e0b', '#ec4899', '#8b5cf6', '#3b82f6'];

    const traces = data.smile_curves.slice(0, 6).map((curve, idx) => ({
      x: curve.strikes,
      y: curve.ivs,
      mode: 'lines+markers',
      name: `${curve.expiry} (${curve.dte.toFixed(1)}d)`,
      line: {
        color: colors[idx % colors.length],
        width: 2.2,
        shape: 'spline',
      },
      marker: { size: 4 },
    }));

    // Vertical ATM line shape
    const shapes = data.spot
      ? [
          {
            type: 'line',
            x0: data.spot,
            x1: data.spot,
            y0: 0,
            y1: 1,
            yref: 'paper',
            line: {
              color: 'rgba(255, 255, 255, 0.4)',
              width: 1.5,
              dash: 'dash',
            },
          },
        ]
      : [];

    const layout = {
      paper_bgcolor: bgColor,
      plot_bgcolor: bgColor,
      margin: { l: 45, r: 20, t: 30, b: 40 },
      xaxis: {
        title: { text: 'Strike Price (₹)', font: { color: textColor, size: 11 } },
        gridcolor: gridColor,
        tickfont: { color: textColor, size: 10 },
      },
      yaxis: {
        title: { text: 'Implied Volatility (%)', font: { color: textColor, size: 11 } },
        gridcolor: gridColor,
        tickfont: { color: textColor, size: 10 },
      },
      shapes: shapes,
      showlegend: true,
      legend: {
        font: { color: textColor, size: 10 },
        orientation: 'h',
        x: 0,
        y: 1.15,
      },
    };

    Plotly.react(containerRef.current, traces, layout, { responsive: true, displayModeBar: false });
  }, [data, isDark]);

  return (
    <div className="glass-panel p-4 rounded-xl border border-slate-800">
      <div className="flex items-center justify-between mb-3 pb-2 border-b border-slate-800">
        <div className="flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-cyan-400" />
          <h2 className="text-sm font-semibold tracking-wide text-white uppercase">
            Opstra 2D Multi-Expiry Smile Overlay
          </h2>
        </div>
        <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300">
          ATM Ref: ₹{data?.spot ? data.spot.toLocaleString('en-IN') : '—'}
        </span>
      </div>

      <div className="w-full h-[320px]">
        <div ref={containerRef} className="w-full h-full rounded-lg overflow-hidden" />
      </div>
    </div>
  );
};
