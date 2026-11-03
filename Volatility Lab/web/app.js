/**
 * Volatility Lab · Opstra/Definedge-Inspired Web Application Engine
 * Renders 3D Volatility Surface, 2D Smile Overlay, and Opstra Option Chain.
 */

let state = {
    symbol: 'NIFTY',
    activeTab: 'surface',
    spaceMode: 'strike',
    ivMode: 'otm',
    strikeRange: 8,
    includeZeroDte: true,
    cameraPreset: 'default',
    selectedExpiry: null
};

// Initial boot
document.addEventListener('DOMContentLoaded', () => {
    initClock();
    refreshMarketSummary();
    loadActiveView();
    // Auto-refresh summary every 20 seconds
    setInterval(refreshMarketSummary, 20000);
});

/* ==========================================================================
   Clock & Market Summary
   ========================================================================== */
function initClock() {
    function update() {
        const now = new Date();
        const istOffset = 5.5 * 60 * 60 * 1000;
        const istDate = new Date(now.getTime() + (now.getTimezoneOffset() * 60000) + istOffset);
        const dateStr = istDate.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
        const timeStr = istDate.toLocaleTimeString('en-GB', { hour12: false });
        const el = document.getElementById('ist-clock');
        if (el) el.innerText = `${dateStr} ${timeStr} IST`;
    }
    update();
    setInterval(update, 1000);
}

async function refreshMarketSummary() {
    try {
        const res = await fetch('/api/market-summary');
        const data = await res.json();
        if (data.error) return;

        // Session status pill
        const badge = document.getElementById('session-badge');
        const text = document.getElementById('session-text');
        if (badge && text) {
            badge.className = `session-pill ${data.status_type === 'live' ? 'live' : 'closed'}`;
            text.innerText = data.market_status;
        }

        // NIFTY Card
        const n = data.nifty;
        if (n && n.price) {
            document.getElementById('nifty-price').innerText = formatNum(n.price, 1);
            const chgEl = document.getElementById('nifty-chg');
            const isUp = (n.change_pct || 0) >= 0;
            chgEl.className = `ticker-change ${isUp ? 'up' : 'down'}`;
            chgEl.innerText = `${isUp ? '▲ +' : '▼ '}${formatNum(n.change_pct, 2)}%`;
            document.getElementById('nifty-iv').innerText = n.atm_iv ? `${formatNum(n.atm_iv, 1)}%` : '—';
            document.getElementById('nifty-sprd').innerText = n.iv_rv_spread != null ? `${n.iv_rv_spread >= 0 ? '+' : ''}${formatNum(n.iv_rv_spread, 1)} pp` : '—';
        }

        // BANKNIFTY Card
        const bn = data.banknifty;
        if (bn && bn.price) {
            document.getElementById('bn-price').innerText = formatNum(bn.price, 1);
            const chgEl = document.getElementById('bn-chg');
            const isUp = (bn.change_pct || 0) >= 0;
            chgEl.className = `ticker-change ${isUp ? 'up' : 'down'}`;
            chgEl.innerText = `${isUp ? '▲ +' : '▼ '}${formatNum(bn.change_pct, 2)}%`;
            document.getElementById('bn-iv').innerText = bn.atm_iv ? `${formatNum(bn.atm_iv, 1)}%` : '—';
            document.getElementById('bn-sprd').innerText = bn.iv_rv_spread != null ? `${bn.iv_rv_spread >= 0 ? '+' : ''}${formatNum(bn.iv_rv_spread, 1)} pp` : '—';
        }

        // VIX Card
        const vix = data.india_vix;
        if (vix && vix.value) {
            document.getElementById('vix-value').innerText = formatNum(vix.value, 2);
            document.getElementById('vix-regime').innerText = vix.regime || 'Normal Vol';
        }

        // Cloud sync card
        const cloud = data.cloud_sync;
        if (cloud) {
            document.getElementById('sync-files').innerText = (cloud.total_files || 0).toLocaleString();
            const syncEl = document.getElementById('sync-status');
            syncEl.innerText = `● ${(cloud.status || 'READY').toUpperCase()}`;
            syncEl.className = `ticker-change ${cloud.status === 'healthy' ? 'up' : 'gold'}`;
        }
    } catch (e) {
        console.error('Market summary fetch failed:', e);
    }
}

function refreshData() {
    refreshMarketSummary();
    loadActiveView();
}

/* ==========================================================================
   Navigation & Controls
   ========================================================================== */
function switchSymbol(symbol) {
    if (state.symbol === symbol) return;
    state.symbol = symbol;
    document.getElementById('btn-nifty').className = `inst-btn ${symbol === 'NIFTY' ? 'active' : ''}`;
    document.getElementById('btn-banknifty').className = `inst-btn ${symbol === 'BANKNIFTY' ? 'active' : ''}`;
    state.selectedExpiry = null;
    loadActiveView();
}

function switchTab(tabId) {
    state.activeTab = tabId;
    document.querySelectorAll('.nav-tabs .tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));
    
    const targetBtn = Array.from(document.querySelectorAll('.nav-tabs .tab-btn'))
        .find(b => b.getAttribute('onclick').includes(tabId));
    if (targetBtn) targetBtn.classList.add('active');

    const pane = document.getElementById(`tab-${tabId}`);
    if (pane) pane.classList.add('active');

    loadActiveView();
}

function loadActiveView() {
    if (state.activeTab === 'surface') loadVolSurface();
    else if (state.activeTab === 'chain') loadOptionChain();
    else if (state.activeTab === 'radar') loadTradeRadar();
    else if (state.activeTab === 'futures') loadFuturesCarry();
}

function setSpaceMode(mode) {
    state.spaceMode = mode;
    document.getElementById('space-strike').className = `seg-btn ${mode === 'strike' ? 'active' : ''}`;
    document.getElementById('space-moneyness').className = `seg-btn ${mode === 'moneyness' ? 'active' : ''}`;
    loadVolSurface();
}

function setIvMode(mode) {
    state.ivMode = mode;
    ['otm', 'call', 'put', 'mid'].forEach(m => {
        const btn = document.getElementById(`mode-${m}`);
        if (btn) btn.className = `seg-btn ${m === mode ? 'active' : ''}`;
    });
    loadVolSurface();
}

function setCamera(preset) {
    state.cameraPreset = preset;
    const cameraMap = {
        default: { eye: { x: 1.65, y: -1.65, z: 1.25 } },
        smile: { eye: { x: 0.0, y: -2.4, z: 0.15 } },   // Strike vs IV view
        term: { eye: { x: 2.4, y: 0.0, z: 0.15 } },    // DTE vs IV view
        topdown: { eye: { x: 0.0, y: 0.0, z: 2.6 } }   // Top-down heatmap
    };
    const cam = cameraMap[preset] || cameraMap.default;
    Plotly.relayout('plot-surface-3d', { 'scene.camera': cam });
}

function updateRange(val) {
    state.strikeRange = parseFloat(val);
    document.getElementById('range-label').innerText = `±${val}%`;
    loadVolSurface();
}

function toggleZeroDte(checked) {
    state.includeZeroDte = checked;
    loadVolSurface();
}

/* ==========================================================================
   TAB 1: 3D Volatility Surface & Smile
   ========================================================================== */
async function loadVolSurface() {
    const container = document.getElementById('plot-surface-3d');
    try {
        const url = `/api/vol-surface?symbol=${state.symbol}&mode=${state.ivMode}&space=${state.spaceMode}&range=${state.strikeRange}&include_0dte=${state.includeZeroDte}`;
        const res = await fetch(url);
        const data = await res.json();

        if (data.error) {
            container.innerHTML = `<div class="loading-spinner">${data.error}</div>`;
            return;
        }

        // Diagnostics Strip
        const d = data.diagnostics;
        if (d) {
            document.getElementById('diag-expiries').innerText = `${d.num_expiries} listed dates`;
            document.getElementById('diag-dte-range').innerText = `${d.min_dte.toFixed(1)}d to ${d.max_dte.toFixed(0)}d`;
            document.getElementById('diag-slope').innerText = `${d.term_slope >= 0 ? '+' : ''}${d.term_slope.toFixed(2)} pp`;
            document.getElementById('diag-term-regime').innerText = d.term_regime;
            document.getElementById('diag-skew').innerText = `${d.front_skew >= 0 ? '+' : ''}${d.front_skew.toFixed(2)} pp`;
            document.getElementById('diag-spot').innerText = formatNum(data.ref_spot, 1);
            document.getElementById('diag-atm-iv').innerText = `Front ATM IV: ${d.front_atm_iv.toFixed(2)}%`;
        }

        // 1. Render 3D Surface
        renderSurface3D(data);

        // 2. Render 2D Opstra Smile Overlay
        renderSmileOverlay2D(data);

        // 3. Render Term Structure
        renderTermStructure2D(data);

    } catch (e) {
        console.error('Failed to load vol surface:', e);
        container.innerHTML = `<div class="loading-spinner">Failed to render 3D surface: ${e.message}</div>`;
    }
}

function renderSurface3D(data) {
    const xTitle = data.space_mode === 'moneyness' ? 'Moneyness % (Strike / Spot)' : 'Strike Price (₹)';

    // Text matrix for custom hover
    const textMatrix = data.y_grid.map((dte, i) => {
        const expLabel = data.expiry_labels[i] || `${dte}d`;
        return data.x_grid.map(x => `${expLabel}`);
    });

    const surfaceTrace = {
        type: 'surface',
        x: data.x_grid,
        y: data.y_grid,
        z: data.z_matrix,
        text: textMatrix,
        colorscale: 'Tealgrn',
        opacity: 0.92,
        showscale: true,
        colorbar: {
            title: { text: 'IV %', font: { color: '#94a3b8', size: 11 } },
            tickfont: { color: '#cbd5e1', size: 10 },
            thickness: 14,
            len: 0.75,
            x: 1.02
        },
        contours: {
            z: { show: true, usecolormap: true, highlightcolor: '#10b981', project: { z: true } },
            x: { show: true, color: 'rgba(6, 182, 212, 0.45)', highlightcolor: '#06b6d4', project: { x: true } },
            y: { show: true, color: 'rgba(239, 68, 68, 0.45)', highlightcolor: '#ef4444', project: { y: true } }
        },
        hovertemplate: '<b>%{text}</b><br>' +
            `${xTitle}: %{x:,.0f}<br>` +
            'DTE: %{y:.1f} days<br>' +
            'Implied Vol: <b>%{z:.2f}%</b>' +
            '<extra></extra>'
    };

    // ATM Ridge / Spine Line
    const ridgeTrace = {
        type: 'scatter3d',
        mode: 'lines+markers',
        name: 'ATM Term Spine',
        x: data.atm_ridge.map(r => r.atm_strike),
        y: data.atm_ridge.map(r => r.dte),
        z: data.atm_ridge.map(r => r.iv + 0.5), // elevated slightly above mesh
        line: { color: '#f59e0b', width: 6 },
        marker: { size: 4, color: '#f59e0b' },
        hovertemplate: 'ATM Spine: %{x:,.0f}<br>DTE: %{y:.1f}d<br>ATM IV: <b>%{z:.2f}%</b><extra></extra>'
    };

    const layout = {
        template: 'plotly_dark',
        paper_bgcolor: '#090e17',
        plot_bgcolor: '#090e17',
        margin: { l: 10, r: 20, t: 25, b: 20 },
        height: 560,
        showlegend: false,
        scene: {
            xaxis: {
                title: { text: xTitle, font: { color: '#94a3b8', size: 11 } },
                tickfont: { color: '#cbd5e1', size: 10 },
                backgroundcolor: '#0d1424',
                gridcolor: '#1e293b',
                showbackground: true
            },
            yaxis: {
                title: { text: 'Days to Expiry (DTE)', font: { color: '#94a3b8', size: 11 } },
                tickfont: { color: '#cbd5e1', size: 10 },
                backgroundcolor: '#0d1424',
                gridcolor: '#1e293b',
                showbackground: true
            },
            zaxis: {
                title: { text: 'Implied Volatility (IV %)', font: { color: '#94a3b8', size: 11 } },
                tickfont: { color: '#cbd5e1', size: 10 },
                backgroundcolor: '#0d1424',
                gridcolor: '#1e293b',
                showbackground: true
            },
            camera: { eye: { x: 1.65, y: -1.65, z: 1.25 } },
            aspectratio: { x: 1.3, y: 1.1, z: 0.85 }
        }
    };

    Plotly.react('plot-surface-3d', [surfaceTrace, ridgeTrace], layout, { displaylogo: false, responsive: true });
}

function renderSmileOverlay2D(data) {
    const traces = (data.smile_curves || []).map(sc => ({
        type: 'scatter',
        mode: 'lines+markers',
        name: sc.label,
        x: sc.strikes,
        y: sc.ivs,
        line: { color: sc.color, width: 2.2 },
        marker: { size: 4, color: sc.color },
        hovertemplate: `<b>${sc.label}</b><br>Strike: %{x:,.0f}<br>IV: %{y:.2f}%<extra></extra>`
    }));

    // Add vertical line at ATM
    const shapes = [{
        type: 'line',
        x0: data.ref_spot,
        x1: data.ref_spot,
        y0: 0,
        y1: 1,
        yref: 'paper',
        line: { color: '#f59e0b', width: 1.5, dash: 'dot' }
    }];

    const layout = {
        template: 'plotly_dark',
        paper_bgcolor: '#0f1726',
        plot_bgcolor: '#0f1726',
        margin: { l: 45, r: 20, t: 20, b: 40 },
        height: 380,
        xaxis: {
            title: { text: 'Strike Price (₹)', font: { color: '#94a3b8', size: 11 } },
            gridcolor: '#1a273e',
            tickfont: { color: '#cbd5e1', size: 10 }
        },
        yaxis: {
            title: { text: 'Implied Volatility (IV %)', font: { color: '#94a3b8', size: 11 } },
            gridcolor: '#1a273e',
            tickfont: { color: '#cbd5e1', size: 10 }
        },
        legend: { orientation: 'h', y: -0.22, font: { color: '#cbd5e1', size: 10 } },
        shapes: shapes
    };

    Plotly.react('plot-smile-2d', traces, layout, { displaylogo: false, responsive: true });
}

function renderTermStructure2D(data) {
    const ts = data.term_structure || [];
    const trace = {
        type: 'scatter',
        mode: 'lines+markers+text',
        name: 'ATM Term Structure',
        x: ts.map(item => item.dte),
        y: ts.map(item => item.iv),
        text: ts.map(item => `${item.iv.toFixed(1)}%`),
        textposition: 'top center',
        line: { color: '#8b5cf6', width: 2.5 },
        marker: { size: 8, color: '#8b5cf6' },
        hovertemplate: '<b>%{text}</b><br>DTE: %{x:.1f} days<br>ATM IV: %{y:.2f}%<extra></extra>'
    };

    const layout = {
        template: 'plotly_dark',
        paper_bgcolor: '#0f1726',
        plot_bgcolor: '#0f1726',
        margin: { l: 45, r: 20, t: 20, b: 40 },
        height: 380,
        xaxis: {
            title: { text: 'Days to Expiry (DTE)', font: { color: '#94a3b8', size: 11 } },
            gridcolor: '#1a273e',
            tickfont: { color: '#cbd5e1', size: 10 }
        },
        yaxis: {
            title: { text: 'ATM Implied Volatility (IV %)', font: { color: '#94a3b8', size: 11 } },
            gridcolor: '#1a273e',
            tickfont: { color: '#cbd5e1', size: 10 }
        },
        showlegend: false
    };

    Plotly.react('plot-term-structure', [trace], layout, { displaylogo: false, responsive: true });
}

/* ==========================================================================
   TAB 2: Option Chain & Greeks (Opstra Style)
   ========================================================================== */
async function loadOptionChain() {
    try {
        // Load expiries if selector empty
        const sel = document.getElementById('chain-expiry-select');
        if (sel.options.length <= 1) {
            const expRes = await fetch(`/api/expiries?symbol=${state.symbol}`);
            const expData = await expRes.json();
            sel.innerHTML = '';
            (expData.expiries || []).forEach(e => {
                const opt = document.createElement('option');
                opt.value = e.expiry;
                opt.innerText = e.label;
                sel.appendChild(opt);
            });
            if (expData.expiries && expData.expiries.length > 0) {
                state.selectedExpiry = expData.expiries[0].expiry;
                sel.value = state.selectedExpiry;
            }
        } else {
            state.selectedExpiry = sel.value;
        }

        const url = `/api/option-chain?symbol=${state.symbol}&expiry=${state.selectedExpiry || ''}`;
        const res = await fetch(url);
        const data = await res.json();

        if (data.error) {
            document.getElementById('chain-table-body').innerHTML = `<tr><td colspan="13" class="loading-td">${data.error}</td></tr>`;
            return;
        }

        // Update KPIs
        document.getElementById('chain-spot').innerText = formatNum(data.spot, 1);
        document.getElementById('chain-atm-strike').innerText = formatNum(data.atm_strike, 0);
        document.getElementById('chain-straddle').innerText = `₹${formatNum(data.atm_straddle, 2)}`;
        document.getElementById('chain-max-pain').innerText = formatNum(data.max_pain, 0);
        document.getElementById('chain-pcr').innerText = data.pcr != null ? data.pcr.toFixed(2) : '—';

        // Render table rows
        const tbody = document.getElementById('chain-table-body');
        tbody.innerHTML = '';

        (data.rows || []).forEach(r => {
            const tr = document.createElement('tr');
            if (r.is_atm) tr.className = 'row-atm';

            const c = r.call || {};
            const p = r.put || {};

            tr.innerHTML = `
                <td class="col-call">${(c.oi || 0).toLocaleString()}</td>
                <td class="col-call">${(c.volume || 0).toLocaleString()}</td>
                <td class="col-call cyan">${c.iv ? c.iv.toFixed(1) : '—'}</td>
                <td class="col-call">${c.delta ? c.delta.toFixed(2) : '—'}</td>
                <td class="col-call muted">${c.theta ? c.theta.toFixed(1) : '—'}</td>
                <td class="col-call green font-bold">${c.ltp ? c.ltp.toFixed(2) : '—'}</td>

                <td class="strike-td ${r.is_atm ? 'cyan' : ''}">${formatNum(r.strike, 0)}</td>

                <td class="col-put red font-bold">${p.ltp ? p.ltp.toFixed(2) : '—'}</td>
                <td class="col-put muted">${p.theta ? p.theta.toFixed(1) : '—'}</td>
                <td class="col-put">${p.delta ? p.delta.toFixed(2) : '—'}</td>
                <td class="col-put cyan">${p.iv ? p.iv.toFixed(1) : '—'}</td>
                <td class="col-put">${(p.volume || 0).toLocaleString()}</td>
                <td class="col-put">${(p.oi || 0).toLocaleString()}</td>
            `;
            tbody.appendChild(tr);
        });

    } catch (e) {
        console.error('Failed to load option chain:', e);
    }
}

/* ==========================================================================
   TAB 3: Trade Decision Radar
   ========================================================================== */
async function loadTradeRadar() {
    const container = document.getElementById('radar-container');
    try {
        const res = await fetch(`/api/trade-radar?symbol=${state.symbol}`);
        const data = await res.json();

        if (data.error) {
            container.innerHTML = `<div class="loading-spinner">${data.error}</div>`;
            return;
        }

        const badgeClass = data.bias_type === 'rich' ? 'rich' : (data.bias_type === 'compressed' ? 'compressed' : 'neutral');
        const badgeColor = data.bias_type === 'rich' ? '#10b981' : (data.bias_type === 'compressed' ? '#06b6d4' : '#94a3b8');

        container.innerHTML = `
            <div class="radar-hero-card">
                <div class="radar-header">
                    <div class="radar-title-group">
                        <h2>${data.symbol} Volatility Opportunity Radar</h2>
                        <span class="muted small">${data.expiry} (${data.dte}d) · Spot Reference: ${formatNum(data.spot, 1)}</span>
                    </div>
                    <span class="radar-badge ${badgeClass}">${data.bias_title}</span>
                </div>

                <div class="radar-grid">
                    <div class="radar-stat-box">
                        <div class="radar-box-label">Implied Volatility & Spread</div>
                        <div class="radar-box-val cyan">${data.iv ? data.iv.toFixed(2) : '—'}%</div>
                        <div class="radar-box-sub" style="color:${badgeColor}">
                            Spread: ${data.spread != null ? (data.spread >= 0 ? '+' : '') + data.spread.toFixed(2) + ' pp' : '—'}
                        </div>
                    </div>

                    <div class="radar-stat-box">
                        <div class="radar-box-label">Expected Expiry Move</div>
                        <div class="radar-box-val">±${formatNum(data.expected_move_pts, 0)} pts</div>
                        <div class="radar-box-sub purple">±${data.expected_move_pct.toFixed(2)}% of underlying</div>
                    </div>

                    <div class="radar-stat-box">
                        <div class="radar-box-label">Straddle Expected Range</div>
                        <div class="radar-box-val gold">${formatNum(data.expected_range_low, 0)} – ${formatNum(data.expected_range_high, 0)}</div>
                        <div class="radar-box-sub muted">Target Expiry Bounds</div>
                    </div>
                </div>

                <div class="radar-desc-box" style="border-left-color:${badgeColor}">
                    <b>Tactical Trade Bias: ${data.bias_action}</b><br>
                    ${data.description}
                </div>

                <div class="radar-meta-row">
                    <span>Synthetic Future Check: <b class="${data.basis_status === 'Parity Normal' ? 'green' : 'gold'}">${data.basis_status} (${data.basis_pts != null ? (data.basis_pts >= 0 ? '+' : '') + data.basis_pts.toFixed(1) + ' pts' : '—'})</b></span>
                    <span>Synthetic: ${data.synthetic_future ? formatNum(data.synthetic_future, 1) : '—'} · Month Future: ${data.month_future ? formatNum(data.month_future, 1) : '—'}</span>
                </div>
            </div>
        `;
    } catch (e) {
        console.error('Failed to load trade radar:', e);
    }
}

/* ==========================================================================
   TAB 4: Futures Carry & Term
   ========================================================================== */
async function loadFuturesCarry() {
    const container = document.getElementById('futures-container');
    try {
        const res = await fetch('/api/futures-carry');
        const data = await res.json();

        if (data.error || !data.indices) {
            container.innerHTML = `<div class="loading-spinner">No futures data available</div>`;
            return;
        }

        let html = '';
        data.indices.forEach(idx => {
            html += `
                <div class="chart-container">
                    <div class="chart-header">
                        <div class="chart-title">
                            <span>${idx.symbol} Futures Carry & Basis</span>
                            <span class="badge cyan">Cash Spot vs Contract Expiries</span>
                        </div>
                    </div>
                    <div class="table-container">
                        <table class="opstra-table">
                            <thead>
                                <tr class="sub-headers">
                                    <th style="text-align:left;">Pair / Contract</th>
                                    <th>Front Expiry</th>
                                    <th>Back Expiry</th>
                                    <th>Expiry Gap</th>
                                    <th>Spread / Basis (₹)</th>
                                    <th>Annualized Carry %</th>
                                    <th>Status</th>
                                </tr>
                            </thead>
                            <tbody>
            `;
            (idx.pairs || []).forEach(p => {
                const isCarryPositive = (p['Annualized carry %'] || 0) >= 0;
                html += `
                    <tr>
                        <td style="text-align:left; font-weight:700;">${p.Pair || 'Contract Pair'}</td>
                        <td>${p['Front expiry'] || '—'}</td>
                        <td>${p['Back expiry'] || '—'}</td>
                        <td>${p['Expiry gap days'] ? p['Expiry gap days'] + ' days' : '—'}</td>
                        <td class="${(p.Spread || 0) >= 0 ? 'green' : 'red'} font-bold">${p.Spread ? p.Spread.toFixed(2) : '—'}</td>
                        <td class="${isCarryPositive ? 'green' : 'purple'} font-bold">${p['Annualized carry %'] ? p['Annualized carry %'].toFixed(2) + '%' : '—'}</td>
                        <td class="muted">${p.Status || 'Active'}</td>
                    </tr>
                `;
            });
            html += `
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
        });

        container.innerHTML = html;
    } catch (e) {
        console.error('Failed to load futures carry:', e);
    }
}

function formatNum(num, decimals = 2) {
    if (num == null || !isFinite(num)) return '—';
    return Number(num).toLocaleString('en-IN', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
    });
}
