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
    const session = String(row.sessionDate || new Date(Date.parse(row.ivCapturedAt || points.at(-1)?.snapshotId) + 19800000).toJSON() || '').slice(0,10);
    return points.filter(p => new Date(Date.parse(p.snapshotId || p.markAt) + 19800000).toISOString().startsWith(session))
      .map(p => ({...p, strad:p.markSource === 'paired_book_mid' && p.cQuoteAt && p.pQuoteAt ? p.strad : null}));
  }
  function expiryForecast(models, row, sessions) {
    const dayMs = 86400000;
    const istOffset = 19800000;
    const at = Date.parse(row.snapshotId || row.ivCapturedAt);
    if (!Number.isFinite(at) || !/^\d{4}-\d{2}-\d{2}$/.test(row.expiry || '')) return null;
    const local = new Date(at + istOffset);
    const day = local.toISOString().slice(0,10);
    const expiry = Date.parse(row.expiry);
    const today = Date.parse(day);
    if (!(expiry > today) || expiry - today > 380 * dayMs || !sessions.session(row.expiry)) return null;
    const session = sessions.session(day);
    const minutes = (at + istOffset - today) / 60000;
    let close = session && minutes >= session.endMins + 5 ? today : today - dayMs;
    while (!sessions.session(new Date(close).toISOString().slice(0,10))) close -= dayMs;
    const trainedThrough = new Date(close).toISOString().slice(0,10);
    const forecast = models?.[trainedThrough];
    if (!forecast) return null;

    let variance = 0, weightSum = 0, step = 0;
    let usableGarch = Array.isArray(forecast.garch_variances);
    for (let ts = close + dayMs; ts <= expiry; ts += dayMs) {
      const window = sessions.session(new Date(ts).toISOString().slice(0,10));
      if (!window) continue;
      const weight = ts < today ? 0 : ts === today
        ? Math.max(0, Math.min(1, (window.endMins - minutes) / (window.endMins - window.startMins))) : 1;
      const next = forecast.garch_variances?.[step++];
      if (positive(next)) variance += next * weight;
      else usableGarch = false;
      weightSum += weight;
    }
    if (step > 245 || !(weightSum > 0)) return null;
    return {trainedThrough, garch: usableGarch ? Math.sqrt(variance / weightSum) : null,
      ewma: positive(forecast.ewma) ? forecast.ewma : null};
  }
  return {smile, straddleSeries, expiryForecast};
})();
