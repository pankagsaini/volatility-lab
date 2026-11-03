import React, { useEffect, useRef } from 'react';
import { TrendingUp, Target } from 'lucide-react';
import { VolSurfacePayload } from '../types';

interface SmileChartProps {
  data: VolSurfacePayload | null;
  isDark: boolean;
  selectedExpiry?: string;
}

export const SmileChart: React.FC<SmileChartProps> = ({ data, isDark, selectedExpiry }) => {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || !data || !data.smile_curves) return;
    const Plotly = (window as any).Plotly;
    if (!Plotly) return;

    const bgColor = isDark ? '#0f1726' : '#ffffff';
    const textColor = isDark ? '#e2e8f0' : '#1e293b';
    const gridColor = isDark ? '#1e293b' : '#e2e8f0';

    const colors = ['#38bdf8', '#10b981', '#f59e0b', '#ec4899', '#8b5cf6', '#a855f7'];

    // Ensure the selected expiry is included in curves to plot
    let curvesToPlot = [...data.smile_curves.slice(0, 6)];
    const focusExp = selectedExpiry || data.selected_expiry;
    if (focusExp) {
      const match = data.smile_curves.find((c) => c.expiry === focusExp);
      if (match && !curvesToPlot.some((c) => c.expiry === match.expiry)) {
        curvesToPlot.push(match);
      }
    }

    const traces = curvesToPlot.map((curve, idx) => {
      const isSelected = focusExp ? curve.expiry === focusExp : idx === 0;
      return {
        x: curve.strikes,
        y: curve.ivs,
        mode: 'lines+markers',
        name: isSelected
          ? `★ ${curve.expiry} (${curve.dte.toFixed(1)}d) [SELECTED]`
          : `${curve.expiry} (${curve.dte.toFixed(1)}d)`,
        line: {
          color: isSelected ? '#00e5ff' : colors[idx % colors.length],
          width: isSelected ? 3.5 : 1.8,
          shape: 'spline',
        },
        marker: {
          size: isSelected ? 6 : 3.5,
          color: isSelected ? '#00e5ff' : colors[idx % colors.length],
        },
        opacity: isSelected ? 1.0 : 0.7,
      };
    });

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
  }, [data, isDark, selectedExpiry]);

  const activeFocus = selectedExpiry || data?.selected_expiry;

  return (
    <div className="glass-panel p-4 rounded-xl border border-slate-800">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3 pb-2 border-b border-slate-800">
        <div className="flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-cyan-400" />
          <h2 className="text-sm font-semibold tracking-wide text-white uppercase">
            Opstra 2D Multi-Expiry Smile Overlay
          </h2>
          {activeFocus && (
            <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 flex items-center gap-1">
              <Target className="w-3 h-3" />
              Focus: {activeFocus}
            </span>
          )}
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
