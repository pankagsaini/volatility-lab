import React, { useEffect, useRef, useState } from 'react';
import { Box, Layers, Target } from 'lucide-react';
import { VolSurfacePayload } from '../types';

interface VolSurface3DProps {
  data?: VolSurfacePayload | null;
  symbol: string;
  isDark: boolean;
  surfaceRange: number;
  ivMode: string;
  spaceMode: string;
  includeZeroDte: boolean;
  selectedExpiry?: string;
}

export const VolSurface3D: React.FC<VolSurface3DProps> = ({
  data: propData,
  symbol,
  isDark,
  surfaceRange,
  ivMode,
  spaceMode,
  includeZeroDte,
  selectedExpiry,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [internalData, setInternalData] = useState<VolSurfacePayload | null>(null);
  const [loading, setLoading] = useState(true);

  const data = propData !== undefined ? propData : internalData;

  useEffect(() => {
    if (propData !== undefined) {
      setLoading(false);
      return;
    }

    let mounted = true;
    setLoading(true);

    const query = new URLSearchParams({
      symbol,
      mode: ivMode,
      space: spaceMode,
      range: surfaceRange.toString(),
      include_0dte: includeZeroDte.toString(),
    });
    if (selectedExpiry) {
      query.append('selected_expiry', selectedExpiry);
    }

    fetch(`/api/vol-surface?${query.toString()}`)
      .then((r) => r.json())
      .then((res: VolSurfacePayload) => {
        if (mounted) {
          setInternalData(res);
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
  }, [propData, symbol, surfaceRange, ivMode, spaceMode, includeZeroDte, selectedExpiry]);

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

    // 3. Highlighted Selected Expiry Cross-Section Curve
    const focusExp = selectedExpiry || data.selected_expiry;
    let selectedCurveTrace: any = null;
    if (focusExp && data.smile_curves) {
      const match = data.smile_curves.find((c) => c.expiry === focusExp);
      if (match && match.strikes && match.strikes.length > 0) {
        selectedCurveTrace = {
          type: 'scatter3d',
          mode: 'lines+markers',
          x: match.strikes,
          y: Array(match.strikes.length).fill(match.dte),
          z: match.ivs,
          line: { color: '#ffb703', width: 7 },
          marker: { size: 4.5, color: '#ffffff' },
          name: `Focus: ${match.expiry} (${match.dte.toFixed(1)}d)`,
        };
      }
    }

    const layout = {
      paper_bgcolor: bgColor,
      plot_bgcolor: bgColor,
      margin: { l: 10, r: 10, t: 10, b: 10 },
      scene: {
        xaxis: {
          title: {
            text: spaceMode === 'moneyness' ? 'Moneyness (K/S)' : 'Strike Price (₹)',
            font: { color: textColor, size: 11 },
          },
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

    const traces = [surfaceTrace, atmTrace];
    if (selectedCurveTrace) traces.push(selectedCurveTrace);

    Plotly.react(containerRef.current, traces, layout, config);
  }, [data, isDark, spaceMode, selectedExpiry]);

  const activeFocus = selectedExpiry || data?.selected_expiry;

  return (
    <div className="glass-panel p-4 rounded-xl border border-slate-800">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3 pb-2 border-b border-slate-800">
        <div className="flex items-center gap-2">
          <Box className="w-4 h-4 text-cyan-400" />
          <h2 className="text-sm font-semibold tracking-wide text-white uppercase">
            Opstra / Definedge 3D Volatility Surface — Strike × DTE × IV
          </h2>
          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
            ATM Ridge Illuminated
          </span>
          {activeFocus && (
            <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/30 flex items-center gap-1">
              <Target className="w-3 h-3" />
              Focus: {activeFocus} {data?.selected_dte != null ? `(${data.selected_dte.toFixed(1)}d)` : ''}
            </span>
          )}
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
