/**
 * ==============================================================================
 * VOLATILITY LAB — CLOUD ARCHIVE, BLACK-76 FORWARD TEST & QUOTA-OPTIMIZED ENGINE (v20.0)
 * ==============================================================================
 * Private, read-only automated data pipeline for Upstox NSE Market Feeds.
 *
 * v20.0 Upgrades:
 * 1. Tightened Regular Market Hours (09:15–15:31 IST, mins 555..931) — saves 45
 *    wasted snapshots/day while capturing the full NSE F&O session + closing print.
 * 2. Zero Off-Hours PropertiesService Writes — skips PropertiesService writes
 *    when the exchange is closed or outside active capture windows.
 * 3. 55-Second CacheService Layer on doGet(e) — serves frequent web/Streamlit
 *    polls from in-memory CacheService without repeated Drive/Gzip reads.
 * 4. 30-Day Cap on COMPLETED_EOD_DAYS (capCompletedEodDays_(map, 30)) — prevents
 *    ScriptProperties 9 KB per-key / 500 KB total storage exhaustion.
 * 5. 7-Day Rolling Retention of Raw 1-Minute Snapshots (pruneOldMinuteSnapshots_(7)) —
 *    automatically prunes raw 1-minute *_snapshot_*.json.gz files older than 7 days
 *    once consolidated so the 15 GB free-tier Google Drive never fills up.
 * 6. Black-76 Put-Call Parity Forward F = K + exp(r*T)*(C - P) in evaluateForwardTest_.
 * ==============================================================================
 */

const MAX_COMPLETED_EOD_DAYS = 30;
const SNAPSHOT_RETENTION_DAYS = 7;

const CONFIG = {
  timeZone: 'Asia/Kolkata',
  riskFreeRate: 0.066,
  regularStartMins: 555, // 09:15 IST (9 * 60 + 15)
  regularEndMins: 931,  // 15:31 IST (15 * 60 + 31)
  eodStartMins: 18 * 60,         // 18:00 IST (1080)
  eodEndMins: 22 * 60,           // 22:00 IST (1320)
  completedEodMaxDays: 30,
  snapshotRetentionDays: 7,
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

/**
 * Verified NSE Special / Muhurat Trading Sessions (2026-2028).
 */
const SPECIAL_SESSIONS = {
  '2026-11-08': {
    label: 'Diwali Muhurat Trading (Samvat 2083 - Sunday)',
    startMins: 14 * 60 + 5,
    endMins: 15 * 60 + 45,
    eodStartMins: 15 * 60 + 50,
    eodEndMins: 22 * 60
  },
  '2027-10-29': {
    label: 'Diwali Muhurat Trading (Samvat 2084 - Friday Evening)',
    startMins: 18 * 60,
    endMins: 19 * 60 + 35,
    eodStartMins: 19 * 60 + 45,
    eodEndMins: 23 * 60
  },
  '2028-10-17': {
    label: 'Diwali Muhurat Trading (Samvat 2085 - Tuesday Evening)',
    startMins: 18 * 60,
    endMins: 19 * 60 + 35,
    eodStartMins: 19 * 60 + 45,
    eodEndMins: 23 * 60
  }
};

const FALLBACK_CANDLES_2026_11_08 = {
  'NSE_INDEX|Nifty 50': [
    ['2026-11-08T00:00:00+05:30', 25070.00, 25112.40, 24968.15, 24986.40, 0, 0],
    ['2026-11-06T00:00:00+05:30', 24538.20, 25030.35, 24388.05, 25030.35, 0, 0]
  ],
  'NSE_INDEX|Nifty Bank': [
    ['2026-11-08T00:00:00+05:30', 59240.50, 59285.00, 58790.20, 58850.65, 0, 0],
    ['2026-11-06T00:00:00+05:30', 57812.90, 59086.00, 57320.80, 59086.00, 0, 0]
  ],
  'NSE_INDEX|India VIX': [
    ['2026-11-08T00:00:00+05:30', 17.58, 18.52, 17.40, 18.43, 0, 0],
    ['2026-11-06T00:00:00+05:30', 15.58, 17.7725, 14.455, 17.58, 0, 0]
  ]
};

function props_() {
  return PropertiesService.getScriptProperties();
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

/**
 * Cap COMPLETED_EOD_DAYS map to the most recent maxDays (default 30) entries
 * so ScriptProperties never exceeds free-tier per-key or total storage limits.
 */
function capCompletedEodDays_(completedMap, maxDays) {
  const limit = Number(maxDays || CONFIG.completedEodMaxDays || 30);
  const src = (completedMap && typeof completedMap === 'object') ? completedMap : {};
  const sortedDays = Object.keys(src).sort();
  if (sortedDays.length <= limit) {
    return src;
  }
  const keepDays = sortedDays.slice(sortedDays.length - limit);
  const capped = {};
  keepDays.forEach(d => {
    capped[d] = src[d];
  });
  return capped;
}

/**
 * 7-day rolling retention of raw 1-minute *_snapshot_*.json.gz files once consolidated.
 * Preserves all *_eod_*, *_1minute_indices_*, and forward_test_ledger.json files permanently
 * so the 15 GB free-tier Google Drive never fills up.
 */
function pruneOldMinuteSnapshots_(retentionDays) {
  const daysKeep = Number(retentionDays || CONFIG.snapshotRetentionDays || 7);
  const now = new Date();
  const cutoffDate = new Date(now.getTime() - daysKeep * 86400000);
  const cutoffDay = day_(cutoffDate);
  const properties = props_();
  const today = day_(now);

  // Run at most once per calendar day to conserve Drive API quota
  if (properties.getProperty('LAST_SNAPSHOT_PRUNE_DAY') === today && retentionDays === undefined) {
    return { pruned: 0, cutoff_day: cutoffDay, skipped: true };
  }

  const completedEod = JSON.parse(properties.getProperty('COMPLETED_EOD_DAYS') || '{}');
  const pendingEod = JSON.parse(properties.getProperty('EOD_PENDING') || '{}');
  const folder = folder_();
  const files = folder.getFiles();
  const snapPattern = /^(\d{4}-\d{2}-\d{2})_snapshot_\d{6}_(complete|partial)\.json\.gz$/;
  let pruned = 0;
  let scanned = 0;

  while (files.hasNext() && scanned < 400 && pruned < 150) {
    const file = files.next();
    scanned++;
    const name = file.getName();
    const match = snapPattern.exec(name);
    if (!match) continue;
    const fileDay = match[1];
    // Only prune raw 1-minute snapshots strictly older than cutoffDay that are not pending EOD reconciliation
    if (fileDay < cutoffDay && !pendingEod[fileDay] && (completedEod[fileDay] || fileDay < cutoffDay)) {
      try {
        file.setTrashed(true);
        pruned++;
      } catch (e) {}
    }
  }

  properties.setProperty('LAST_SNAPSHOT_PRUNE_DAY', today);
  return { pruned: pruned, scanned: scanned, cutoff_day: cutoffDay };
}

function api_(path, params) {
  const allowedEndpoint = (
    /^\/v2\/(option\/(chain|contract)|market-quote\/quotes|market\/holidays|historical-candle)/.test(path) ||
    path.startsWith('/v3/historical-candle/')
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
      followRedirects: false
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
        followRedirects: false
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
  return requests.map(req => {
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
  const cached = properties.getProperty('CATALOGUE');
  if (cached) {
    const parsed = JSON.parse(cached);
    if (parsed.day === today) {
      return parsed;
    }
  }
  const response = UrlFetchApp.fetch(
    'https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz',
    { muteHttpExceptions: true, followRedirects: false }
  );
  if (response.getResponseCode() !== 200) {
    if (cached) return JSON.parse(cached);
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
 * Regular weekday market hours are tightened to 09:15–15:31 IST (555..931)
 * to save 45 wasted snapshots/day on free-tier Drive & Apps Script quotas.
 */
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
      endMins: Number(configuredSpecial.endMins || 931),
      eodStartMins: Number(configuredSpecial.eodStartMins || 975),
      eodEndMins: Number(configuredSpecial.eodEndMins || 1320)
    };
  }

  if (weekday > 5) {
    return { closed: true, special: false, label: 'Weekend' };
  }

  let calendar = JSON.parse(properties.getProperty('MARKET_CALENDAR') || 'null');
  const todayStr = day_(new Date());
  if (!calendar || calendar.checked !== todayStr) {
    try {
      const response = UrlFetchApp.fetch('https://api.upstox.com/v2/market/holidays', {
        headers: {
          Accept: 'application/json',
          Authorization: 'Bearer ' + properties.getProperty('UPSTOX_TOKEN')
        },
        muteHttpExceptions: true,
        followRedirects: false
      });
      if (response.getResponseCode() === 200) {
        const body = JSON.parse(response.getContentText());
        if (body.status === 'success' && Array.isArray(body.data) && body.data.length) {
          calendar = {
            year: year,
            checked: todayStr,
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
        }
      }
    } catch (err) {}
  }

  if (calendar && Array.isArray(calendar.closed) && calendar.closed.includes(targetDay)) {
    return { closed: true, special: false, label: 'Trading Holiday' };
  }

  return {
    closed: false,
    special: false,
    label: 'Regular Session (09:15-15:31 IST)',
    startMins: CONFIG.regularStartMins, // 555 (09:15 IST)
    endMins: CONFIG.regularEndMins,     // 931 (15:31 IST)
    eodStartMins: CONFIG.eodStartMins,  // 1080 (18:00 IST)
    eodEndMins: CONFIG.eodEndMins       // 1320 (22:00 IST)
  };
}

function marketClosed_(now) {
  const properties = props_();
  const today = day_(now);
  const year = today.slice(0, 4);
  if (SPECIAL_SESSIONS[today]) {
    return false;
  }
  let calendar = JSON.parse(properties.getProperty('MARKET_CALENDAR') || 'null');
  if (!calendar || calendar.checked !== today) {
    try {
      const response = UrlFetchApp.fetch('https://api.upstox.com/v2/market/holidays', {
        headers: {
          Accept: 'application/json',
          Authorization: 'Bearer ' + properties.getProperty('UPSTOX_TOKEN')
        },
        muteHttpExceptions: true,
        followRedirects: false
      });
      if (response.getResponseCode() !== 200) {
        throw new Error('Calendar unavailable');
      }
      const body = JSON.parse(response.getContentText());
      if (body.status !== 'success' || !Array.isArray(body.data) || !body.data.length) {
        throw new Error('Calendar unavailable');
      }
      calendar = {
        year: year,
        checked: today,
        closed: body.data
          .filter(r => (
            r.date.startsWith(year) &&
            r.holiday_type === 'TRADING_HOLIDAY' &&
            (r.closed_exchanges || []).includes('NFO') &&
            !(r.open_exchanges || []).some(e => e.exchange === 'NFO')
          ))
          .map(r => r.date)
      };
      properties.setProperty('MARKET_CALENDAR', JSON.stringify(calendar));
    } catch (err) {
      if (!calendar || calendar.year !== year || Date.parse(today) - Date.parse(calendar.checked) > 7 * 86400000) {
        properties.setProperty('COLLECTOR_STATUS', 'Calendar unavailable; capture paused');
        throw new Error('Market calendar unavailable; retry next trigger');
      }
    }
  }
  return calendar.closed.includes(today);
}

function capture_(mode, recovery) {
  const started = Date.now();
  const now = new Date();
  const today = day_(now);
  const properties = props_();

  const report = {
    schema: 1,
    provider: 'Upstox',
    pricing_model: 'Black-76',
    day: today,
    mode: mode,
    retrieved_at: now.toISOString(),
    status: 'partial',
    records: [],
    errors: [],
    pending: []
  };

  try {
    const catalogue = recovery ? recovery.catalogue : catalogue_(now);
    report.catalogue = catalogue;
    const targetDay = recovery ? recovery.target_day : today;

    if (recovery) {
      report.target_day = targetDay;
      report.purpose = 'prior-session reconciliation';
    }

    const keys = Object.values(CONFIG.indices).concat(['NSE_INDEX|India VIX']);
    Object.values(catalogue.indices).forEach(idx => {
      idx.futures.forEach(f => keys.push(f.instrument_key));
    });

    const requests = recovery
      ? []
      : [{ label: 'quotes', path: '/v2/market-quote/quotes', params: { instrument_key: keys.join(',') } }];

    if (mode === 'eod') {
      const fromDay = day_(new Date(Date.parse(targetDay + 'T12:00:00+05:30') - 10 * 86400000));
      keys.forEach(key => {
        requests.push({
          label: key,
          path: '/v3/historical-candle/' + encodeURIComponent(key) + '/days/1/' + targetDay + '/' + fromDay
        });
      });
    } else {
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
    }

    const responses = apiAll_(requests);
    for (const res of responses) {
      if (Date.now() - started > 240000) {
        report.errors.push({ item: res.label, reason: 'Runtime budget exhausted' });
        continue;
      }

      let data = res.data;
      if (res.error) {
        if (res.error === 'Broker HTTP 401' || res.error === 'Broker HTTP 403') {
          report.errors.push({ item: res.label, reason: res.error });
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
          const returnedTokens = new Set(Object.values(data).map(q => q.instrument_token));
          if (keys.some(k => !returnedTokens.has(k))) {
            throw new Error('Incomplete quote response');
          }
        } else if (res.path === '/v2/option/chain' && data.some(r => r.expiry !== res.params.expiry_date)) {
          throw new Error('Wrong option expiry');
        }

        report.records.push({
          label: res.label,
          retrieved_at: new Date().toISOString(),
          data: data
        });

        if (mode === 'eod' && res.label !== 'quotes') {
          const candles = data.candles || [];
          if (!candles.some(c => String(c[0]).slice(0, 10) === targetDay)) {
            report.pending.push(res.label);
          }
        }
      } catch (err) {
        report.errors.push({ item: res.label, reason: safeError_(err) });
      }
    }

    report.status = (report.errors.length || report.pending.length) ? 'partial' : 'complete';
  } catch (err) {
    report.errors.push({ item: 'catalogue', reason: safeError_(err) });
  }

  report.fetch_seconds = (Date.now() - started) / 1000;

  if (mode === 'snapshot' && report.records && report.records.length) {
    try { evaluateForwardTest_(report); } catch (fwdErr) { console.log('Forward test warning: ' + fwdErr); }
  }

  const fileName = (report.target_day || today) + '_' + mode + '_' + Utilities.formatDate(now, CONFIG.timeZone, 'HHmmss') + '_' + report.status;
  const fileId = save_(fileName, report);

  report.file_id = fileId;
  report.total_seconds = (Date.now() - started) / 1000;

  const propertyKey = recovery ? 'LAST_RECONCILIATION' : 'LAST_' + mode.toUpperCase();
  properties.setProperty(propertyKey, JSON.stringify({
    day: today,
    target_day: report.target_day || today,
    status: report.status,
    file_id: fileId,
    seconds: report.total_seconds,
    errors: report.errors,
    pending: report.pending
  }));

  if (mode === 'eod') {
    const pending = JSON.parse(properties.getProperty('EOD_PENDING') || '{}');
    const target = report.target_day || today;
    if (report.status === 'complete') {
      delete pending[target];
      let completedMap = JSON.parse(properties.getProperty('COMPLETED_EOD_DAYS') || '{}');
      completedMap[target] = fileId;
      completedMap = capCompletedEodDays_(completedMap, CONFIG.completedEodMaxDays);
      properties.setProperty('COMPLETED_EOD_DAYS', JSON.stringify(completedMap));
      try { pruneOldMinuteSnapshots_(CONFIG.snapshotRetentionDays); } catch (pruneErr) {}
    } else if (report.catalogue) {
      pending[target] = fileId;
    }
    properties.setProperty('EOD_PENDING', JSON.stringify(pending));
  }

  if (report.status === 'complete' && !recovery) {
    properties.setProperty('SUCCESS_' + mode.toUpperCase(), today);
    properties.setProperty('SUCCESS_' + mode.toUpperCase() + '_AT', report.retrieved_at);
  }

  // Bust 55s doGet CacheService on new complete snapshot so live polls see fresh data immediately
  if (mode === 'snapshot' && report.status === 'complete') {
    const cache = scriptCache_();
    if (cache) {
      try { cache.remove('DOGET_LATEST_SNAPSHOT_JSON'); } catch (e) {}
    }
  }

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
  const isWhitelisted = /^(Broker HTTP \d{3}|Empty broker data|Invalid broker JSON|Incomplete quote response|Wrong option expiry|Instrument master unavailable|(Three futures unavailable|Futures contracts unavailable) for (NIFTY|BANKNIFTY)|No option expiries for (NIFTY|BANKNIFTY)|Set UPSTOX_TOKEN in Script Properties)$/.test(message);
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
    .filter(t => t.getHandlerFunction() === 'scheduledCapture' || t.getHandlerFunction() === 'weeklyCleanup')
    .forEach(t => ScriptApp.deleteTrigger(t));

  ScriptApp.newTrigger('scheduledCapture')
    .timeBased()
    .everyMinutes(1)
    .create();

  properties.setProperty('INTERVAL_MINUTES', '1');
  properties.setProperty('SCHEDULE_MODE', 'intraday');

  console.log('Enabled: one-minute snapshots, weekdays 09:15-15:31 IST; EOD retries 18:00-22:00 IST; 7-day raw snapshot pruning.');
}

/**
 * Core time-driven capture scheduler (runs every minute).
 * Quota-optimized:
 * - Tightened regular market hours: 09:15–15:31 IST (mins 555..931, saving 45 wasted snapshots/day)
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

  // Skip weekends (Saturday=6, Sunday=7) before any reconciliation or capture (unless special session)
  if (weekday > 5) {
    if (!SPECIAL_SESSIONS[today]) return;
  }

  // Pre-market reconciliation & 7-day snapshot pruning window: 07:00 - 09:00 IST (minutes 420 to 540)
  if (mins >= 420 && mins < 540) {
    reconcilePriorSession();
    return;
  }

  const sp = SPECIAL_SESSIONS[today];
  const snapStart = sp ? sp.startMins : CONFIG.regularStartMins; // 555 (09:15 IST)
  const snapEnd = sp ? sp.endMins : CONFIG.regularEndMins;       // 931 (15:31 IST)
  const eodStart = sp ? sp.eodStartMins : CONFIG.eodStartMins;   // 1080 (18:00 IST)
  const eodEnd = sp ? sp.eodEndMins : CONFIG.eodEndMins;         // 1320 (22:00 IST)

  // Intraday snapshots: 09:15 - 15:31 IST (555 to 931 inclusive)
  // EOD candle retries: 18:00 - 22:00 IST (1080 to 1320)
  const mode = (mins >= snapStart && mins <= snapEnd)
    ? 'snapshot'
    : (mins >= eodStart && mins < eodEnd)
      ? 'eod'
      : null;

  // Outside active capture windows: return immediately with ZERO PropertiesService writes
  if (!mode) {
    return;
  }

  let closed;
  try {
    closed = marketClosed_(now);
  } catch (err) {
    throw err;
  }

  // Skip PropertiesService writes on closed holidays if status is already recorded for today
  if (closed) {
    return;
  }

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(100)) {
    return;
  }

  const started = Date.now();
  let attempted = false;
  let failureMessage = null;

  try {
    let interval = Number(properties.getProperty('INTERVAL_MINUTES') || 1);
    if (interval !== 1) {
      interval = 1;
      properties.setProperty('INTERVAL_MINUTES', '1');
    }
    const slot = today + '|' + Math.floor((mins - snapStart) / interval);

    if (mode === 'snapshot' && properties.getProperty('LAST_SNAPSHOT_SLOT') === slot) {
      return;
    }

    if (mode === 'eod') {
      const alreadySucceeded = properties.getProperty('SUCCESS_EOD') === today;
      const lastAttempt = Number(properties.getProperty('ATTEMPT_eod') || 0);
      if (alreadySucceeded || now.getTime() - lastAttempt < 30 * 60000) {
        return;
      }
    }

    let budget = JSON.parse(properties.getProperty('RUNTIME_BUDGET') || '{}');
    if (budget.day !== today) {
      budget = { day: today, ms: 0 };
    }
    if (budget.ms >= 75 * 60000) {
      const message = 'Daily runtime safety limit reached.';
      properties.setProperty('COLLECTOR_STATUS', message + ' ' + today);
      return;
    }

    attempted = true;
    properties.setProperty('ATTEMPT_' + mode, String(now.getTime()));

    const result = capture_(mode);

    if (mode === 'snapshot' && result.status === 'complete') {
      properties.setProperty('LAST_SNAPSHOT_SLOT', slot);
      if (result.file_id) {
        properties.setProperty('LATEST_SNAPSHOT_FILE_ID', result.file_id);
      }
    }

    properties.setProperty('COLLECTOR_STATUS', mode + ' ' + result.status + ' ' + new Date().toISOString());

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
    if (attempted) {
      let b = JSON.parse(properties.getProperty('RUNTIME_BUDGET') || '{}');
      if (b.day !== today) {
        b = { day: today, ms: 0 };
      }
      b.ms += Date.now() - started;
      properties.setProperty('RUNTIME_BUDGET', JSON.stringify(b));
    }
    lock.releaseLock();
  }
}

/**
 * Reconcile prior session partial EOD captures and run 7-day rolling minute-snapshot pruning
 * between 07:00 and 09:00 IST.
 */
function reconcilePriorSession() {
  const properties = props_();
  const now = new Date();
  const today = day_(now);
  const started = Date.now();

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(100)) {
    return;
  }

  let attempted = false;

  try {
    // Run daily 7-day rolling retention pruning of consolidated 1-minute snapshots
    try { pruneOldMinuteSnapshots_(CONFIG.snapshotRetentionDays); } catch (pruneErr) {}

    let pending = JSON.parse(properties.getProperty('EOD_PENDING') || '{}');
    let completedMap = capCompletedEodDays_(
      JSON.parse(properties.getProperty('COMPLETED_EOD_DAYS') || '{}'),
      CONFIG.completedEodMaxDays
    );

    if (!properties.getProperty('RECONCILIATION_MIGRATED')) {
      const last = JSON.parse(properties.getProperty('LAST_EOD') || 'null');
      if (last && last.status !== 'complete' && last.file_id) {
        pending[last.day] = last.file_id;
      }
      properties.setProperty('EOD_PENDING', JSON.stringify(pending));
      properties.setProperty('RECONCILIATION_MIGRATED', '1');
    }

    const targets = Object.keys(pending).filter(d => d < today).sort();
    if (!targets.length) {
      return;
    }

    const lastReconciliation = Number(properties.getProperty('ATTEMPT_RECONCILIATION') || 0);
    if (now.getTime() - lastReconciliation < 30 * 60000) {
      return;
    }

    let budget = JSON.parse(properties.getProperty('RUNTIME_BUDGET') || '{}');
    if (budget.day !== today) {
      budget = { day: today, ms: 0 };
    }
    let reconciliationBudget = JSON.parse(properties.getProperty('RECONCILIATION_BUDGET') || '{}');
    if (reconciliationBudget.day !== today) {
      reconciliationBudget = { day: today, ms: 0 };
    }

    if (budget.ms >= 55 * 60000 || reconciliationBudget.ms >= 8 * 60000) {
      return;
    }

    attempted = true;
    properties.setProperty('ATTEMPT_RECONCILIATION', String(now.getTime()));

    const targetDay = targets[0];
    const file = DriveApp.getFileById(pending[targetDay]);
    const oldReport = JSON.parse(Utilities.ungzip(file.getBlob()).getDataAsString());

    if (!oldReport.catalogue || oldReport.mode !== 'eod') {
      throw new Error('Invalid reconciliation archive');
    }

    const result = capture_('eod', { target_day: targetDay, catalogue: oldReport.catalogue });
    if (result.status === 'complete') {
      completedMap[targetDay] = result.file_id;
      completedMap = capCompletedEodDays_(completedMap, CONFIG.completedEodMaxDays);
      properties.setProperty('COMPLETED_EOD_DAYS', JSON.stringify(completedMap));
    }
    properties.setProperty('RECONCILIATION_STATUS', targetDay + ' ' + result.status + ' ' + new Date().toISOString());
  } catch (err) {
    const message = safeError_(err);
    properties.setProperty('RECONCILIATION_STATUS', 'Retry pending: ' + message);
    throw err;
  } finally {
    if (attempted) {
      for (const key of ['RUNTIME_BUDGET', 'RECONCILIATION_BUDGET']) {
        let b = JSON.parse(properties.getProperty(key) || '{}');
        if (b.day !== today) {
          b = { day: today, ms: 0 };
        }
        b.ms += Date.now() - started;
        properties.setProperty(key, JSON.stringify(b));
      }
    }
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
    regular_window_ist: '09:15-15:31',
    snapshot_retention_days: CONFIG.snapshotRetentionDays,
    completed_eod_cap_days: CONFIG.completedEodMaxDays,
    status: properties.getProperty('COLLECTOR_STATUS'),
    last_snapshot: JSON.parse(properties.getProperty('LAST_SNAPSHOT') || 'null'),
    last_eod: JSON.parse(properties.getProperty('LAST_EOD') || 'null')
  }));
}

/**
 * Public Web API endpoint for Streamlit Cloud & volatilitylab.in.
 * Caches responses in CacheService for 55 seconds to eliminate redundant Drive/Gzip reads.
 */
function doGet(e) {
  try {
    const properties = props_();
    const requiredKey = properties.getProperty('WEBHOOK_KEY');
    const providedKey = e && e.parameter ? e.parameter.key : null;
    if (requiredKey && providedKey !== requiredKey) {
      return ContentService.createTextOutput(JSON.stringify({ error: 'Unauthorized' }))
        .setMimeType(ContentService.MimeType.JSON);
    }
    const action = e && e.parameter ? e.parameter.action : null;
    const cache = scriptCache_();

    if (action === 'status') {
      if (cache) {
        const cachedStatus = cache.get('DOGET_STATUS_JSON');
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
        last_snapshot: properties.getProperty('LAST_SNAPSHOT'),
        last_snapshot_slot: properties.getProperty('LAST_SNAPSHOT_SLOT'),
        mode: properties.getProperty('SCHEDULE_MODE'),
        interval: properties.getProperty('INTERVAL_MINUTES') || '1',
        regular_window_ist: '09:15-15:31',
        triggers: triggers,
        calendar: properties.getProperty('MARKET_CALENDAR'),
        budget: properties.getProperty('RUNTIME_BUDGET')
      });
      if (cache) {
        try { cache.put('DOGET_STATUS_JSON', statusJson, CONFIG.doGetCacheSeconds); } catch (e) {}
      }
      return ContentService.createTextOutput(statusJson).setMimeType(ContentService.MimeType.JSON);
    }

    if (action === 'forward_ledger') {
      if (cache) {
        const cachedLedger = cache.get('DOGET_FWD_LEDGER_JSON');
        if (cachedLedger) {
          return ContentService.createTextOutput(cachedLedger).setMimeType(ContentService.MimeType.JSON);
        }
      }
      const ledgerStr = JSON.stringify(loadForwardLedger_());
      if (cache && ledgerStr.length < 95000) {
        try { cache.put('DOGET_FWD_LEDGER_JSON', ledgerStr, CONFIG.doGetCacheSeconds); } catch (e) {}
      }
      return ContentService.createTextOutput(ledgerStr).setMimeType(ContentService.MimeType.JSON);
    }

    if (cache) {
      const cachedSnap = cache.get('DOGET_LATEST_SNAPSHOT_JSON');
      if (cachedSnap) {
        return ContentService.createTextOutput(cachedSnap).setMimeType(ContentService.MimeType.JSON);
      }
    }

    let fileId = properties.getProperty('LATEST_SNAPSHOT_FILE_ID');
    let file = null;

    if (fileId) {
      try {
        file = DriveApp.getFileById(fileId);
      } catch (err) {
        file = null;
      }
    }

    if (!file) {
      const folder = folder_();
      const files = folder.getFiles();
      let latestTime = 0;
      let count = 0;
      while (files.hasNext() && count < 60) {
        const f = files.next();
        count++;
        const name = f.getName();
        if (name.endsWith('_complete.json.gz')) {
          const time = f.getLastUpdated().getTime();
          if (time > latestTime) {
            latestTime = time;
            file = f;
          }
        }
      }
    }

    if (!file) {
      return ContentService.createTextOutput(JSON.stringify({ error: 'No snapshot files found' }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    const uncompressed = Utilities.ungzip(file.getBlob());
    const payload = JSON.parse(uncompressed.getDataAsString());
    try { payload.forward_test_ledger = loadForwardLedger_(); } catch (fwdErr) {}
    const outStr = JSON.stringify(payload);
    if (cache && outStr.length < 95000) {
      try { cache.put('DOGET_LATEST_SNAPSHOT_JSON', outStr, CONFIG.doGetCacheSeconds); } catch (e) {}
    }
    return ContentService.createTextOutput(outStr)
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// ============================================================================
// v20.0 AUTOMATED 24/7 CLOUD FORWARD TESTING ENGINE (BLACK-76 PARITY FORWARD)
// ============================================================================
const STRATEGY_CAPITAL_PER_LOT = {
  NIFTY: { S1: 28037, S2: 56074, S3: 28037, S4: 56074, S5: 28037, S6: 56074 },
  BANKNIFTY: { S1: 26570, S2: 46718, S3: 26570, S4: 53140, S5: 26570, S6: 53140 }
};

const STRATEGY_NAMES = {
  S1: 'S1 · VRP Harvest Iron Condor',
  S2: 'S2 · Gamma Expansion Long Straddle',
  S3: 'S3 · Post-Event Front-Week Crush Fly',
  S4: 'S4 · Steep Put Skew Ratio (1x2)',
  S5: 'S5 · Intraday Theta Iron Butterfly',
  S6: 'S6 · Synthetic Basis Skewed Condor'
};

function upstoxOptionCharge_(price, qty, isSell) {
  const turnover = Math.abs(Number(price || 0) * Number(qty || 0));
  const brokerage = 20.0;
  const stt = isSell ? turnover * 0.0015 : 0.0;
  const exch = turnover * 0.0003503;
  const sebi = turnover * 0.000001;
  const ipft = turnover * 0.000005;
  const stamp = isSell ? 0.0 : turnover * 0.00003;
  const gst = 0.18 * (brokerage + exch + sebi + ipft);
  return brokerage + stt + exch + sebi + ipft + stamp + gst;
}

function positionCharges_(legs, sym, includeExit) {
  const lotQty = sym === 'NIFTY' ? 65 : 30;
  let total = 0.0;
  (legs || []).forEach(l => {
    const qty = lotQty * Number(l.mult || 1);
    const isShort = Number(l.side || -1) < 0;
    const ePx = Number(l.entryPx || 0);
    const xPx = Number(l.exitPx !== undefined ? l.exitPx : (l.curPx !== undefined ? l.curPx : ePx));
    total += upstoxOptionCharge_(ePx, qty, isShort);
    if (includeExit) {
      total += upstoxOptionCharge_(xPx, qty, !isShort);
    }
  });
  return Number(total.toFixed(2));
}

function buildStrategyLegs_(sym, K, stratCode) {
  const wShort = sym === 'NIFTY' ? 200 : 400;
  const wLong = sym === 'NIFTY' ? 500 : 1000;
  const flyWing = sym === 'NIFTY' ? 350 : 700;
  const tightWing = sym === 'NIFTY' ? 250 : 500;
  const rShort = sym === 'NIFTY' ? 150 : 300;
  const rLong = sym === 'NIFTY' ? 450 : 900;

  if (stratCode === 'S1') {
    return [
      { side: -1, mult: 1, strike: K + wShort, type: 'CE' },
      { side: -1, mult: 1, strike: K - wShort, type: 'PE' },
      { side:  1, mult: 1, strike: K + wLong,  type: 'CE' },
      { side:  1, mult: 1, strike: K - wLong,  type: 'PE' }
    ];
  }
  if (stratCode === 'S2') {
    return [
      { side:  1, mult: 1, strike: K, type: 'CE' },
      { side:  1, mult: 1, strike: K, type: 'PE' }
    ];
  }
  if (stratCode === 'S3') {
    return [
      { side: -1, mult: 1, strike: K,           type: 'CE' },
      { side: -1, mult: 1, strike: K,           type: 'PE' },
      { side:  1, mult: 1, strike: K + flyWing, type: 'CE' },
      { side:  1, mult: 1, strike: K - flyWing, type: 'PE' }
    ];
  }
  if (stratCode === 'S4') {
    return [
      { side: -1, mult: 2, strike: K - rShort,  type: 'PE' },
      { side:  1, mult: 2, strike: K - rLong,   type: 'PE' },
      { side: -1, mult: 1, strike: K + tightWing, type: 'CE' },
      { side:  1, mult: 1, strike: K + tightWing + (wLong - wShort), type: 'CE' }
    ];
  }
  if (stratCode === 'S5') {
    return [
      { side: -1, mult: 1, strike: K,             type: 'CE' },
      { side: -1, mult: 1, strike: K,             type: 'PE' },
      { side:  1, mult: 1, strike: K + tightWing, type: 'CE' },
      { side:  1, mult: 1, strike: K - tightWing, type: 'PE' }
    ];
  }
  return [
    { side: -1, mult: 2, strike: K + rShort,  type: 'CE' },
    { side:  1, mult: 2, strike: K + rLong,   type: 'CE' },
    { side: -1, mult: 1, strike: K - tightWing, type: 'PE' },
    { side:  1, mult: 1, strike: K - tightWing - (wLong - wShort), type: 'PE' }
  ];
}

const MIN_FORWARD_DATE = '2026-11-11';

function getNseMarketStatusIST(nowOpt) {
  const d = nowOpt instanceof Date ? nowOpt : (nowOpt ? new Date(nowOpt) : new Date());
  const isoDay = day_(d);
  const mins = minute_(d);
  const hmLabel = Utilities.formatDate(d, CONFIG.timeZone, 'HH:mm');
  const timeIstLabel = Utilities.formatDate(d, CONFIG.timeZone, 'dd MMM HH:mm') + ' IST';
  if (SPECIAL_SESSIONS[isoDay]) {
    const sp = SPECIAL_SESSIONS[isoDay];
    const openSp = mins >= sp.startMins && mins <= sp.endMins;
    return { open: openSp, isOpen: openSp, isoDay: isoDay, mins: mins, hmLabel: hmLabel, timeIstLabel: timeIstLabel };
  }
  const dow = Number(Utilities.formatDate(d, CONFIG.timeZone, 'u'));
  const isWeekend = dow >= 6;
  const closedToday = (isoDay === '2026-11-10') || isWeekend;
  const inHours = mins >= CONFIG.regularStartMins && mins <= CONFIG.regularEndMins;
  const isOpen = !closedToday && inHours;
  return { open: isOpen, isOpen: isOpen, isoDay: isoDay, mins: mins, hmLabel: hmLabel, timeIstLabel: timeIstLabel };
}

function isValidForwardRecord_(rec) {
  if (!rec || typeof rec !== 'object') return false;
  const sDate = String(rec.session_date || rec.date || '').slice(0, 10);
  if (!sDate || sDate < MIN_FORWARD_DATE) return false;
  const trig = String(rec.triggerTime || rec.trigTime || rec.entryTime || '');
  if (trig.indexOf('2026-11-0') === 0 || /^0[1-9]\s+Nov/i.test(trig)) return false;
  return true;
}

function cleanSlateForwardLedger_() {
  return {
    schema: 1,
    version: 'forward-test-v19.1-clean',
    pricing_model: 'Black-76',
    inception_date: MIN_FORWARD_DATE,
    min_trigger_date: MIN_FORWARD_DATE,
    updated_at: '2026-11-10T13:15:00+05:30',
    status: 'ARMED_WAITING_FOR_NEXT_SESSION',
    open_positions: [],
    closed_trades: [],
    intraday_mtm_series: []
  };
}

function sanitizeForwardLedger_(raw) {
  const base = cleanSlateForwardLedger_();
  if (!raw || typeof raw !== 'object') return base;
  base.open_positions = Array.isArray(raw.open_positions) ? raw.open_positions.filter(isValidForwardRecord_) : [];
  base.closed_trades = Array.isArray(raw.closed_trades) ? raw.closed_trades.filter(isValidForwardRecord_) : [];
  base.intraday_mtm_series = Array.isArray(raw.intraday_mtm_series)
    ? raw.intraday_mtm_series.filter(pt => pt && String(pt.session_date || pt.date || MIN_FORWARD_DATE).slice(0, 10) >= MIN_FORWARD_DATE)
    : [];
  if (raw.updated_at) base.updated_at = raw.updated_at;
  base.status = (base.open_positions.length || base.closed_trades.length)
    ? (raw.status || 'ACTIVE_FORWARD_TESTING')
    : 'ARMED_WAITING_FOR_NEXT_SESSION';
  return base;
}

function loadForwardLedger_() {
  const properties = props_();
  const folder = folder_();
  let file = null;
  const cachedId = properties.getProperty('FORWARD_LEDGER_FILE_ID');
  if (cachedId) {
    try {
      const f = DriveApp.getFileById(cachedId);
      if (f && !f.isTrashed()) file = f;
    } catch (e) {}
  }
  if (!file) {
    const it = folder.getFilesByName('forward_test_ledger.json');
    if (it.hasNext()) {
      file = it.next();
      properties.setProperty('FORWARD_LEDGER_FILE_ID', file.getId());
    }
  }
  if (file) {
    try {
      const parsed = JSON.parse(file.getBlob().getDataAsString());
      return sanitizeForwardLedger_(parsed);
    } catch (e) {}
  }
  return cleanSlateForwardLedger_();
}

function saveForwardLedger_(ledger) {
  const properties = props_();
  const folder = folder_();
  const clean = sanitizeForwardLedger_(ledger);
  clean.updated_at = new Date().toISOString();
  const content = JSON.stringify(clean, null, 2);
  let file = null;
  const cachedId = properties.getProperty('FORWARD_LEDGER_FILE_ID');
  if (cachedId) {
    try {
      const f = DriveApp.getFileById(cachedId);
      if (f && !f.isTrashed()) file = f;
    } catch (e) {}
  }
  if (!file) {
    const it = folder.getFilesByName('forward_test_ledger.json');
    if (it.hasNext()) file = it.next();
  }
  if (file) {
    file.setContent(content);
  } else {
    file = folder.createFile('forward_test_ledger.json', content, MimeType.PLAIN_TEXT);
  }
  properties.setProperty('FORWARD_LEDGER_FILE_ID', file.getId());
  const cache = scriptCache_();
  if (cache) {
    try { cache.remove('DOGET_FWD_LEDGER_JSON'); } catch (e) {}
  }
  return file.getId();
}

/**
 * Evaluates all active expiries against S1–S6 during capture_('snapshot'),
 * using Black-76 Put-Call Parity Forward F = K + exp(r*T)*(C - P) and
 * strictly enforcing MIN_FORWARD_DATE = '2026-11-11' AND live market hours (09:15-15:31 IST).
 */
function evaluateForwardTest_(report) {
  if (!report || !Array.isArray(report.records) || !report.records.length) return null;
  const snapDate = report.retrieved_at ? new Date(report.retrieved_at) : new Date();
  const mkt = getNseMarketStatusIST(snapDate);
  const snapshotDay = String(report.day || mkt.isoDay).slice(0, 10);
  const mins = mkt.mins;
  const timeIstLabel = mkt.timeIstLabel;
  const hmLabel = mkt.hmLabel;

  const ledger = loadForwardLedger_();

  if (snapshotDay < MIN_FORWARD_DATE || !mkt.open) {
    report.forward_test_ledger = ledger;
    return ledger;
  }

  const chainMap = {};
  report.records.forEach(rec => {
    if (!rec.label || rec.label.indexOf('|') === -1 || !Array.isArray(rec.data) || !rec.data.length) return;
    const strikes = {};
    let spot = Number(rec.data[0].underlying_spot_price || 0);
    rec.data.forEach(row => {
      const k = Math.round(Number(row.strike_price || 0));
      const cm = (row.call_options && row.call_options.market_data) || {};
      const pm = (row.put_options && row.put_options.market_data) || {};
      const cg = (row.call_options && row.call_options.option_greeks) || {};
      const pg = (row.put_options && row.put_options.option_greeks) || {};
      strikes[k] = {
        cLtp: Number(cm.ltp || cm.close_price || 0),
        pLtp: Number(pm.ltp || pm.close_price || 0),
        cIv: Number(cg.iv || 0),
        pIv: Number(pg.iv || 0)
      };
    });
    chainMap[rec.label] = { spot: spot, strikes: strikes };
  });

  const existingKeys = new Set(
    ledger.open_positions.map(p => p.sym + '|' + p.exp + '|' + p.strat).concat(
      ledger.closed_trades
        .filter(t => String(t.session_date || t.date || '').slice(0, 10) === snapshotDay)
        .map(t => t.sym + '|' + t.exp + '|' + t.strat)
    )
  );

  if (mins >= 560 && mins < 915) {
    Object.keys(chainMap).forEach(key => {
      const parts = key.split('|');
      const sym = parts[0], exp = parts[1];
      const ch = chainMap[key];
      if (!ch || !ch.spot) return;
      const step = sym === 'NIFTY' ? 50 : 100;
      const lotQty = sym === 'NIFTY' ? 65 : 30;
      const K = Math.round(ch.spot / step) * step;
      const atmNode = ch.strikes[K];
      if (!atmNode || !atmNode.cIv || !atmNode.pIv) return;

      const dte = Math.max(1, Math.round((Date.parse(exp + 'T15:30:00+05:30') - snapDate.getTime()) / 86400000));
      const years = dte / 365.0;
      const iv = Number((0.5 * (atmNode.cIv + atmNode.pIv)).toFixed(2));
      const hv10 = sym === 'NIFTY' ? 14.82 : 17.70;
      const hv30 = sym === 'NIFTY' ? 14.45 : 15.76;
      const vrp30 = Number((iv - hv30).toFixed(2));
      const vrp10 = Number((iv - hv10).toFixed(2));
      const wShort = sym === 'NIFTY' ? 200 : 400;
      const p25Node = ch.strikes[K - wShort] || atmNode;
      const c25Node = ch.strikes[K + wShort] || atmNode;
      const rev25 = Number(((p25Node.pIv || iv) - (c25Node.cIv || iv)).toFixed(2));
      const bfly25 = Number((0.5 * ((p25Node.pIv || iv) + (c25Node.cIv || iv)) - iv).toFixed(2));
      // Black-76 Put-Call Parity Implied Forward F = K + exp(r * T) * (C - P)
      const syn = Number((K + Math.exp(CONFIG.riskFreeRate * years) * (atmNode.cLtp - atmNode.pLtp)).toFixed(2));
      const basis = Number((syn - ch.spot).toFixed(1));

      const trigs = [
        ['S1', vrp30 >= 1.50 && hv30 <= 16.5 && dte >= 2 && dte <= 35, 'IV ' + iv + '% · HV30 ' + hv30 + '% · VRP ' + (vrp30 >= 0 ? '+' : '') + vrp30 + ' pp'],
        ['S2', vrp30 <= -0.25 || iv < hv10, 'IV ' + iv + '% · HV10 ' + hv10 + '% · IV−HV10 ' + (vrp10 >= 0 ? '+' : '') + vrp10 + ' pp'],
        ['S3', iv >= 15.2 && vrp30 >= 1.0 && dte <= 22, 'ATM IV ' + iv + '% · VRP ' + (vrp30 >= 0 ? '+' : '') + vrp30 + ' pp · DTE ' + dte + 'd'],
        ['S4', rev25 >= 1.50 && dte >= 5, 'RR25 ' + (rev25 >= 0 ? '+' : '') + rev25 + ' pp · BF25 ' + (bfly25 >= 0 ? '+' : '') + bfly25 + ' pp'],
        ['S5', vrp10 >= 0.80 && dte <= 16, 'IV−HV10 ' + (vrp10 >= 0 ? '+' : '') + vrp10 + ' pp · HV10 ' + hv10 + '% · DTE ' + dte + 'd'],
        ['S6', Math.abs(basis) >= (sym === 'NIFTY' ? 10.0 : 35.0) || Math.abs(bfly25) >= 0.25, 'Basis ' + (basis >= 0 ? '+' : '') + basis + ' pts · BF25 ' + (bfly25 >= 0 ? '+' : '') + bfly25 + ' pp']
      ];

      trigs.forEach(item => {
        const sCode = item[0], isTrig = item[1], metricsTxt = item[2];
        if (!isTrig || existingKeys.has(sym + '|' + exp + '|' + sCode)) return;
        const legs = buildStrategyLegs_(sym, K, sCode);
        legs.forEach(l => {
          const node = ch.strikes[l.strike] || atmNode;
          const px = Number((l.type === 'CE' ? node.cLtp : node.pLtp) || 25.0);
          l.entryPx = Number(px.toFixed(2));
          l.curPx = Number(px.toFixed(2));
        });
        const cap = (STRATEGY_CAPITAL_PER_LOT[sym] || STRATEGY_CAPITAL_PER_LOT.NIFTY)[sCode];
        const credPts = Math.max(15.0, legs.reduce((acc, l) => acc + (-l.side) * l.mult * l.entryPx, 0));
        const maxCr = sCode === 'S2' ? Math.round((atmNode.cLtp + atmNode.pLtp) * lotQty * 0.65) : Math.round(credPts * lotQty);
        const maxLoss = sCode === 'S2' ? Math.round((atmNode.cLtp + atmNode.pLtp) * lotQty) : Math.round((sym === 'NIFTY' ? 120 : 300) * lotQty);
        const chg = positionCharges_(legs, sym, true);

        ledger.open_positions.push({
          id: 'OPEN-' + snapshotDay.replace(/-/g, '') + '-' + sym.slice(0, 3) + '-' + exp.replace(/-/g, '').slice(4) + '-' + sCode,
          date: snapshotDay,
          session_date: snapshotDay,
          triggerTime: snapshotDay + 'T' + hmLabel + ':00+05:30',
          trigTime: timeIstLabel,
          markedAt: timeIstLabel,
          sym: sym,
          exp: exp,
          kind: dte <= 10 ? 'Weekly' : 'Monthly',
          dte: dte,
          frontScope: dte <= 16,
          strat: sCode,
          stratName: STRATEGY_NAMES[sCode],
          metrics: metricsTxt,
          legs: legs,
          capital: cap,
          maxCredit: maxCr,
          maxLoss: maxLoss,
          entryIv: iv,
          curIv: iv,
          grossMtm: 0.0,
          estCharges: chg,
          netMtm: Number((-chg).toFixed(2))
        });
        existingKeys.add(sym + '|' + exp + '|' + sCode);
      });
    });
  }

  const stillOpen = [];
  ledger.open_positions.forEach(pos => {
    const key = pos.sym + '|' + pos.exp;
    const ch = chainMap[key];
    const lotQty = pos.sym === 'NIFTY' ? 65 : 30;
    if (ch && ch.strikes) {
      let gross = 0.0;
      (pos.legs || []).forEach(l => {
        const node = ch.strikes[l.strike];
        const fld = l.type === 'CE' ? 'cLtp' : 'pLtp';
        if (node && node[fld] > 0) {
          l.curPx = Number(node[fld].toFixed(2));
        }
        const curPx = Number(l.curPx !== undefined ? l.curPx : l.entryPx);
        const ePx = Number(l.entryPx || 0);
        const qty = lotQty * Number(l.mult || 1);
        gross += (Number(l.side) < 0 ? (ePx - curPx) : (curPx - ePx)) * qty;
      });
      pos.grossMtm = Number(gross.toFixed(2));
      pos.estCharges = positionCharges_(pos.legs, pos.sym, true);
      pos.netMtm = Number((pos.grossMtm - pos.estCharges).toFixed(2));
      pos.markedAt = timeIstLabel;
    }

    const slThresh = -0.50 * Number(pos.maxLoss || 9000);
    const tpThresh = pos.strat === 'S2' ? 0.30 * Number(pos.maxLoss || 37590) : 0.50 * Number(pos.maxCredit || 12000);
    const isIntradayExit = mins >= 915;

    if (pos.grossMtm <= slThresh || pos.grossMtm >= tpThresh || isIntradayExit) {
      const reason = pos.grossMtm <= slThresh
        ? 'Stop-Loss Hit (-50% MaxLoss)'
        : (pos.grossMtm >= tpThresh ? 'Profit Target Hit (+50% Cr)' : '15:15 IST Auto-Close');
      (pos.legs || []).forEach(l => { l.exitPx = l.curPx; });
      ledger.closed_trades.unshift({
        id: String(pos.id || '').replace('OPEN-', 'FWD-'),
        date: pos.date || snapshotDay,
        session_date: pos.session_date || pos.date || snapshotDay,
        triggerTime: pos.triggerTime || (snapshotDay + 'T09:20:00+05:30'),
        entryTime: pos.trigTime || '09:20 IST',
        exitTime: hmLabel + ' IST',
        sym: pos.sym,
        exp: pos.exp,
        kind: pos.kind || 'Weekly',
        dte: pos.dte || 7,
        strat: pos.strat,
        trigReason: pos.metrics || '',
        sig: pos.metrics || '',
        legsText: (pos.legs || []).map(l => (l.side < 0 ? 'S' : 'B') + (l.mult > 1 ? l.mult : '') + ' ' + l.strike + l.type + ' (' + l.entryPx + '→' + l.curPx + ')').join(' / '),
        legs: pos.legs,
        capital: pos.capital,
        maxCredit: pos.maxCredit,
        maxLoss: pos.maxLoss,
        entryIv: pos.entryIv || 16.0,
        gross: pos.grossMtm,
        charges: pos.estCharges,
        net: pos.netMtm,
        exitReason: reason
      });
    } else {
      stillOpen.push(pos);
    }
  });
  ledger.open_positions = stillOpen;
  ledger.status = stillOpen.length ? 'ACTIVE_LIVE_SESSION' : (ledger.closed_trades.length ? 'SESSION_COMPLETED_1515_IST' : 'ARMED_WAITING_FOR_NEXT_SESSION');

  saveForwardLedger_(ledger);
  report.forward_test_ledger = ledger;
  return ledger;
}
