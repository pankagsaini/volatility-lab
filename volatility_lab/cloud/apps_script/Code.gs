/** Record and serve one-minute Upstox market snapshots. */
// A zero value means raw minute archives are retained indefinitely.  Do not
// convert this to a truthy fallback: historical snapshots are research evidence.
const SNAPSHOT_RETENTION_DAYS = 0;
const RUNTIME_BUDGET_WINDOW_MS = 24 * 60 * 60 * 1000;
const CAPTURE_SAFE_DEADLINE_MS = 45000;

const CONFIG = {
  timeZone: 'Asia/Kolkata',
  regularStartMins: 555, // 09:15 IST (9 * 60 + 15)
  eodEndMins: 22 * 60,           // 22:00 IST (1320)
  snapshotRetentionDays: SNAPSHOT_RETENTION_DAYS,
  doGetCacheSeconds: 55,
  indices: {
    NIFTY: 'NSE_INDEX|Nifty 50',
    BANKNIFTY: 'NSE_INDEX|Nifty Bank'
  }
};

const VL = {
  zone: CONFIG.timeZone,
  indices: CONFIG.indices
};

// NSE/FAOP/71777 and the additional closure in NSE/FAOP/72262.
const NSE_FO_HOLIDAYS = {
  '2026': [
    '2026-01-15', '2026-01-26', '2026-03-03', '2026-03-26', '2026-03-31', '2026-04-03',
    '2026-04-14', '2026-05-01', '2026-05-28', '2026-06-26', '2026-09-14', '2026-10-02',
    '2026-10-20', '2026-11-10', '2026-11-24', '2026-12-25'
  ]
};

// Exact normal-market hours from the separate NSE special-session circulars.
const SPECIAL_SESSIONS = {
  '2026-02-01': { // NSE/FAOP/72352
    label: 'Union Budget live session',
    startMins: 555, endMins: 930, eodStartMins: 935, eodEndMins: 1320
  },
  '2026-11-08': { // NSE/FAOP/76940
    label: 'Diwali Muhurat Trading (Samvat 2083 - Sunday)',
    startMins: 14 * 60 + 20,
    endMins: 15 * 60 + 40,
    eodStartMins: 15 * 60 + 50,
    eodEndMins: 22 * 60
  }
};

function props_() {
  return PropertiesService.getScriptProperties();
}

function readJsonProperty_(properties, key, fallback) {
  const raw = properties.getProperty(key);
  if (!raw) return fallback;
  try {
    return JSON.parse(raw);
  } catch (err) {
    properties.setProperty('COLLECTOR_STATUS', 'Invalid saved state for ' + key + '; safe defaults applied');
    return fallback;
  }
}

function runtimePolicy_(properties) {
  const workspace = properties.getProperty('COLLECTOR_QUOTA_TIER') === 'verified_workspace';
  const maximum = workspace ? 355 : 85;
  const requested = Number(properties.getProperty('COLLECTOR_RUNTIME_MINUTES'));
  const total = Number.isInteger(requested) && requested > 0 ? Math.min(requested, maximum) : maximum;

  // Allow for the last admitted capture as well as the protected attempt.
  // Keep at least half of even a small operator-set budget for ordinary minutes.
  const reserveMs = Math.min(2 * CAPTURE_SAFE_DEADLINE_MS, total * 60000 / 4);
  return { tier: workspace ? 'operator_verified_workspace' : 'consumer_default',
    total_minutes: total, snapshot_minutes: total, eod_reserve_minutes: 0,
    reserve_per_protected_snapshot_seconds: reserveMs / 1000 };
}

function snapshotAdmissionLimit_(policy, minute, open, close) {
  // Match scheduled_exit_at in backtest/forward_test.py for special sessions too.
  const exitMinute = Math.max(open, Math.min(15 * 60 + 15, close - 15));
  const pending = Number(minute < exitMinute) + Number(minute < close);
  return policy.snapshot_minutes * 60000 - pending * policy.reserve_per_protected_snapshot_seconds * 1000;
}

function readRuntimeBudget_(properties, key, nowMs) {
  const stored = readJsonProperty_(properties, key, {});
  const startMs = Number(stored.windowStartMs);
  const endMs = Number(stored.windowEndMs);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) {
    // Preserve the prior calendar-day counter during migration to the rolling window.
    return {
      windowStartMs: nowMs,
      windowEndMs: nowMs + RUNTIME_BUDGET_WINDOW_MS,
      ms: Math.max(0, Number(stored.ms) || 0)
    };
  }
  if (startMs > nowMs || endMs <= nowMs) {
    return { windowStartMs: nowMs, windowEndMs: nowMs + RUNTIME_BUDGET_WINDOW_MS, ms: 0 };
  }
  return {
    windowStartMs: startMs,
    windowEndMs: endMs,
    ms: Math.max(0, Number(stored.ms) || 0)
  };
}

function addRuntimeBudget_(properties, key, elapsedMs) {
  const ended = Date.now();
  const budget = readRuntimeBudget_(properties, key, ended);
  budget.ms += Math.max(0, Number(elapsedMs) || 0);
  properties.setProperty(key, JSON.stringify(budget));
  return budget;
}

function scriptCache_() {
  try {
    if (typeof CacheService !== 'undefined' && CacheService && typeof CacheService.getScriptCache === 'function') {
      return CacheService.getScriptCache();
    }
  } catch (e) {}
  return null;
}

function day_(date) {
  return Utilities.formatDate(date, CONFIG.timeZone, 'yyyy-MM-dd');
}

function minute_(date) {
  const hours = Number(Utilities.formatDate(date, CONFIG.timeZone, 'H'));
  const minutes = Number(Utilities.formatDate(date, CONFIG.timeZone, 'm'));
  return hours * 60 + minutes;
}

function folder_() {
  const properties = props_();
  let folderId = properties.getProperty('ARCHIVE_FOLDER_ID');
  if (!folderId) {
    const existing = DriveApp.getFoldersByName('Volatility Lab - private Upstox archive');
    if (existing.hasNext()) {
      folderId = existing.next().getId();
    } else {
      const folder = DriveApp.createFolder('Volatility Lab - private Upstox archive');
      folderId = folder.getId();
    }
    properties.setProperty('ARCHIVE_FOLDER_ID', folderId);
  }
  return DriveApp.getFolderById(folderId);
}

function listDriveFilesPage_(folderId, pageToken, orderBy) {
  const query = "'" + folderId + "' in parents and trashed = false";
  const params = {
    q: query,
    orderBy: orderBy || 'name',
    pageSize: '1000',
    fields: 'nextPageToken,files(id,name)'
  };
  if (pageToken) params.pageToken = pageToken;
  const queryString = Object.keys(params)
    .map(key => encodeURIComponent(key) + '=' + encodeURIComponent(params[key]))
    .join('&');
  const response = UrlFetchApp.fetch('https://www.googleapis.com/drive/v3/files?' + queryString, {
    headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() },
    muteHttpExceptions: true,
    timeoutSeconds: 30
  });
  if (response.getResponseCode() !== 200) {
    throw new Error('Drive file listing failed with HTTP ' + response.getResponseCode());
  }
  return JSON.parse(response.getContentText());
}

function api_(path, params) {
  const allowedEndpoint = (
    /^\/v2\/(option\/(chain|contract)|market-quote\/quotes|market\/holidays|historical-candle)/.test(path) ||
    path.startsWith('/v3/historical-candle/') ||
    path === '/v2/expired-instruments/future/contract' ||
    path.startsWith('/v2/expired-instruments/historical-candle/')
  );
  if (!allowedEndpoint) {
    throw new Error('Endpoint not allowed');
  }
  const token = props_().getProperty('UPSTOX_TOKEN');
  if (!token) {
    throw new Error('Set UPSTOX_TOKEN in Script Properties');
  }
  const queryString = Object.keys(params || {})
    .map(key => encodeURIComponent(key) + '=' + encodeURIComponent(params[key]))
    .join('&');
  const url = 'https://api.upstox.com' + path + (queryString ? '?' + queryString : '');

  for (let attempt = 0; attempt < 2; attempt++) {
    const response = UrlFetchApp.fetch(url, {
      headers: {
        Authorization: 'Bearer ' + token,
        Accept: 'application/json',
        'User-Agent': 'VolatilityLab/20.0 (read-only market-data client)'
      },
      muteHttpExceptions: true,
      followRedirects: false,
      timeoutSeconds: 15
    });
    const statusCode = response.getResponseCode();
    if (statusCode === 200) {
      let body;
      try {
        body = JSON.parse(response.getContentText());
      } catch (err) {
        throw new Error('Invalid broker JSON');
      }
      const hasData = (
        body.status === 'success' &&
        body.data &&
        (!Array.isArray(body.data) || body.data.length > 0) &&
        (Array.isArray(body.data) || Object.keys(body.data).length > 0)
      );
      if (!hasData) {
        throw new Error('Empty broker data');
      }
      return body.data;
    }
    if (attempt === 0 && (statusCode === 429 || statusCode >= 500)) {
      Utilities.sleep(1000);
      continue;
    }
    throw new Error('Broker HTTP ' + statusCode);
  }
}

function apiAll_(requests) {
  if (!requests || !requests.length) return [];
  if (typeof UrlFetchApp !== 'undefined' && typeof UrlFetchApp.fetchAll === 'function') {
    const token = props_().getProperty('UPSTOX_TOKEN');
    if (!token) {
      throw new Error('Set UPSTOX_TOKEN in Script Properties');
    }
    const payloads = requests.map(req => {
      const queryString = Object.keys(req.params || {})
        .map(key => encodeURIComponent(key) + '=' + encodeURIComponent(req.params[key]))
        .join('&');
      const url = 'https://api.upstox.com' + req.path + (queryString ? '?' + queryString : '');
      return {
        url: url,
        headers: {
          Authorization: 'Bearer ' + token,
          Accept: 'application/json',
          'User-Agent': 'VolatilityLab/20.0 (read-only market-data client)'
        },
        muteHttpExceptions: true,
        followRedirects: false,
        timeoutSeconds: 12
      };
    });
    const responses = UrlFetchApp.fetchAll(payloads);
    return responses.map((response, i) => {
      const req = requests[i];
      const statusCode = response.getResponseCode();
      if (statusCode === 200) {
        try {
          const body = JSON.parse(response.getContentText());
          if (body.status === 'success' && body.data) {
            return { label: req.label, path: req.path, params: req.params, data: body.data };
          }
        } catch (err) {
          return { label: req.label, path: req.path, params: req.params, error: 'Invalid broker JSON' };
        }
      }
      return { label: req.label, path: req.path, params: req.params, error: 'Broker HTTP ' + statusCode };
    });
  }
  const fallbackStarted = Date.now();
  return requests.map(req => {
    if (Date.now() - fallbackStarted >= 60000) {
      return { label: req.label, path: req.path, params: req.params, error: 'API fallback time budget exhausted' };
    }
    try {
      const data = api_(req.path, req.params);
      return { label: req.label, path: req.path, params: req.params, data: data };
    } catch (err) {
      return { label: req.label, path: req.path, params: req.params, error: safeError_(err) };
    }
  });
}

function save_(name, value) {
  const jsonContent = JSON.stringify(value);
  const jsonBlob = Utilities.newBlob(jsonContent, 'application/json', name + '.json');
  const gzipBlob = Utilities.gzip(jsonBlob);
  gzipBlob.setName(name + '.json.gz');
  return folder_().createFile(gzipBlob).getId();
}

function catalogue_(now) {
  const properties = props_();
  const today = day_(now);
  const cached = readJsonProperty_(properties, 'CATALOGUE', null);
  if (cached) {
    const parsed = cached;
    if (parsed.day === today) {
      return parsed;
    }
  }
  const response = UrlFetchApp.fetch(
    'https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz',
    { muteHttpExceptions: true, followRedirects: false, timeoutSeconds: 30 }
  );
  if (response.getResponseCode() !== 200) {
    if (cached) return cached;
    throw new Error('Instrument master unavailable');
  }
  const master = JSON.parse(Utilities.ungzip(response.getBlob()).getDataAsString());
  const catalogue = { day: today, indices: {} };

  for (const symbol of Object.keys(CONFIG.indices)) {
    const underlyingKey = CONFIG.indices[symbol];
    const futures = master
      .filter(x => x.underlying_key === underlyingKey && x.instrument_type === 'FUT')
      .map(x => ({
        instrument_key: x.instrument_key,
        expiry: typeof x.expiry === 'number' ? day_(new Date(x.expiry)) : String(x.expiry).slice(0, 10),
        trading_symbol: x.trading_symbol
      }))
      .filter(x => x.expiry >= today)
      .sort((a, b) => a.expiry.localeCompare(b.expiry))
      .slice(0, 3);

    if (futures.length < 2) {
      throw new Error('Futures contracts unavailable for ' + symbol);
    }
    const contracts = api_('/v2/option/contract', { instrument_key: underlyingKey });
    const maxExpiry = futures[futures.length - 1].expiry;
    let dates = [...new Set(contracts.map(x => String(x.expiry).slice(0, 10)))]
      .filter(x => x >= today && x <= maxExpiry)
      .sort();
    if (symbol === 'BANKNIFTY') {
      dates = dates.filter(x => futures.some(f => f.expiry === x));
    }
    if (!dates.length) {
      throw new Error('No option expiries for ' + symbol);
    }
    catalogue.indices[symbol] = {
      futures: futures,
      option_expiries: dates
    };
  }
  properties.setProperty('CATALOGUE', JSON.stringify(catalogue));
  return catalogue;
}

/**
 * Determines whether a given date is open, closed, or a special session.
 * NSE/FAOP/74467 and the December 2026 CAS rules define regular F&O hours.
 */
function regularCloseMinutes_(day) {
  return day < '2026-08-03' ? 930 : day < '2026-12-07' ? 940 : 945;
}

function indexExpiryMillis_(day) {
  // From Dec 7 expiring index contracts cease at 15:30 while the segment stays open.
  const mins = day >= '2026-12-07' ? 930 : regularCloseMinutes_(day);
  return Date.parse(day + 'T00:00:00+05:30') + mins * 60000;
}

function regularWindowLabel_(day) {
  const end = regularCloseMinutes_(day);
  return '09:15-' + Math.floor(end / 60) + ':' + String(end % 60).padStart(2,'0');
}

function calendarSpecial_(calendar, day) {
  const entry = calendar && calendar.special && calendar.special[day];
  if (!entry) return null;
  return {startMins:minute_(new Date(entry.start_time)), endMins:minute_(new Date(entry.end_time)),
    eodStartMins:minute_(new Date(entry.end_time)) + 5, eodEndMins:CONFIG.eodEndMins, label:'Broker NFO special session'};
}

function marketWindow_(dateObj) {
  const properties = props_();
  const targetDay = day_(dateObj);
  const year = targetDay.slice(0, 4);
  const weekday = Number(Utilities.formatDate(dateObj, CONFIG.timeZone, 'u')); // 1=Mon..7=Sun

  let customSpecial = {};
  try {
    customSpecial = JSON.parse(properties.getProperty('SPECIAL_SESSIONS_JSON') || '{}');
  } catch (e) {}
  const configuredSpecial = customSpecial[targetDay] || SPECIAL_SESSIONS[targetDay];
  if (configuredSpecial) {
    return {
      closed: false,
      special: true,
      label: configuredSpecial.label || 'Special Session',
      startMins: Number(configuredSpecial.startMins || 555),
      endMins: Number(configuredSpecial.endMins || regularCloseMinutes_(targetDay)),
      eodStartMins: Number(configuredSpecial.eodStartMins || 975),
      eodEndMins: Number(configuredSpecial.eodEndMins || 1320)
    };
  }

  const todayStr = targetDay;
  let calendar = readJsonProperty_(properties, 'MARKET_CALENDAR', null);
  const healthy = calendar?.year === year && calendar.checked === todayStr && Array.isArray(calendar.closed) && calendar.special;
  const lastFailure = Number(properties.getProperty('MARKET_CALENDAR_RETRY_AT') || 0);
  if (!healthy && (!lastFailure || Date.now() >= lastFailure)) {
    let refreshed = false;
    try {
      const response = UrlFetchApp.fetch('https://api.upstox.com/v2/market/holidays', {
        headers: {
          Accept: 'application/json',
          Authorization: 'Bearer ' + properties.getProperty('UPSTOX_TOKEN')
        },
        muteHttpExceptions: true,
        followRedirects: false,
        timeoutSeconds: 8
      });
      if (response.getResponseCode() === 200) {
        const body = JSON.parse(response.getContentText());
        if (body.status === 'success' && Array.isArray(body.data) && body.data.length) {
          calendar = {
            year: year,
            checked: todayStr,
            special: Object.fromEntries(body.data.flatMap(r => {
              const nfo = (r.open_exchanges || []).find(e => e.exchange === 'NFO');
              if (!nfo || !/^\d{4}-\d{2}-\d{2}$/.test(r.date) || !r.date.startsWith(year)) return [];
              const start = timestampMillis_(nfo.start_time), end = timestampMillis_(nfo.end_time);
              if (!(end > start) || day_(new Date(start)) !== r.date || day_(new Date(end)) !== r.date) return [];
              return [[r.date, {start_time:start, end_time:end}]];
            })),
            closed: body.data
              .filter(r => (
                r.date && r.date.startsWith(year) &&
                r.holiday_type === 'TRADING_HOLIDAY' &&
                (r.closed_exchanges || []).includes('NFO') &&
                !(r.open_exchanges || []).some(e => e.exchange === 'NFO')
              ))
              .map(r => r.date)
          };
          properties.setProperty('MARKET_CALENDAR', JSON.stringify(calendar));
          refreshed = true;
        }
      }
    } catch (err) {}
    if (refreshed && lastFailure) properties.deleteProperty('MARKET_CALENDAR_RETRY_AT');
    if (!refreshed) properties.setProperty('MARKET_CALENDAR_RETRY_AT', String(Date.now() + 5 * 60000));
  }

  const saved = calendar?.year === year && Array.isArray(calendar.closed) &&
    Number.isFinite(Date.parse(calendar.checked)) && calendar.checked <= targetDay ? calendar : null;
  const exchangeHolidays = NSE_FO_HOLIDAYS[year] || [];
  // A saved full-year calendar keeps announced dates through a failed refresh.
  // Fresh broker dates can include additional closures issued after the annual circular.
  const special = calendarSpecial_(saved,targetDay);
  if (special) return Object.assign({closed:false, special:true},special);
  if (weekday > 5) return {closed:true, special:false,label:saved?.checked === targetDay ? 'Weekend' : 'Market calendar unavailable'};
  if (saved?.closed.includes(targetDay) || exchangeHolidays.includes(targetDay)) {
    return { closed: true, special: false, label: 'Trading Holiday' };
  }

  const recent = saved && Date.parse(targetDay) - Date.parse(saved.checked) <= 7 * 86400000;
  if (!recent && !NSE_FO_HOLIDAYS[year]) {
    return {closed:true, special:false, label:'Market calendar unavailable'};
  }
  return {
    closed: false,
    special: false,
    label: 'Regular session ' + regularWindowLabel_(targetDay) + ' IST',
    startMins: CONFIG.regularStartMins, // 555 (09:15 IST)
    endMins: regularCloseMinutes_(targetDay),
    eodStartMins: regularCloseMinutes_(targetDay) + 5,
    eodEndMins: CONFIG.eodEndMins       // 1320 (22:00 IST)
  };
}

function marketClosed_(now) {
  const session = marketWindow_(now);
  if (session.label === 'Market calendar unavailable') {
    props_().setProperty('COLLECTOR_STATUS', 'Calendar unavailable; capture paused');
    throw new Error('Market calendar unavailable; retry next trigger');
  }
  return session.closed;
}

function publishedClosingRisk_(values) {
  const days = {};
  for (const [key, value] of Object.entries(values)) {
    if (!key.startsWith('CLOSING_HV_')) continue;
    try { Object.assign(days,JSON.parse(value)); } catch (err) {}
  }
  return days;
}

function dailyIndexCutoff_(day) {
  return new Date(Date.parse(day + 'T00:00:00Z') - 365 * 86400000).toISOString().slice(0,10);
}

function publishedDailyHistory_(properties, day, values) {
  values = values || properties.getProperties();
  const latest = JSON.parse(values.LAST_SNAPSHOT || '{}').daily_rows || {};
  const cutoff = dailyIndexCutoff_(day);
  const result = {};
  for (const symbol of Object.keys(CONFIG.indices)) {
    const days = {};
    for (const key of Object.keys(values)) {
      if (!key.startsWith('DAILY_IV_' + symbol + '_')) continue;
      try { Object.assign(days,JSON.parse(values[key])); } catch (err) {}
    }
    if (latest[symbol]) days[latest[symbol].d] = latest[symbol];
    result[symbol] = Object.keys(days).filter(d => d >= cutoff && d <= day).sort().map(d => days[d]);
  }
  return result;
}

function attachStraddleAnchors_(report, properties) {
  const stored = readJsonProperty_(properties,'SESSION_STRADDLE_ANCHORS',null);
  const prior = stored && stored.day === report.day ? stored.strikes : {};
  const strikes = Object.assign({}, prior);
  let changed = false;
  for (const rec of report.records) {
    if (!/^(NIFTY|BANKNIFTY)\|\d{4}-\d{2}-\d{2}$/.test(rec.label) || !Array.isArray(rec.data)) continue;
    if (!(strikes[rec.label] > 0)) {
      // A partial market capture cannot establish a new anchor.
      if (report.errors.length || report.pending.length) continue;
      const spot = Number(rec.data[0]?.underlying_spot_price);
      const listed = rec.data.map(row => Number(row.strike_price)).filter(k => Number.isFinite(k) && k > 0);
      if (!(spot > 0) || !listed.length) continue;
      strikes[rec.label] = listed.reduce((a,b) => Math.abs(a-spot) === Math.abs(b-spot) ? Math.min(a,b)
        : Math.abs(a-spot) < Math.abs(b-spot) ? a : b);
      changed = true;
    }
    rec.straddle_anchor_strike = strikes[rec.label];
  }
  if (changed) properties.setProperty('SESSION_STRADDLE_ANCHORS',JSON.stringify({day:report.day,strikes}));
}

function quoteStrikes_(rec) {
  const [sym] = rec.label.split('|');
  const rows = rec.data, step = sym === 'NIFTY' ? 50 : 100;
  const spot = Number(rows[0] && rows[0].underlying_spot_price);
  const atm = Math.round(spot / step) * step;
  const strikes = new Set();
  if (rec.straddle_anchor_strike > 0) strikes.add(rec.straddle_anchor_strike);
  // Dense near ATM, then sparse wings. Delta brackets below use observed broker data.
  for (const distance of [0,1,2,3,4,5,7,10]) {
    strikes.add(atm - distance * step);
    strikes.add(atm + distance * step);
  }
  for (const side of ['call_options','put_options']) {
    const sign = side === 'call_options' ? 1 : -1;
    const candidates = rows.map(r => ({k:Number(r.strike_price), d:sign * Number(r[side]?.option_greeks?.delta),
      iv:Number(r[side]?.option_greeks?.iv)})).filter(r => r.d > 0 && r.d < 1 && r.iv > 0);
    for (const target of [.10,.25]) {
      for (const pool of [candidates, candidates.filter(r => (r.k - spot) * sign > 0)]) {
        let lower = -Infinity, upper = Infinity;
        for (const r of pool) {
          if (r.d <= target) lower = Math.max(lower,r.d);
          if (r.d >= target) upper = Math.min(upper,r.d);
        }
        for (const r of pool) if (r.d === lower || r.d === upper) strikes.add(r.k);
      }
    }
  }
  return strikes;
}

function enrichSnapshotInputs_(report, started, fullDepth) {
  report.input_diagnostics = [];
  const requests = [];
  const chains = report.records.filter(r => /^(NIFTY|BANKNIFTY)\|\d{4}-\d{2}-\d{2}$/.test(r.label) && Array.isArray(r.data));
  const options = [];
  // Widen the front option chain at the frozen entry and scheduled intraday exit clocks.
  const decisionMinute = !fullDepth && [560, 565, 915].includes(minute_(new Date(started)));
  for (const rec of chains) {
    const [symbol, expiry] = rec.label.split('|');
    const front = report.catalogue?.indices?.[symbol]?.option_expiries?.[0];
    const selected = fullDepth || (decisionMinute && expiry === front) ? null : quoteStrikes_(rec);
    for (const row of rec.data) {
      if (selected && !selected.has(Number(row.strike_price))) continue;
      for (const side of ['call_options','put_options']) {
        const option = row[side];
        if (option && option.instrument_key) options.push(option);
        else report.input_diagnostics.push({item:rec.label, reason:'Missing option instrument key'});
      }
    }
  }
  const keys = Array.from(new Set(options.map(o => o.instrument_key)));
  // Apps Script URLs have a 2 KB limit, tighter than Upstox's 500-key limit.
  let batch = [];
  keys.forEach(key => {
    if (batch.length && (batch.length >= 500 || encodeURIComponent(batch.concat([key]).join(',')).length > 1700)) {
      requests.push({ label: 'option_quotes:' + requests.length, path: '/v2/market-quote/quotes', params: { instrument_key: batch.join(',') } });
      batch = [];
    }
    batch.push(key);
  });
  if (batch.length) requests.push({ label: 'option_quotes:' + requests.length, path: '/v2/market-quote/quotes', params: { instrument_key: batch.join(',') } });
  const quotes = new Map();
  // Standard market-data APIs permit 50 requests/second. Keep headroom and
  // space group starts even when the preceding fetch completes in under a second.
  let previousGroupStarted = null;
  for (let offset = 0; offset < requests.length; offset += 40) {
    if (previousGroupStarted !== null) {
      const remaining = 1000 - (Date.now() - previousGroupStarted);
      if (remaining > 0) Utilities.sleep(remaining);
    }
    if (Date.now() - started > CAPTURE_SAFE_DEADLINE_MS - 15000) throw new Error('Snapshot enrichment time budget exhausted');
    previousGroupStarted = Date.now();
    apiAll_(requests.slice(offset, offset + 40)).forEach(res => {
      try {
        if (res.error) throw new Error(res.error);
        Object.values(res.data || {}).forEach(q => { if (q && q.instrument_token) quotes.set(q.instrument_token, q); });
      } catch (err) {
        report.input_diagnostics.push({ item: res.label, reason: safeError_(err) });
      }
    });
  }
  options.forEach(option => {
    // Keep the original response for archive consumers, including prev_oi and close_price.
    option.chain_market_data = option.market_data;
    const q = quotes.get(option.instrument_key);
    if (!q || !Number.isFinite(timestampMillis_(q.timestamp))) {
      option.market_data = {}; // Withhold a book whose source time is unknown.
      report.input_diagnostics.push({ item: option.instrument_key, reason: 'Missing full quote or feed timestamp' });
      return;
    }
    const buy = q.depth && q.depth.buy && q.depth.buy[0] || {};
    const sell = q.depth && q.depth.sell && q.depth.sell[0] || {};
    option.market_data = Object.assign({}, option.chain_market_data, {
      ltp: q.last_price, bid_price: buy.price, ask_price: sell.price, bid_qty: buy.quantity, ask_qty: sell.quantity,
      volume: q.volume, oi: q.oi, quote_timestamp: q.timestamp, last_trade_time: q.last_trade_time,
      quote_source: 'Upstox /v2/market-quote/quotes'
    });
  });
  chains.forEach(rec => {
    rec.chain_retrieved_at = rec.retrieved_at;
    rec.retrieved_at = new Date().toISOString();
    if (!captureQuoteFresh_(rec.chain_retrieved_at, rec.retrieved_at)) {
      report.input_diagnostics.push({ item: rec.label, reason: 'Option-chain Greeks became stale during quote enrichment' });
    }
  });
}

function capture_(mode) {
  if (!['snapshot','test'].includes(mode)) throw new Error('Unsupported capture mode');
  const started = Date.now();
  const now = new Date();
  const today = day_(now);
  const properties = props_();

  const report = {
    schema: 1,
    capture_profile: 'minute_snapshots_only',
    provider: 'Upstox',
    day: today,
    mode: mode,
    retrieved_at: now.toISOString(),
    status: 'partial',
    records: [],
    errors: [],
    pending: []
  };

  try {
    if (mode === 'snapshot') {
      if (marketClosed_(now)) throw new Error('NFO session is closed');
      report.nfo_session_validated = true;
    }
    const catalogue = catalogue_(now);
    report.catalogue = catalogue;
    const keys = Object.values(CONFIG.indices).concat(['NSE_INDEX|India VIX']);
    Object.values(catalogue.indices).forEach(idx => {
      idx.futures.forEach(f => keys.push(f.instrument_key));
    });

    const requests = [{ label: 'quotes', path: '/v2/market-quote/quotes', params: { instrument_key: keys.join(',') } }];
    Object.keys(catalogue.indices).forEach(symbol => {
      catalogue.indices[symbol].option_expiries.forEach(expiry => {
        requests.push({
          label: symbol + '|' + expiry,
          path: '/v2/option/chain',
          params: {
            instrument_key: CONFIG.indices[symbol],
            expiry_date: expiry
          }
        });
      });
    });

    const responses = apiAll_(requests);
    for (const res of responses) {
      if (Date.now() - started > CAPTURE_SAFE_DEADLINE_MS) {
        report.errors.push({ item: res.label, reason: 'Runtime budget exhausted' });
        continue;
      }

      let data = res.data;
      if (res.error) {
        if (res.error === 'Broker HTTP 401' || res.error === 'Broker HTTP 403' || res.error === 'API fallback time budget exhausted') {
          report.errors.push({ item: res.label, reason: res.error });
          continue;
        }
        // api_ can spend up to about 31 seconds retrying.  Do not start a
        // fallback retry late in the run and risk Apps Script's 6-minute cap.
        if (Date.now() - started > CAPTURE_SAFE_DEADLINE_MS - 35000) {
          report.errors.push({ item: res.label, reason: 'Retry budget exhausted' });
          continue;
        }
        try {
          data = api_(res.path, res.params);
        } catch (retryErr) {
          report.errors.push({ item: res.label, reason: safeError_(retryErr) });
          continue;
        }
      }

      try {
        if (res.label === 'quotes') {
          if (!data || typeof data !== 'object' || Array.isArray(data)) {
            throw new Error('Invalid quote response');
          }
          const returnedTokens = new Set(Object.values(data).map(q => q.instrument_token));
          if (keys.some(k => !returnedTokens.has(k))) {
            throw new Error('Incomplete quote response');
          }
        } else if (res.path === '/v2/option/chain') {
          if (!Array.isArray(data) || !data.length) {
            throw new Error('Invalid option-chain response');
          }
          if (data.some(r => !r || r.expiry !== res.params.expiry_date || !Number.isFinite(Number(r.underlying_spot_price)) || Number(r.underlying_spot_price) <= 0)) {
            throw new Error('Wrong option expiry');
          }
        }

        report.records.push({
          label: res.label,
          retrieved_at: new Date().toISOString(),
          data: data
        });

      } catch (err) {
        report.errors.push({ item: res.label, reason: safeError_(err) });
      }
    }

    if (mode === 'snapshot') {
      try {
        attachStraddleAnchors_(report,properties);
        enrichSnapshotInputs_(report, started, properties.getProperty('OPTION_QUOTE_DEPTH') === 'full');
        report.enrichment_status = report.input_diagnostics.length ? 'partial' : 'complete';
      } catch (err) {
        report.enrichment_status = 'failed';
        report.input_diagnostics = (report.input_diagnostics || []).concat([{item:'enrichment',reason:safeError_(err)}]);
      }
    }
    report.status = (report.errors.length || report.pending.length) ? 'partial' : 'complete';
  } catch (err) {
    report.errors.push({ item: 'catalogue', reason: safeError_(err) });
  }

  report.fetch_seconds = (Date.now() - started) / 1000;
  // Session gates use the completed capture time; per-instrument event times
  // remain separate and are validated before entry or MTM.
  report.retrieved_at = new Date().toISOString();

  const archiveStarted = Date.now();
  const fileName = (report.target_day || today) + '_' + mode + '_' + Utilities.formatDate(now, CONFIG.timeZone, 'HHmmss') + '_' + report.status;
  const fileId = save_(fileName, report);
  report.archive_seconds = (Date.now() - archiveStarted) / 1000;
  report.file_id = fileId;
  report.total_seconds = (Date.now() - started) / 1000;

  const propertyKey = 'LAST_' + mode.toUpperCase();
  properties.setProperty(propertyKey, JSON.stringify({
    day: today,
    target_day: report.target_day || today,
    status: report.status,
    file_id: fileId,
    retrieved_at: report.retrieved_at,
    enrichment_status: report.enrichment_status,
    seconds: report.total_seconds,
    fetch_seconds: report.fetch_seconds,
    archive_seconds: report.archive_seconds,
    errors: report.errors,
    pending: report.pending
  }));

  if (mode === 'test' && report.status === 'complete') properties.setProperty('SUCCESS_TEST', today);

  console.log(JSON.stringify({
    mode: mode,
    status: report.status,
    seconds: report.total_seconds,
    records: report.records.length,
    errors: report.errors,
    pending: report.pending,
    file_id: fileId
  }));

  return report;
}

function safeError_(err) {
  const message = String(err.message || '');
  if (['Timestamped quote enrichment unavailable',
       'Snapshot enrichment time budget exhausted', 'Recovery archive unavailable', 'Recovery archive decode failed', 'Recovery archive unavailable or unreadable', 'Expired contract unavailable'].includes(message)) return message;
  const isWhitelisted = /^(Broker HTTP \d{3}|Empty broker data|Invalid broker JSON|Incomplete quote response|Wrong option expiry|Instrument master unavailable|(Three futures unavailable|Futures contracts unavailable) for (NIFTY|BANKNIFTY)|No option expiries for (NIFTY|BANKNIFTY)|Set UPSTOX_TOKEN in Script Properties|Invalid session date|Capture in progress|Invalid reconciliation archive|No pending EOD.*)$/.test(message);
  return isWhitelisted ? message : 'Capture failed; check permissions, network or quota';
}

function testCapture() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) {
    throw new Error('Capture already running');
  }
  try {
    const result = capture_('test');
    if (result.status !== 'complete') {
      throw new Error('Test incomplete; see sanitized execution log');
    }
  } finally {
    lock.releaseLock();
  }
}

function verifyArchive() {
  const last = JSON.parse(props_().getProperty('LAST_TEST') || 'null');
  if (!last) {
    throw new Error('Run testCapture first');
  }
  const file = DriveApp.getFileById(last.file_id);
  const record = JSON.parse(Utilities.ungzip(file.getBlob()).getDataAsString());
  if (record.status !== 'complete' || !record.records.length) {
    throw new Error('Archive verification failed');
  }
  console.log(JSON.stringify({
    verified: true,
    records: record.records.length,
    bytes: file.getSize(),
    folder_url: folder_().getUrl(),
    sharing: String(folder_().getSharingAccess())
  }));
}

function installIntradaySchedule() {
  const properties = props_();
  if (!properties.getProperty('UPSTOX_TOKEN')) {
    throw new Error('Set UPSTOX_TOKEN first');
  }
  if (!properties.getProperty('SUCCESS_TEST')) {
    throw new Error('Run successful testCapture first');
  }

  folder_();

  ScriptApp.getProjectTriggers()
    .filter(t => ['scheduledCapture', 'scheduledWatchdog', 'weeklyCleanup'].includes(t.getHandlerFunction()))
    .forEach(t => ScriptApp.deleteTrigger(t));

  ScriptApp.newTrigger('scheduledCapture')
    .timeBased()
    .everyMinutes(1)
    .create();


  properties.setProperty('INTERVAL_MINUTES', '1');
  properties.setProperty('SCHEDULE_MODE', 'intraday');

  console.log('Enabled: one-minute snapshots, weekdays exchange session hours; raw snapshots retained indefinitely.');
}

/**
 * Re-create a deleted minute trigger and surface stale capture state.  It is a
 * best-effort recovery mechanism; upstream platform and market-data outages are
 * still reported rather than fabricated as successful data.
 */
function scheduledWatchdog() {
  const properties = props_();
  if (properties.getProperty('SCHEDULE_MODE') !== 'intraday') return;
  const handlers = ScriptApp.getProjectTriggers().map(t => t.getHandlerFunction());
  if (!handlers.includes('scheduledCapture')) {
    ScriptApp.newTrigger('scheduledCapture').timeBased().everyMinutes(1).create();
    properties.setProperty('COLLECTOR_STATUS', 'recovered missing scheduledCapture trigger ' + new Date().toISOString());
  }
  const last = readJsonProperty_(properties, 'LAST_SNAPSHOT', null);
  const now = new Date();
  if (!last || !last.day || last.day !== day_(now)) return;
  const at = Number(properties.getProperty('LAST_SNAPSHOT_AT_MS') || 0);
  const session = marketWindow_(now);
  if (at > 0 && !session.closed && minute_(now) >= session.startMins && minute_(now) <= session.endMins
      && now.getTime() - at > 3 * 60000) {
    properties.setProperty('COLLECTOR_STATUS', 'stale snapshot health alert ' + new Date().toISOString());
  }
}

/**
 * Core time-driven capture scheduler (runs every minute).
 * Quota-optimized:
 * - Tightened regular market hours: date-specific NSE F&O hours
 * - Skips PropertiesService writes outside market hours so daily write quota is never wasted.
 */
function scheduledCapture() {
  const now = new Date();
  const today = day_(now);
  const weekday = Number(Utilities.formatDate(now, CONFIG.timeZone, 'u'));
  const mins = minute_(now);
  const properties = props_();

  if (properties.getProperty('SCHEDULE_MODE') !== 'intraday') {
    return;
  }

  let customSpecial = {};
  try {
    customSpecial = JSON.parse(properties.getProperty('SPECIAL_SESSIONS_JSON') || '{}');
  } catch (e) {}
  const calendar = readJsonProperty_(properties,'MARKET_CALENDAR',null);
  let sp = customSpecial[today] || SPECIAL_SESSIONS[today] ||
    (calendar?.checked <= today ? calendarSpecial_(calendar,today) : null);
  const weekend = weekday > 5;
  // Refresh before selecting a window, including after a failed premarket lookup.
  // Unknown weekends can still discover an announced afternoon session.
  if (!sp && mins >= 420 && mins < CONFIG.eodEndMins &&
      (calendar?.checked !== today || !calendar.special)) {
    const session = marketWindow_(now);
    if (session.special) sp = session;
    else if (weekend && session.label === 'Market calendar unavailable' && mins >= 540) {
      const status = 'Weekend session unverified · NSE F&O calendar unavailable · ' + today;
      if (properties.getProperty('COLLECTOR_STATUS') !== status) properties.setProperty('COLLECTOR_STATUS',status);
    }
  }
  if (weekend && !sp) return;
  const snapStart = sp ? sp.startMins : CONFIG.regularStartMins;
  const snapEnd = sp ? sp.endMins : regularCloseMinutes_(today);
  if (mins < snapStart || mins > snapEnd) return;
  const mode = 'snapshot';
  const policy = runtimePolicy_(properties);
  const limit = snapshotAdmissionLimit_(policy, mins, snapStart, snapEnd);
  const pauseMessage = limit < policy.snapshot_minutes * 60000
    ? 'snapshot paused to preserve runtime for later session captures (' + limit / 60000 + ' minutes). ' + today
    : mode + ' runtime admission limit reached (' + limit / 60000 + ' minutes). ' + today;
  let budget = readRuntimeBudget_(properties, 'RUNTIME_BUDGET', Date.now());
  if (budget.ms >= limit) {
    if (properties.getProperty('COLLECTOR_STATUS') !== pauseMessage) properties.setProperty('COLLECTOR_STATUS', pauseMessage);
    return;
  }

  const started = Date.now();
  let closed;
  try {
    closed = marketClosed_(now);
  } catch (err) {
    addRuntimeBudget_(properties, 'RUNTIME_BUDGET', Date.now() - started);
    throw err;
  }

  // Avoid capture work and status writes on known closed holidays.
  if (closed) {
    addRuntimeBudget_(properties, 'RUNTIME_BUDGET', Date.now() - started);
    return;
  }

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(100)) {
    properties.setProperty('COLLECTOR_STATUS', 'capture overlap; retrying next minute ' + new Date().toISOString());
    addRuntimeBudget_(properties, 'RUNTIME_BUDGET', Date.now() - started);
    return;
  }

  let failureMessage = null;

  try {
    const slotNumber = mins - snapStart;
    const slot = today + '|' + slotNumber;
    const previousSlot = properties.getProperty('LAST_SNAPSHOT_SLOT') || '';
    if (previousSlot === slot) return;
    const [previousDay, previousNumber] = previousSlot.split('|');
    if (previousDay !== today) {
      properties.deleteProperty('SNAPSHOT_GAP');
      properties.deleteProperty('SNAPSHOT_GAP_DAY');
    } else if (slotNumber > Number(previousNumber) + 1) {
      properties.setProperty('SNAPSHOT_GAP', previousSlot + ' to ' + slot + ' detected ' + now.toISOString());
      properties.setProperty('SNAPSHOT_GAP_DAY', today);
    }

    budget = readRuntimeBudget_(properties, 'RUNTIME_BUDGET', Date.now());
    if (budget.ms >= limit) {
      properties.setProperty('COLLECTOR_STATUS', pauseMessage);
      return;
    }

    const result = capture_(mode);

    if (result.status === 'complete') {
      const saved = {
        LAST_SNAPSHOT_SLOT: slot,
        LAST_SNAPSHOT_AT_MS: String(Date.now()),
        COLLECTOR_STATUS: 'snapshot complete ' + new Date().toISOString()
      };
      if (result.file_id) saved.LATEST_SNAPSHOT_FILE_ID = result.file_id;
      properties.setProperties(saved);
    }

    if (result.status !== 'complete') {
      const details = (result.errors || [])
        .map(x => x.reason)
        .concat(result.pending || [])
        .filter(Boolean)
        .slice(0, 3)
        .join('; ');
      failureMessage = 'Capture ' + mode + ' incomplete' + (details ? ': ' + details : '');
      throw new Error('Capture incomplete; sanitized details in execution log and Drive archive');
    }
  } catch (err) {
    const message = failureMessage || safeError_(err);
    properties.setProperty('COLLECTOR_STATUS', 'failed ' + mode + ' ' + new Date().toISOString() + ' · ' + message);
    throw err;
  } finally {
    const ended = Date.now();
    const b = readRuntimeBudget_(properties, 'RUNTIME_BUDGET', ended);
    b.ms += ended - started;
    properties.setProperty('RUNTIME_BUDGET', JSON.stringify(b));
    lock.releaseLock();
  }
}

function auditStatus() {
  const properties = props_();
  console.log(JSON.stringify({
    closed_today: marketClosed_(new Date()),
    sharing: String(folder_().getSharingAccess()),
    handlers: ScriptApp.getProjectTriggers().map(t => t.getHandlerFunction()),
    interval: properties.getProperty('INTERVAL_MINUTES') || '1',
    regular_window_ist: regularWindowLabel_(day_(new Date())),
    snapshot_retention_days: CONFIG.snapshotRetentionDays,
    status: properties.getProperty('COLLECTOR_STATUS'),
    last_snapshot: marketFieldsOnly_(readJsonProperty_(properties, 'LAST_SNAPSHOT', null)),
    last_eod: readJsonProperty_(properties, 'LAST_EOD', null)
  }));
}

/**
 * Public Web API endpoint for volatilitylab.in.
 * Caches responses in CacheService for 55 seconds to eliminate redundant Drive/Gzip reads.
 */
function captureIntegrity_(properties,stamp,day) {
    const capturedAt = timestampMillis_(stamp);
    return {
      status: properties.getProperty('COLLECTOR_STATUS'),
      captured_at: stamp || null,
      age_seconds: Number.isFinite(capturedAt) ? Math.max(0, (Date.now() - capturedAt) / 1000) : null,
      snapshot_gap: properties.getProperty('SNAPSHOT_GAP_DAY') === String(day || '').slice(0, 10)
        ? (properties.getProperty('SNAPSHOT_GAP') || 'Detected snapshot gap')
        : null,
      raw_snapshot_retention: 'indefinite'
    };
}

// Public capture reads can encounter pre-separation archives or status properties.
function marketFieldsOnly_(data) {
  if (data && typeof data === 'object') {
    for (const key of Object.keys(data)) if (key.startsWith('forward_')) delete data[key];
  }
  return data;
}

function doGet(e) {
  const action = e?.parameter?.action || 'latest';
  const callback = e?.parameter?.callback || '';
  const readAction = action === 'latest';
  if (callback && (!readAction || !/^[A-Za-z_$][A-Za-z0-9_$]{0,63}$/.test(callback))) {
    return ContentService.createTextOutput(JSON.stringify({error:'Invalid callback'})).setMimeType(ContentService.MimeType.JSON);
  }
  const respond = json => callback
    ? ContentService.createTextOutput(callback + '(' + json + ');').setMimeType(ContentService.MimeType.JAVASCRIPT)
    : ContentService.createTextOutput(json).setMimeType(ContentService.MimeType.JSON);
  try {
    const properties = props_();
    const requiredKey = properties.getProperty('WEBHOOK_KEY');
    if (requiredKey && e?.parameter?.key !== requiredKey) return respond(JSON.stringify({error:'Unauthorized'}));
    const cache = scriptCache_();
    if (action === 'list_days') {
      const folderId = folder_().getId();
      const days = new Set();
      let pageToken = '';
      for (let pageIndex = 0; pageIndex < 20; pageIndex++) {
        const page = listDriveFilesPage_(folderId, pageToken, 'modifiedTime desc');
        for (const f of (page.files || [])) {
          const name = f.name || '';
          if ((name.indexOf('_snapshot_') !== -1 || name.indexOf('_eod_') !== -1) && name.endsWith('_complete.json.gz')) {
            const match = name.match(/^(\d{4}-\d{2}-\d{2})/);
            if (match) days.add(match[1]);
          }
        }
        if (!page.nextPageToken || days.size >= 60) break;
        pageToken = page.nextPageToken;
      }
      return respond(JSON.stringify({
        days: Array.from(days).sort().reverse()
      }));
    }

    const requestedDay = e && e.parameter && e.parameter.day;
    if (requestedDay && action === 'latest') {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(requestedDay)) throw new Error('Invalid archive date');
      const folderId = folder_().getId();
      let matchId = null;
      let pageToken = '';
      for (let pageIndex = 0; pageIndex < 50 && !matchId; pageIndex++) {
        const page = listDriveFilesPage_(folderId, pageToken, 'modifiedTime desc');
        for (const f of (page.files || [])) {
          const name = f.name || '';
          if (name.startsWith(requestedDay) && (name.indexOf('_snapshot_') !== -1 || name.indexOf('_eod_') !== -1) && name.endsWith('_complete.json.gz')) {
            matchId = f.id;
            break;
          }
        }
        if (!page.nextPageToken) break;
        pageToken = page.nextPageToken;
      }
      if (!matchId) throw new Error('No complete archive for ' + requestedDay);
      const file = DriveApp.getFileById(matchId);
      return respond(JSON.stringify(marketFieldsOnly_(JSON.parse(Utilities.ungzip(file.getBlob()).getDataAsString()))));
    }


    if (action === 'status') {
      if (cache) {
        const cachedStatus = cache.get('DOGET_COLLECTOR_STATUS_V1');
        if (cachedStatus) {
          return ContentService.createTextOutput(cachedStatus).setMimeType(ContentService.MimeType.JSON);
        }
      }
      let triggers = [];
      try {
        triggers = ScriptApp.getProjectTriggers().map(t => t.getHandlerFunction());
      } catch (err) {}
      const statusJson = JSON.stringify({
        status: properties.getProperty('COLLECTOR_STATUS'),
        last_snapshot: JSON.stringify(marketFieldsOnly_(readJsonProperty_(properties,'LAST_SNAPSHOT',null))),
        last_snapshot_slot: properties.getProperty('LAST_SNAPSHOT_SLOT'),
        mode: properties.getProperty('SCHEDULE_MODE'),
        interval: properties.getProperty('INTERVAL_MINUTES') || '1',
        regular_window_ist: regularWindowLabel_(day_(new Date())),
        triggers: triggers,
        calendar: properties.getProperty('MARKET_CALENDAR'),
        budget: properties.getProperty('RUNTIME_BUDGET'),
        runtime_policy: runtimePolicy_(properties),
        last_snapshot_at_ms: properties.getProperty('LAST_SNAPSHOT_AT_MS'),
        snapshot_gap: properties.getProperty('SNAPSHOT_GAP'),
        collection_only: true,
        capture_profile: 'minute_snapshots_only',
        raw_snapshot_retention: 'indefinite'
      });
      if (cache) {
        try { cache.put('DOGET_COLLECTOR_STATUS_V1', statusJson, CONFIG.doGetCacheSeconds); } catch (e) {}
      }
      return ContentService.createTextOutput(statusJson).setMimeType(ContentService.MimeType.JSON);
    }

    if (action === 'closing_risk') {
      return ContentService.createTextOutput(JSON.stringify(publishedClosingRisk_(properties.getProperties())))
        .setMimeType(ContentService.MimeType.JSON);
    }

    if (action !== 'latest') return respond(JSON.stringify({error:'Unknown data action'}));
    const fileId = properties.getProperty('LATEST_SNAPSHOT_FILE_ID');
    const last = readJsonProperty_(properties,'LAST_SNAPSHOT',null);
    if (fileId && last && last.file_id === fileId && last.status === 'complete' && last.retrieved_at &&
        e?.parameter?.since === last.retrieved_at) {
      return respond(JSON.stringify({unchanged:true,retrieved_at:last.retrieved_at,
        capture_integrity:captureIntegrity_(properties,last.retrieved_at,last.day)}));
    }
    if (cache && fileId) {
      const cachedSnap = cache.get('DOGET_CAPTURE_DATA_V1_' + fileId);
      if (cachedSnap) return respond(cachedSnap);
    }

    let file = null;

    if (fileId) {
      try {
        file = DriveApp.getFileById(fileId);
      } catch (err) {
        file = null;
      }
    }

    if (!file) {
      // A lost file-id must not make recovery dependent on Drive's arbitrary
      // iterator order or a small prefix of an indefinitely retained archive.
      const page = listDriveFilesPage_(folder_().getId(), '', 'modifiedTime desc');
      const latest = (page.files || []).find(f =>
        /^\d{4}-\d{2}-\d{2}_snapshot_\d{6}_complete\.json\.gz$/.test(f.name)
      );
      if (latest && latest.id) file = DriveApp.getFileById(latest.id);
    }

    if (!file) {
      return respond(JSON.stringify({ error: 'No snapshot files found' }));
    }

    const uncompressed = Utilities.ungzip(file.getBlob());
    const payload = JSON.parse(uncompressed.getDataAsString());
    if (payload.schema !== 1 || payload.provider !== 'Upstox' || payload.mode !== 'snapshot'
        || payload.status !== 'complete' || payload.nfo_session_validated !== true
        || !Array.isArray(payload.records) || !payload.records.length) {
      throw new Error('Latest archived snapshot is incomplete or invalid');
    }
    // Older immutable archives can contain unrelated paper data. Publish market data only.
    marketFieldsOnly_(payload);
    payload.capture_integrity = captureIntegrity_(properties,payload.retrieved_at,payload.day);
    const calendar = readJsonProperty_(properties,'MARKET_CALENDAR',null);
    if (calendar && Array.isArray(calendar.closed)) {
      payload.exchange_calendar = {year:calendar.year, closed:calendar.closed, special:calendar.special || {}};
    }
    const values = properties.getProperties();
    payload.daily_history = publishedDailyHistory_(properties,day_(new Date()),values);
    payload.closing_risk = publishedClosingRisk_(values);
    const outStr = JSON.stringify(payload);
    if (cache && fileId && file.getId() === fileId && outStr.length < 95000) {
      try { cache.put('DOGET_CAPTURE_DATA_V1_' + fileId, outStr, CONFIG.doGetCacheSeconds); } catch (e) {}
    }
    return respond(outStr);
  } catch (err) {
    return respond(JSON.stringify({ error: safeError_(err) }));
  }
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
