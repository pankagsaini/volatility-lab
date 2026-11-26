function pairedBookMid_(rec, strike, referenceStamp) {
  const row = rec.data.find(r => Number(r.strike_price) === strike);
  const books = [row?.call_options, row?.put_options].map(leg => {
    const md = leg?.market_data;
    if (md?.quote_source !== 'Upstox /v2/market-quote/quotes') return null;
    const time = timestampMillis_(md.quote_timestamp);
    const bid = Number(md.bid_price), ask = Number(md.ask_price);
    if (!captureQuoteFresh_(time, referenceStamp) || ![bid,ask].every(Number.isFinite)
        || !(bid > 0 && ask >= bid && md.bid_qty > 0 && md.ask_qty > 0)) return null;
    return {time, mid:(bid + ask) / 2};
  });
  const matched = books[0] && books[1] && Math.abs(books[0].time - books[1].time) <= 30000;
  return matched ? Number((books[0].mid + books[1].mid).toFixed(2)) : null;
}

function optionBookCoverage_(record, lotSize) {
  const coverage = {sides: record.data.length * 2, fresh: 0, oneLot: 0};
  for (const row of record.data) {
    for (const side of ['call_options', 'put_options']) {
      const book = row?.[side]?.market_data;
      if (book?.quote_source !== 'Upstox /v2/market-quote/quotes' ||
          !captureQuoteFresh_(book.quote_timestamp, record.retrieved_at)) continue;
      coverage.fresh++;
      if ([book.bid_price, book.ask_price, book.bid_qty, book.ask_qty].every(Number.isFinite)
          && book.bid_price > 0 && book.ask_price >= book.bid_price
          && Math.min(book.bid_qty, book.ask_qty) >= lotSize) coverage.oneLot++;
    }
  }
  return coverage;
}

function dualStraddlePoint_(rec, stamp) {
  const markAt = timestampMillis_(stamp);
  if (!Number.isFinite(markAt) || !Array.isArray(rec.data) || !rec.data.length) return null;
  const sym = String(rec.label || '').split('|')[0];
  if (sym !== 'NIFTY' && sym !== 'BANKNIFTY') return null;
  const spot = Number(rec.data[0].underlying_spot_price);
  const listed = rec.data.map(row => Number(row.strike_price)).filter(k => Number.isFinite(k) && k > 0);
  if (!(spot > 0) || !listed.length) return null;
  const rollAtm = listed.reduce((a,b) => Math.abs(a-spot) === Math.abs(b-spot) ? Math.min(a,b)
    : Math.abs(a-spot) < Math.abs(b-spot) ? a : b);
  const anchorK = Number(rec.straddle_anchor_strike) > 0 ? Number(rec.straddle_anchor_strike) : null;
  const rollStrad = pairedBookMid_(rec, rollAtm, stamp);
  const anchorStrad = anchorK ? pairedBookMid_(rec, anchorK, stamp) : null;
  if (!(rollStrad > 0) && !(anchorStrad > 0)) return null;
  return {markAt, rollAtm, rollStrad, anchorK, anchorStrad};
}

function timestampMillis_(timestamp) {
  if (typeof timestamp === 'number' && Number.isFinite(timestamp)) {
    return timestamp > 1e11 ? timestamp : timestamp * 1000;
  }
  const value = String(timestamp || '').trim();
  if (/^\d{13}$/.test(value)) return Number(value);
  if (/^\d{10}$/.test(value)) return Number(value) * 1000;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : NaN;
}

function captureQuoteFresh_(timestamp, referenceTimestamp) {
  const observed = timestampMillis_(timestamp);
  const reference = referenceTimestamp === undefined ? Date.now() : timestampMillis_(referenceTimestamp);
  if (!Number.isFinite(observed) || !Number.isFinite(reference)) return false;
  const age = reference - observed;
  return age >= 0 && age <= 120000;
}
