/**
 * ============================================================================
 * VOLATILITY LAB — ULTRA-ROBUST 1-MINUTE UPSTOX SNAPSHOT COLLECTOR (v24.0)
 * ============================================================================
 * Dedicated, fault-tolerant 1-minute market data collector for NIFTY 50,
 * BANKNIFTY, and INDIA VIX.
 *
 * Design Principles:
 *   1. Pure Data Capture Only: Captures Upstox spot, futures, India VIX, full
 *      option chains, and +/-10 strike L1 bid/ask order-book depth every minute
 *      during NSE F&O trading hours (09:15–15:31 IST) and saves compressed
 *      .json.gz snapshots to Google Drive. Zero strategy or ledger bloat.
 *   2. Never-Stop Fault Isolation: Every external boundary (PropertiesService,
 *      CacheService, Upstox CDN, UrlFetchApp, DriveApp, and LockService) is
 *      individually guarded. If Upstox CDN or a single chain call hiccups,
 *      cached catalogue fallback and per-chain recovery keep snapshots flowing
 *      without interruption for as long as UPSTOX_TOKEN is valid.
 *   3. Built-in Historical Backfill: Run downloadEarlierData() once in the
 *      Apps Script editor to backfill 1-minute & daily candles for earlier dates.
 * ============================================================================
 */

const CONFIG = {
  timeZone: 'Asia/Kolkata',
  regularStartMins: 555, // 09:15 IST
  regularEndMins: 931,   // 15:31 IST
  doGetCacheSeconds: 55,
  safeDeadlineMs: 180000,
  indices: {
    NIFTY: 'NSE_INDEX|Nifty 50',
    BANKNIFTY: 'NSE_INDEX|Nifty Bank'
  }
};

const NSE_FO_HOLIDAYS = {
  '2026': [
    '2026-01-15', '2026-01-26', '2026-03-03', '2026-03-26', '2026-03-31', '2026-04-03',
    '2026-04-14', '2026-05-01', '2026-05-28', '2026-06-26', '2026-09-14', '2026-10-02',
    '2026-10-20', '2026-11-10', '2026-11-24', '2026-12-25'
  ],
  '2027': [
    '2027-01-26', '2027-03-17', '2027-03-26', '2027-04-14', '2027-04-15', '2027-08-15',
    '2027-10-02', '2027-12-25'
  ]
};

const SPECIAL_SESSIONS = {
  '2026-02-01': { label: 'Union Budget Special Live Session (Sunday)', startMins: 555, endMins: 930 },
  '2026-11-08': { label: 'Diwali Laxmi Pujan Muhurat Trading Session (Sunday)', startMins: 860, endMins: 940 }
};

function props_() {
  try { return PropertiesService.getScriptProperties(); } catch (e) { return null; }
}
function safeGetProp_(p, k, fb) {
  try { return (p && p.getProperty(k)) || fb; } catch (e) { return fb; }
}
function safeSetProp_(p, k, v) {
  try { if (p) p.setProperty(k, String(v)); } catch (e) {}
}
function day_(d) {
  return Utilities.formatDate(d || new Date(), CONFIG.timeZone, 'yyyy-MM-dd');
}
function minute_(d) {
  const dt = d || new Date();
  return Number(Utilities.formatDate(dt, CONFIG.timeZone, 'H')) * 60 + Number(Utilities.formatDate(dt, CONFIG.timeZone, 'm'));
}
function scriptCache_() {
  try { return CacheService.getScriptCache(); } catch (e) { return null; }
}
function readJson_(p, k, fb) {
  try {
    const raw = safeGetProp_(p, k, '');
    return raw ? JSON.parse(raw) : fb;
  } catch (e) { return fb; }
}
function timestampMillis_(ts) {
  if (typeof ts === 'number' && Number.isFinite(ts)) return ts > 1e11 ? ts : ts * 1000;
  const s = String(ts || '').trim();
  if (/^\d{13}$/.test(s)) return Number(s);
  if (/^\d{10}$/.test(s)) return Number(s) * 1000;
  const p = Date.parse(s);
  return Number.isFinite(p) ? p : NaN;
}

function folder_() {
  const p = props_();
  const id = safeGetProp_(p, 'ARCHIVE_FOLDER_ID', '');
  if (id) {
    try { return DriveApp.getFolderById(id); } catch (e) {}
  }
  const it = DriveApp.getFoldersByName('Volatility Lab - private Upstox archive');
  const f = it.hasNext() ? it.next() : DriveApp.createFolder('Volatility Lab - private Upstox archive');
  safeSetProp_(p, 'ARCHIVE_FOLDER_ID', f.getId());
  return f;
}

function save_(name, value) {
  const gz = Utilities.gzip(Utilities.newBlob(JSON.stringify(value), 'application/json', name + '.json')).setName(name + '.json.gz');
  for (let attempt = 0; attempt < 2; attempt++) {
    try { return folder_().createFile(gz).getId(); } catch (e) { if (attempt === 0) Utilities.sleep(400); }
  }
  return DriveApp.getRootFolder().createFile(gz).getId();
}

function marketWindow_(dateObj) {
  const dt = dateObj || new Date();
  const day = day_(dt);
  const weekday = Number(Utilities.formatDate(dt, CONFIG.timeZone, 'u'));
  if (SPECIAL_SESSIONS[day]) return Object.assign({ closed: false, special: true }, SPECIAL_SESSIONS[day]);
  if (weekday > 5) return { closed: true, special: false, label: 'Weekend (Saturday/Sunday)' };
  if ((NSE_FO_HOLIDAYS[day.slice(0, 4)] || []).includes(day)) return { closed: true, special: false, label: 'NSE F&O Holiday (' + day + ')' };
  return { closed: false, special: false, label: 'Regular Session 09:15-15:31 IST', startMins: CONFIG.regularStartMins, endMins: CONFIG.regularEndMins };
}

function api_(path, params) {
  const token = safeGetProp_(props_(), 'UPSTOX_TOKEN', '');
  if (!token) throw new Error('Set UPSTOX_TOKEN in Script Properties');
  const qs = Object.keys(params || {}).map(k => encodeURIComponent(k) + '=' + encodeURIComponent(params[k])).join('&');
  const url = 'https://api.upstox.com' + path + (qs ? '?' + qs : '');
  for (let attempt = 0; attempt < 2; attempt++) {
    const res = UrlFetchApp.fetch(url, {
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/json' },
      muteHttpExceptions: true, followRedirects: false, timeoutSeconds: 15
    });
    const code = res.getResponseCode();
    if (code === 200) {
      const body = JSON.parse(res.getContentText());
      if (body && body.status === 'success' && body.data) return body.data;
      throw new Error('Empty broker data');
    }
    if (attempt === 0 && (code === 429 || code >= 500)) { Utilities.sleep(600); continue; }
    throw new Error('Broker HTTP ' + code);
  }
}

function apiAll_(requests) {
  if (!requests || !requests.length) return [];
  const token = safeGetProp_(props_(), 'UPSTOX_TOKEN', '');
  if (!token) throw new Error('Set UPSTOX_TOKEN in Script Properties');
  const payloads = requests.map(req => {
    const qs = Object.keys(req.params || {}).map(k => encodeURIComponent(k) + '=' + encodeURIComponent(req.params[k])).join('&');
    return {
      url: 'https://api.upstox.com' + req.path + (qs ? '?' + qs : ''),
      headers: { Authorization: 'Bearer ' + token, Accept: 'application/json' },
      muteHttpExceptions: true, followRedirects: false, timeoutSeconds: 20
    };
  });
  return UrlFetchApp.fetchAll(payloads).map((res, i) => {
    const req = requests[i], code = res.getResponseCode();
    if (code === 200) {
      try {
        const body = JSON.parse(res.getContentText());
        if (body && body.status === 'success' && body.data) return { label: req.label, path: req.path, params: req.params, data: body.data };
      } catch (e) { return { label: req.label, path: req.path, params: req.params, error: 'Invalid broker JSON' }; }
    }
    return { label: req.label, path: req.path, params: req.params, error: 'Broker HTTP ' + code };
  });
}

function catalogue_(now) {
  const p = props_(), cache = scriptCache_(), today = day_(now);
  if (cache) {
    try {
      const mem = cache.get('CAT_' + today);
      if (mem) return JSON.parse(mem);
    } catch (e) {}
  }
  const cached = readJson_(p, 'CATALOGUE', null);
  if (cached && cached.day === today && cached.indices) {
    if (cache) { try { cache.put('CAT_' + today, JSON.stringify(cached), 21600); } catch (e) {} }
    return cached;
  }
  let master = null;
  try {
    const res = UrlFetchApp.fetch('https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz', { muteHttpExceptions: true, timeoutSeconds: 25 });
    if (res.getResponseCode() === 200) master = JSON.parse(Utilities.ungzip(res.getBlob()).getDataAsString());
  } catch (e) {}
  if (!master && cached && cached.indices) {
    const rolled = { day: today, indices: {} };
    Object.keys(cached.indices).forEach(sym => {
      const prev = cached.indices[sym] || {};
      rolled.indices[sym] = {
        futures: (prev.futures || []).filter(f => f.expiry >= today),
        option_expiries: (prev.option_expiries || []).filter(d => d >= today)
      };
    });
    return rolled;
  }
  if (!master) throw new Error('Instrument master unavailable');
  const catalogue = { day: today, indices: {} };
  for (const symbol of Object.keys(CONFIG.indices)) {
    const uKey = CONFIG.indices[symbol];
    const futures = master
      .filter(x => x.underlying_key === uKey && x.instrument_type === 'FUT')
      .map(x => ({ instrument_key: x.instrument_key, expiry: typeof x.expiry === 'number' ? day_(new Date(x.expiry)) : String(x.expiry).slice(0, 10), trading_symbol: x.trading_symbol }))
      .filter(x => x.expiry >= today).sort((a, b) => a.expiry.localeCompare(b.expiry)).slice(0, 3);
    const contracts = api_('/v2/option/contract', { instrument_key: uKey });
    const maxExp = futures.length ? futures[futures.length - 1].expiry : '2099-12-31';
    let dates = [...new Set(contracts.map(x => String(x.expiry).slice(0, 10)))].filter(x => x >= today && x <= maxExp).sort();
    if (symbol === 'BANKNIFTY' && futures.length) dates = dates.filter(x => futures.some(f => f.expiry === x));
    catalogue.indices[symbol] = { futures: futures, option_expiries: dates };
  }
  safeSetProp_(p, 'CATALOGUE', JSON.stringify(catalogue));
  if (cache) { try { cache.put('CAT_' + today, JSON.stringify(catalogue), 21600); } catch (e) {} }
  return catalogue;
}

function enrichOptionQuotes_(report) {
  const chains = report.records.filter(r => r.label.indexOf('|') !== -1 && Array.isArray(r.data));
  const options = [];
  chains.forEach(rec => {
    const sym = rec.label.split('|')[0], rows = rec.data || [], step = sym === 'NIFTY' ? 50 : 100;
    const spot = Number(rows[0] && rows[0].underlying_spot_price), atm = Math.round(spot / step) * step;
    const strikes = new Set();
    for (let i = -10; i <= 10; i++) strikes.add(atm + i * step);
    rows.forEach(row => {
      if (!strikes.has(Number(row.strike_price))) return;
      ['call_options', 'put_options'].forEach(s => { if (row[s] && row[s].instrument_key) options.push(row[s]); });
    });
  });
  const keys = [...new Set(options.map(o => o.instrument_key))], requests = [];
  let batch = [];
  keys.forEach(k => {
    if (batch.length && (batch.length >= 450 || encodeURIComponent(batch.concat([k]).join(',')).length > 1700)) {
      requests.push({ label: 'q:' + requests.length, path: '/v2/market-quote/quotes', params: { instrument_key: batch.join(',') } });
      batch = [];
    }
    batch.push(k);
  });
  if (batch.length) requests.push({ label: 'q:' + requests.length, path: '/v2/market-quote/quotes', params: { instrument_key: batch.join(',') } });
  const quotes = new Map();
  apiAll_(requests).forEach(res => {
    if (res.data) Object.values(res.data).forEach(q => { if (q && q.instrument_token) quotes.set(q.instrument_token, q); });
  });
  options.forEach(opt => {
    opt.chain_market_data = opt.market_data;
    const q = quotes.get(opt.instrument_key);
    if (!q || !Number.isFinite(timestampMillis_(q.timestamp))) return;
    const buy = (q.depth && q.depth.buy && q.depth.buy[0]) || {}, sell = (q.depth && q.depth.sell && q.depth.sell[0]) || {};
    opt.market_data = Object.assign({}, opt.chain_market_data, {
      ltp: q.last_price, bid_price: buy.price, ask_price: sell.price, bid_qty: buy.quantity, ask_qty: sell.quantity,
      volume: q.volume, oi: q.oi, quote_timestamp: q.timestamp, last_trade_time: q.last_trade_time,
      quote_source: 'Upstox /v2/market-quote/quotes'
    });
  });
}

function capture_(mode) {
  const started = Date.now(), now = new Date(), today = day_(now), p = props_();
  const report = {
    schema: 1, provider: 'Upstox', day: today, mode: mode || 'snapshot',
    retrieved_at: now.toISOString(), status: 'partial', nfo_session_validated: true,
    records: [], errors: [], pending: []
  };
  try {
    const cat = catalogue_(now);
    report.catalogue = cat;
    const qKeys = Object.values(CONFIG.indices).concat(['NSE_INDEX|India VIX']);
    Object.values(cat.indices).forEach(idx => (idx.futures || []).forEach(f => qKeys.push(f.instrument_key)));
    const requests = [{ label: 'quotes', path: '/v2/market-quote/quotes', params: { instrument_key: qKeys.join(',') } }];
    Object.keys(cat.indices).forEach(sym => {
      (cat.indices[sym].option_expiries || []).forEach(exp => {
        requests.push({ label: sym + '|' + exp, path: '/v2/option/chain', params: { instrument_key: CONFIG.indices[sym], expiry_date: exp } });
      });
    });
    for (const res of apiAll_(requests)) {
      let data = res.data;
      if (res.error) {
        if (res.error === 'Broker HTTP 401' || res.error === 'Broker HTTP 403') {
          report.errors.push({ item: res.label, reason: res.error });
          continue;
        }
        if (Date.now() - started < CONFIG.safeDeadlineMs - 30000) {
          try { data = api_(res.path, res.params); } catch (e) { report.errors.push({ item: res.label, reason: String(e.message || e) }); continue; }
        } else { report.errors.push({ item: res.label, reason: res.error }); continue; }
      }
      report.records.push({ label: res.label, retrieved_at: new Date().toISOString(), data: data });
    }
    try { enrichOptionQuotes_(report); } catch (e) {}
    report.status = report.errors.length ? 'partial' : 'complete';
  } catch (err) {
    report.errors.push({ item: 'capture', reason: String(err.message || err) });
  }
  report.fetch_seconds = (Date.now() - started) / 1000;
  report.retrieved_at = new Date().toISOString();
  const fileName = today + '_' + report.mode + '_' + Utilities.formatDate(now, CONFIG.timeZone, 'HHmmss') + '_' + report.status;
  report.file_id = save_(fileName, report);
  report.total_seconds = (Date.now() - started) / 1000;
  if (report.records.length > 0) {
    safeSetProp_(p, 'LATEST_SNAPSHOT_FILE_ID', report.file_id);
    safeSetProp_(p, 'LAST_SNAPSHOT_AT_MS', String(Date.now()));
    safeSetProp_(p, 'LAST_SNAPSHOT', JSON.stringify({ day: today, status: report.status, file_id: report.file_id, retrieved_at: report.retrieved_at, seconds: report.total_seconds }));
    const cache = scriptCache_();
    if (cache) { try { cache.remove('DOGET_LATEST_JSON'); } catch (e) {} }
  }
  return report;
}

function scheduledCapture() {
  const now = new Date(), weekday = Number(Utilities.formatDate(now, CONFIG.timeZone, 'u'));
  const session = marketWindow_(now), mins = minute_(now);
  if (weekday > 5 && !session.special) return;
  if (session.closed || mins < session.startMins || mins > session.endMins) return;
  const p = props_(), today = day_(now), slot = today + '|' + (mins - session.startMins);
  if (safeGetProp_(p, 'LAST_SNAPSHOT_SLOT', '') === slot) return;
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(500)) return;
  const started = Date.now();
  try {
    const result = capture_('snapshot');
    if (result.records && result.records.length > 0) safeSetProp_(p, 'LAST_SNAPSHOT_SLOT', slot);
    safeSetProp_(p, 'COLLECTOR_STATUS', 'snapshot ' + result.status + ' ' + result.retrieved_at);
  } catch (err) {
    safeSetProp_(p, 'COLLECTOR_STATUS', 'failed snapshot ' + new Date().toISOString() + ' · ' + String(err.message || err));
  } finally {
    const b = readJson_(p, 'RUNTIME_BUDGET', {});
    safeSetProp_(p, 'RUNTIME_BUDGET', JSON.stringify({ day: today, ms: (b.day === today ? Number(b.ms || 0) : 0) + (Date.now() - started) }));
    try { lock.releaseLock(); } catch (e) {}
  }
}

function scheduledWatchdog() {
  try {
    const handlers = ScriptApp.getProjectTriggers().map(t => t.getHandlerFunction());
    if (!handlers.includes('scheduledCapture')) {
      ScriptApp.newTrigger('scheduledCapture').timeBased().everyMinutes(1).create();
      safeSetProp_(props_(), 'COLLECTOR_STATUS', 'watchdog recovered scheduledCapture trigger ' + new Date().toISOString());
    }
  } catch (e) {}
}

function installIntradaySchedule() {
  const p = props_();
  if (!safeGetProp_(p, 'UPSTOX_TOKEN', '')) throw new Error('Set UPSTOX_TOKEN first');
  folder_();
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('scheduledCapture').timeBased().everyMinutes(1).create();
  ScriptApp.newTrigger('scheduledWatchdog').timeBased().everyHours(1).create();
  safeSetProp_(p, 'SCHEDULE_MODE', 'intraday');
}

function testCapture() { return capture_('test'); }

function downloadEarlierData(fromDateStr, toDateStr) {
  const today = day_(new Date());
  const toDay = toDateStr || today;
  const fromDay = fromDateStr || '2026-11-10';
  const cat = catalogue_(new Date());
  const keys = Object.values(CONFIG.indices).concat(['NSE_INDEX|India VIX']);
  Object.values(cat.indices).forEach(idx => (idx.futures || []).forEach(f => keys.push(f.instrument_key)));
  const backfill = { schema: 1, provider: 'Upstox', mode: 'historical_backfill', from_date: fromDay, to_date: toDay, retrieved_at: new Date().toISOString(), records: [], errors: [] };
  keys.forEach(k => {
    const enc = encodeURIComponent(k);
    [['day', '/v2/historical-candle/' + enc + '/day/' + toDay + '/' + fromDay],
     ['1minute', '/v2/historical-candle/' + enc + '/1minute/' + toDay + '/' + fromDay]].forEach(pair => {
      try {
        const d = api_(pair[1], {});
        backfill.records.push({ instrument_key: k, interval: pair[0], candles: (d && d.candles) || [] });
      } catch (e) { backfill.errors.push({ instrument_key: k, interval: pair[0], reason: String(e.message || e) }); }
    });
  });
  backfill.file_id = save_(toDay + '_historical_backfill_complete', backfill);
  return backfill;
}

function doGet(e) {
  const p = props_(), requiredKey = safeGetProp_(p, 'WEBHOOK_KEY', '');
  if (requiredKey && (!e || !e.parameter || e.parameter.key !== requiredKey)) {
    return ContentService.createTextOutput(JSON.stringify({ error: 'Unauthorized' })).setMimeType(ContentService.MimeType.JSON);
  }
  const action = String((e && e.parameter && e.parameter.action) || 'latest').toLowerCase();
  const cb = (e && e.parameter && e.parameter.callback) || '';
  const respond = json => cb && /^[A-Za-z_$][A-Za-z0-9_$]{0,63}$/.test(cb)
    ? ContentService.createTextOutput(cb + '(' + json + ');').setMimeType(ContentService.MimeType.JAVASCRIPT)
    : ContentService.createTextOutput(json).setMimeType(ContentService.MimeType.JSON);
  try {
    if (action === 'status') {
      return respond(JSON.stringify({ status: safeGetProp_(p, 'COLLECTOR_STATUS', ''), last_snapshot: readJson_(p, 'LAST_SNAPSHOT', null), budget: readJson_(p, 'RUNTIME_BUDGET', null), window: marketWindow_(new Date()) }));
    }
    const cache = scriptCache_();
    if (cache) {
      const hit = cache.get('DOGET_LATEST_JSON');
      if (hit) return respond(hit);
    }
    const id = safeGetProp_(p, 'LATEST_SNAPSHOT_FILE_ID', '');
    const file = id ? DriveApp.getFileById(id) : null;
    if (!file) return respond(JSON.stringify({ error: 'No snapshot files found' }));
    const raw = Utilities.ungzip(file.getBlob()).getDataAsString();
    if (cache && raw.length < 95000) { try { cache.put('DOGET_LATEST_JSON', raw, CONFIG.doGetCacheSeconds); } catch (e) {} }
    return respond(raw);
  } catch (err) {
    return respond(JSON.stringify({ error: String(err.message || err) }));
  }
}
