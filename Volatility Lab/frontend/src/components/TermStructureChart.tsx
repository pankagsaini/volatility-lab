import React, { useEffect, useRef } from 'react';
import { Calendar } from 'lucide-react';
import { VolSurfacePayload } from '../types';

interface TermStructureChartProps {
  data: VolSurfacePayload | null;
  isDark: boolean;
}

export const TermStructureChart: React.FC<TermStructureChartProps> = ({ data, isDark }) => {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || !data || !data.term_structure) return;
    const Plotly = (window as any).Plotly;
    if (!Plotly) return;

    const bgColor = isDark ? '#0f1726' : '#ffffff';
    const textColor = isDark ? '#e2e8f0' : '#1e293b';
    const gridColor = isDark ? '#1e293b' : '#e2e8f0';

    const trace = {
      x: data.term_structure.dtes,
      y: data.term_structure.ivs,
      text: data.term_structure.expiries,
      mode: 'lines+markers+text',
      textposition: 'top center',
      textfont: { color: '#00e5ff', size: 9 },
      line: { color: '#00e5ff', width: 2.5, shape: 'spline' },
      marker: { size: 6, color: '#ffffff', line: { color: '#00e5ff', width: 2 } },
      name: 'ATM Term Structure',
    };

    const layout = {
      paper_bgcolor: bgColor,
      plot_bgcolor: bgColor,
      margin: { l: 45, r: 20, t: 30, b: 40 },
      xaxis: {
        title: { text: 'Days to Expiry (DTE)', font: { color: textColor, size: 11 } },
        gridcolor: gridColor,
        tickfont: { color: textColor, size: 10 },
      },
      yaxis: {
        title: { text: 'ATM Implied Vol (%)', font: { color: textColor, size: 11 } },
        gridcolor: gridColor,
        tickfont: { color: textColor, size: 10 },
      },
      showlegend: false,
    };

    Plotly.react(containerRef.current, [trace], layout, { responsive: true, displayModeBar: false });
  }, [data, isDark]);

  const regime = data?.term_structure?.regime || 'Normal Contango';
  const isContango = regime.toLowerCase().includes('contango');

  return (
    <div className="glass-panel p-4 rounded-xl border border-slate-800">
      <div className="flex items-center justify-between mb-3 pb-2 border-b border-slate-800">
        <div className="flex items-center gap-2">
          <Calendar className="w-4 h-4 text-cyan-400" />
          <h2 className="text-sm font-semibold tracking-wide text-white uppercase">
            ATM Term Structure Curve
          </h2>
        </div>
        <span
          className={`text-[10px] font-mono px-2 py-0.5 rounded font-semibold border ${
            isContango
              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
              : 'bg-rose-500/10 text-rose-400 border-rose-500/30'
          }`}
        >
          {regime}
        </span>
      </div>

      <div className="w-full h-[320px]">
        <div ref={containerRef} className="w-full h-full rounded-lg overflow-hidden" />
      </div>
    </div>
  );
};
