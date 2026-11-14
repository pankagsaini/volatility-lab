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
  function surface(rows, near) {
    // Plot captured OTM observations only; no surface fitting or gap interpolation.
    return rows.flatMap(row => (row.chain || []).filter(n => positive(n.k) && positive(row.spot)
      && (!near || Math.abs(n.k / row.spot - 1) <= .10)).flatMap(n => {
        const iv = n.k === row.atm && positive(n.uCallIv) && positive(n.uPutIv)
          ? (n.uCallIv + n.uPutIv) / 2 : n.k < row.spot ? n.uPutIv : n.uCallIv;
        return positive(iv) && positive(row.dte) ? [{strike:n.k, dte:row.dte, iv, expiry:row.expiry, asOf:row.ivCapturedAt || row.archivedAsOf || ''}] : [];
      }));
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
  return {smile, straddleSeries, surface, forecast, auditedTrade};
})();
