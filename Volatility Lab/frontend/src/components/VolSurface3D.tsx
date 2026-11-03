import React, { useEffect, useRef, useState } from 'react';
import { Box, Layers, Eye } from 'lucide-react';
import { VolSurfacePayload } from '../types';

interface VolSurface3DProps {
  symbol: string;
  isDark: boolean;
  surfaceRange: number;
  ivMode: string;
  spaceMode: string;
  includeZeroDte: boolean;
}

export const VolSurface3D: React.FC<VolSurface3DProps> = ({
  symbol,
  isDark,
  surfaceRange,
  ivMode,
  spaceMode,
  includeZeroDte,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<VolSurfacePayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    setLoading(true);

    const query = new URLSearchParams({
      symbol,
      mode: ivMode,
      space: spaceMode,
      range: surfaceRange.toString(),
      include_0dte: includeZeroDte.toString(),
    });

    fetch(`/api/vol-surface?${query.toString()}`)
      .then((r) => r.json())
      .then((res: VolSurfacePayload) => {
        if (mounted) {
          setData(res);
          setLoading(false);
        }
      })
      .catch((err) => {
        console.error('Failed to load vol surface:', err);
        if (mounted) setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, [symbol, surfaceRange, ivMode, spaceMode, includeZeroDte]);

  useEffect(() => {
    if (!containerRef.current || !data || !data.z_matrix) return;
    const Plotly = (window as any).Plotly;
    if (!Plotly) return;

    const bgColor = isDark ? '#0f1726' : '#ffffff';
    const textColor = isDark ? '#e2e8f0' : '#1e293b';
    const gridColor = isDark ? '#1e293b' : '#e2e8f0';

    // 1. Surface trace
    const surfaceTrace = {
      type: 'surface',
      x: data.x_grid,
      y: data.y_grid,
      z: data.z_matrix,
      colorscale: [
        [0.0, '#001026'],
        [0.2, '#0052cc'],
        [0.4, '#00b4d8'],
        [0.6, '#06d6a0'],
        [0.8, '#ffd166'],
        [1.0, '#ef476f'],
      ],
      contours: {
        x: { show: true, color: 'rgba(255,255,255,0.15)', width: 1 },
        y: { show: true, color: 'rgba(255,255,255,0.15)', width: 1 },
        z: { show: true, usecolormap: true, project: { z: true } },
      },
      lighting: { ambient: 0.8, diffuse: 0.9, specular: 0.2 },
      colorbar: {
        title: { text: 'Implied Vol (%)', font: { color: textColor, size: 11 } },
        tickfont: { color: textColor, size: 10 },
        len: 0.65,
        thickness: 14,
      },
      name: 'Implied Volatility Surface',
    };

    // 2. ATM Ridge Line
    const atmTrace = {
      type: 'scatter3d',
      mode: 'lines+markers',
      x: data.atm_ridge.x,
      y: data.atm_ridge.y,
      z: data.atm_ridge.z,
      line: { color: '#00e5ff', width: 6 },
      marker: { size: 4, color: '#ffffff' },
      name: 'ATM Ridge Spine',
    };

    const layout = {
      paper_bgcolor: bgColor,
      plot_bgcolor: bgColor,
      margin: { l: 10, r: 10, t: 10, b: 10 },
      scene: {
        xaxis: {
          title: { text: spaceMode === 'moneyness' ? 'Moneyness (K/S)' : 'Strike Price (₹)', font: { color: textColor, size: 11 } },
          gridcolor: gridColor,
          zerolinecolor: gridColor,
          tickfont: { color: textColor, size: 10 },
        },
        yaxis: {
          title: { text: 'Days to Expiry (DTE)', font: { color: textColor, size: 11 } },
          gridcolor: gridColor,
          zerolinecolor: gridColor,
          tickfont: { color: textColor, size: 10 },
        },
        zaxis: {
          title: { text: 'IV (%)', font: { color: textColor, size: 11 } },
          gridcolor: gridColor,
          zerolinecolor: gridColor,
          tickfont: { color: textColor, size: 10 },
        },
        camera: {
          eye: { x: -1.6, y: -1.7, z: 1.2 },
        },
      },
      showlegend: true,
      legend: {
        font: { color: textColor, size: 10 },
        orientation: 'h',
        x: 0.05,
        y: 0.98,
      },
    };

    const config = {
      responsive: true,
      displayModeBar: false,
    };

    Plotly.react(containerRef.current, [surfaceTrace, atmTrace], layout, config);
  }, [data, isDark, spaceMode]);

  return (
    <div className="glass-panel p-4 rounded-xl border border-slate-800">
      <div className="flex items-center justify-between mb-3 pb-2 border-b border-slate-800">
        <div className="flex items-center gap-2">
          <Box className="w-4 h-4 text-cyan-400" />
          <h2 className="text-sm font-semibold tracking-wide text-white uppercase">
            Opstra / Definedge 3D Volatility Surface — Strike × DTE × IV
          </h2>
          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
            ATM Ridge Illuminated
          </span>
        </div>

        <div className="flex items-center gap-2 text-xs font-mono text-slate-400">
          <Layers className="w-3.5 h-3.5 text-cyan-400" />
          <span>ATM: ₹{data?.spot ? data.spot.toLocaleString('en-IN') : '—'}</span>
        </div>
      </div>

      <div className="relative w-full h-[520px]">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-950/60 z-10 backdrop-blur-sm">
            <span className="text-xs font-mono text-cyan-400 animate-pulse">
              Computing 3D Volatility Mesh & ATM Ridge...
            </span>
          </div>
        )}
        <div ref={containerRef} className="w-full h-full rounded-lg overflow-hidden" />
      </div>
    </div>
  );
};
