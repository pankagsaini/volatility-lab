/* Captured broker observations used by the options desk charts. */
const OptionsCharts = (() => {
  const positive = value => typeof value === 'number' && Number.isFinite(value) && value > 0;
  function smile(row) {
    const chain = row.chain || [];
    const atm = chain.find(n => n.k === row.atm);
    const atmIv = atm && positive(atm.uCallIv) && positive(atm.uPutIv) ? (atm.uCallIv + atm.uPutIv) / 2 : null;
    function wing(call, target) {
      const deltaKey = call ? 'uCallDelta' : 'uPutDelta';
      const ivKey = call ? 'uCallIv' : 'uPutIv';
      const eligible = chain.filter(n => positive(row.spot) && (call ? n.k > row.spot : n.k < row.spot)
        && positive(n[ivKey]) && positive(call ? n[deltaKey] : -n[deltaKey]) && Math.abs(n[deltaKey]) <= 1);
      eligible.sort((a,b) => Math.abs(Math.abs(a[deltaKey]) - target) - Math.abs(Math.abs(b[deltaKey]) - target) || a.k - b.k);
      const n = eligible[0];
      if (!n || Math.abs(Math.abs(n[deltaKey]) - target) > .0250000001) return null;
      return {strike:n.k, iv:n[ivKey], delta:n[deltaKey]};
    }
    return [wing(false,.10), wing(false,.25), atmIv === null ? null : {strike:atm.k,iv:atmIv}, wing(true,.25), wing(true,.10)];
  }
  function straddleSeries(row) {
    const points = (row.intraday || []).filter(p => p.expiry === row.expiry && p.atm === (row.straddleStrike || row.atm) &&
      Number.isFinite(Date.parse(p.markAt || p.snapshotId)) && p.ivSource === 'Upstox option_greeks.iv')
      .sort((a,b) => Date.parse(a.markAt || a.snapshotId) - Date.parse(b.markAt || b.snapshotId));
    const day = stamp => Number.isFinite(Date.parse(stamp)) ? new Date(Date.parse(stamp) + 19800000).toISOString().slice(0,10) : '';
    const session = String(row.sessionDate || day(row.ivCapturedAt) || day(points.at(-1)?.snapshotId || points.at(-1)?.markAt)).slice(0,10);
    return points.filter(p => session && day(p.snapshotId || p.markAt) === session)
      .map(p => ({...p, strad:p.markSource === 'paired_book_mid' && p.cQuoteAt && p.pQuoteAt ? p.strad : null}));
  }
  function tooltipName(label) {
    const name = String(label || '');
    if (/EGARCH/i.test(name)) return 'EGARCH';
    if (/^(Captured |ATM IV)/.test(name)) return 'IV';
    if (/^Expiring front/.test(name)) return 'IV (0DTE)';
    if (/^VRP Spread/.test(name)) return 'IV − HV30';
    if (/^Spread:/.test(name)) return 'IV − HV10';
    if (/^Reference Threshold/.test(name)) return 'Threshold';
    if (/^Call \+ put/.test(name)) return 'Straddle';
    if (/^Theoretical/.test(name)) return '√t benchmark';
    if (/^OIData/.test(name)) return 'NSE median';
    if (/^Call-minus-put/.test(name)) return 'Net OI gamma';
    if (/^Underwater/.test(name)) return 'Drawdown';
    if (/^Live Forward Intraday/.test(name)) return /Net/.test(name) ? 'Net MTM' : 'Gross MTM';
    if (/^Cumulative/.test(name)) return /Net/.test(name) ? 'Cumulative net P&L' : 'Cumulative gross P&L';
    if (/^Net P&L After/.test(name)) return 'Net P&L';
    if (/^Expiry P&L/.test(name)) return 'Expiry P&L';
    return name.replace(/\s*\([^)]*\)/g, '').trim();
  }
  function heatStyle(value, maxAbs) {
    if (!Number.isFinite(value) || value === 0 || !(maxAbs > 0)) return 'background:#111b29;color:#dbe5f2';
    // One symmetric scale for all cells; retain signed numbers for accessibility.
    const intensity = Math.sqrt(Math.min(1, Math.abs(value) / maxAbs));
    const lightness = (90 - 66 * intensity).toFixed(1);
    const hue = value > 0 ? 153 : 355;
    const l = Number(lightness) / 100, a = .52 * Math.min(l, 1-l);
    const rgb = [0,8,4].map(n => {
      const k = (n + hue / 30) % 12;
      const channel = l - a * Math.max(-1, Math.min(k-3, 9-k, 1));
      return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
    });
    const luminance = rgb[0]*.2126 + rgb[1]*.7152 + rgb[2]*.0722;
    return 'background:hsl(' + hue + ' 52% ' + lightness + '%);color:' +
      (luminance < .179 ? '#ffffff' : '#000000') + ';font-weight:600';
  }
  function forecast(state, symbol, row) {
    const record = state?.forecasts?.[symbol]?.expiries?.find(item => item.expiry === row.expiry);
    // Forecast context is never copied into broker IV or historical observations.
    return record && Number.isFinite(Date.parse(record.as_of)) && record.training_through ? record : null;
  }
  function auditedTrade(row) {
    const decision = Date.parse(row.decision_at), available = Date.parse(row.available_at);
    return row.provenance_status === 'VALIDATED_EVENT_TIME_TWO_SIDED_BOOK'
      && Number.isFinite(decision) && Number.isFinite(available) && available <= decision
      && row.entry_source && row.exit_source && positive(row.unitLegs)
      && [row.gross,row.charges,row.capital].every(Number.isFinite) && row.capital > 0;
  }
  function comparison(state, symbol, row) {
    const record = forecast(state, symbol, row);
    const captured = Date.parse(row.ivCapturedAt || row.archivedAsOf);
    const forecastAt = Date.parse(record?.as_of);
    const day = stamp => Number.isFinite(stamp) ? new Date(stamp + 19800000).toISOString().slice(0,10) : '';
    // Older forecasts remain visible as dated references, never as current edges.
    const aligned = !!record && Number.isFinite(captured) && forecastAt <= captured
      && day(forecastAt) === day(captured) && record.training_through <= day(captured);
    const iv = positive(row.brokerAtmIv) ? row.brokerAtmIv : null;
    const egarch = positive(record?.egarch) ? record.egarch : null;
    return {iv, egarch, aligned, training:record?.training_through || null,
      egarchGap:aligned && iv !== null && egarch !== null ? iv - egarch : null};
  }
  return {smile, straddleSeries, tooltipName, heatStyle, forecast, comparison, auditedTrade};
})();
