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
  return {smile, straddleSeries};
})();
