/**
 * ==============================================================================
 * VOLATILITY LAB — CLOUD ARCHIVE, BLACK-76 FORWARD TEST & INTEGRITY ENGINE (v21.0)
 * ==============================================================================
 * Private, read-only automated data pipeline for Upstox NSE Market Feeds.
 *
 * v20.0 Upgrades:
 * 1. Tightened Regular Market Hours (09:15–15:31 IST, mins 555..931) — saves 45
 *    wasted snapshots/day while capturing the full NSE F&O session + closing print.
 * 2. Skips capture/status writes outside active windows; bounded scheduler state
 *    records trigger runtime for the free-tier execution quota.
 * 3. 55-Second CacheService Layer on doGet(e) — serves frequent web/Streamlit
 *    polls from in-memory CacheService without repeated Drive/Gzip reads.
 * 4. 30-Day Cap on COMPLETED_EOD_DAYS (capCompletedEodDays_(map, 30)) — prevents
 *    ScriptProperties 9 KB per-key / 500 KB total storage exhaustion.
 * 5. Raw minute snapshots are retained indefinitely. Capture health is surfaced instead
 *    of silently deleting research evidence.
 * 6. Black-76 Put-Call Parity Forward F = K + exp(r*T)*(C - P) in evaluateForwardTest_.
 * ==============================================================================
 */

const MAX_COMPLETED_EOD_DAYS = 30;
// A zero value means raw minute archives are retained indefinitely.  Do not
// convert this to a truthy fallback: historical snapshots are research evidence.
const SNAPSHOT_RETENTION_DAYS = 0;
const RUNTIME_BUDGET_WINDOW_MS = 24 * 60 * 60 * 1000;
const CAPTURE_SAFE_DEADLINE_MS = 180000;

const CONFIG = {
  timeZone: 'Asia/Kolkata',
  riskFreeRate: 0.066,
  regularStartMins: 555, // 09:15 IST (9 * 60 + 15)
  regularEndMins: 931,  // 15:31 IST (15 * 60 + 31)
  eodStartMins: 18 * 60,         // 18:00 IST (1080)
  eodEndMins: 22 * 60,           // 22:00 IST (1320)
  completedEodMaxDays: 30,
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
 * Legacy retention hook.  Raw minute archives are deliberately never pruned by
 * this project because they are the source evidence for the displayed research.
 */
function pruneOldMinuteSnapshots_(_retentionDays) {
  return { pruned: 0, retention: 'indefinite', disabled: true };
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

const EOD_PENDING_PREFIX_ = 'EOD_PENDING_DAY_';

function loadPendingEod_() {
  const properties = props_();
  const all = properties.getProperties();
  const pending = readJsonProperty_(properties, 'EOD_PENDING', {});
  Object.keys(all).forEach(key => {
    const match = /^EOD_PENDING_DAY_(\d{4}-\d{2}-\d{2})$/.exec(key);
    if (match) pending[match[1]] = all[key];
  });
  if (Object.prototype.hasOwnProperty.call(all, 'EOD_PENDING')) savePendingEod_(pending);
  return pending;
}

function savePendingEod_(pending) {
  const properties = props_();
  const all = properties.getProperties();
  Object.keys(all).forEach(key => {
    const match = /^EOD_PENDING_DAY_(\d{4}-\d{2}-\d{2})$/.exec(key);
    if (match && !Object.prototype.hasOwnProperty.call(pending, match[1])) {
      properties.deleteProperty(key);
    }
  });
  Object.keys(pending).forEach(day => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !pending[day]) return;
    properties.setProperty(EOD_PENDING_PREFIX_ + day, String(pending[day]));
  });
  properties.deleteProperty('EOD_PENDING');
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
        timeoutSeconds: 60
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

  let calendar = readJsonProperty_(properties, 'MARKET_CALENDAR', null);
  const todayStr = targetDay;
  if (!calendar || calendar.checked !== todayStr) {
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

  if (!calendar || !Array.isArray(calendar.closed)) {
    return { closed: true, special: false, label: 'Market calendar unavailable' };
  }

  if (calendar.closed.includes(targetDay)) {
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
  let customSpecial = {};
  try {
    customSpecial = JSON.parse(properties.getProperty('SPECIAL_SESSIONS_JSON') || '{}');
  } catch (e) {}
  if (customSpecial[today] || SPECIAL_SESSIONS[today]) {
    return false;
  }
  let calendar = readJsonProperty_(properties, 'MARKET_CALENDAR', null);
  if (!calendar || calendar.checked !== today) {
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
  if (!calendar || !Array.isArray(calendar.closed)) {
    throw new Error('Market calendar unavailable; retry next trigger');
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
    if (mode === 'snapshot') {
      if (marketClosed_(now)) throw new Error('NFO session is closed');
      report.nfo_session_validated = true;
    }
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
  // Session gates use the completed capture time; per-instrument event times
  // remain separate and are validated before entry or MTM.
  report.retrieved_at = new Date().toISOString();

  if (mode === 'snapshot' && report.status !== 'complete') {
    // A partial capture may omit an expiry, a leg, or the index quote.  It is
    // retained for diagnostics but cannot create or mark a forward-test trade.
    report.forward_test_status = 'withheld_incomplete_snapshot';
  } else if (mode === 'snapshot' && report.records && report.records.length) {
    try {
      const forwardLedger = evaluateForwardTest_(report);
      if (!forwardLedger) throw new Error('Forward evaluator returned no ledger');
      report.forward_test_status = 'complete';
    } catch (fwdErr) {
      report.forward_test_status = 'failed';
      report.errors.push({ item: 'forward_test', reason: safeError_(fwdErr) });
      report.status = 'partial';
      console.log('Forward test failed; snapshot marked partial: ' + safeError_(fwdErr));
    }
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
    const pending = loadPendingEod_();
    const target = report.target_day || today;
    if (report.status === 'complete') {
      delete pending[target];
      let completedMap = readJsonProperty_(properties, 'COMPLETED_EOD_DAYS', {});
      completedMap[target] = fileId;
      completedMap = capCompletedEodDays_(completedMap, CONFIG.completedEodMaxDays);
      properties.setProperty('COMPLETED_EOD_DAYS', JSON.stringify(completedMap));
    } else if (report.catalogue) {
      pending[target] = fileId;
    }
    savePendingEod_(pending);
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
    .filter(t => ['scheduledCapture', 'scheduledWatchdog', 'weeklyCleanup'].includes(t.getHandlerFunction()))
    .forEach(t => ScriptApp.deleteTrigger(t));

  ScriptApp.newTrigger('scheduledCapture')
    .timeBased()
    .everyMinutes(1)
    .create();

  ScriptApp.newTrigger('scheduledWatchdog')
    .timeBased()
    .everyHours(1)
    .create();

  properties.setProperty('INTERVAL_MINUTES', '1');
  properties.setProperty('SCHEDULE_MODE', 'intraday');

  console.log('Enabled: one-minute snapshots, weekdays 09:15-15:31 IST; EOD retries 18:00-22:00 IST; raw snapshots retained indefinitely; watchdog enabled.');
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

  let customSpecial = {};
  try {
    customSpecial = JSON.parse(properties.getProperty('SPECIAL_SESSIONS_JSON') || '{}');
  } catch (e) {}
  const sp = customSpecial[today] || SPECIAL_SESSIONS[today];

  // Skip weekends (Saturday=6, Sunday=7) before any reconciliation or capture (unless special session)
  if (weekday > 5) {
    if (!sp) return;
  }

  // Pre-market reconciliation & 7-day snapshot pruning window: 07:00 - 09:00 IST (minutes 420 to 540)
  if (mins >= 420 && mins < 540) {
    reconcilePriorSession();
    return;
  }

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

  let budget = readRuntimeBudget_(properties, 'RUNTIME_BUDGET', Date.now());
  if (budget.ms >= 75 * 60000) {
    properties.setProperty('RUNTIME_BUDGET', JSON.stringify(budget));
    properties.setProperty('COLLECTOR_STATUS', 'Rolling 24-hour runtime safety limit reached. ' + today);
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
    let interval = Number(properties.getProperty('INTERVAL_MINUTES') || 1);
    if (interval !== 1) {
      interval = 1;
      properties.setProperty('INTERVAL_MINUTES', '1');
    }
    const slot = today + '|' + Math.floor((mins - snapStart) / interval);

    if (mode === 'snapshot') {
      const previousSlot = String(properties.getProperty('LAST_SNAPSHOT_SLOT') || '');
      const previousParts = previousSlot.split('|');
      const currentSlotNumber = Number(slot.split('|')[1]);
      const previousSlotNumber = Number(previousParts[1]);
      if (previousParts[0] !== today) {
        properties.deleteProperty('SNAPSHOT_GAP');
        properties.deleteProperty('SNAPSHOT_GAP_DAY');
      }
      if (previousParts[0] === today && Number.isFinite(previousSlotNumber)
          && Number.isFinite(currentSlotNumber) && currentSlotNumber > previousSlotNumber + 1) {
        properties.setProperty('SNAPSHOT_GAP', previousSlot + ' to ' + slot + ' detected ' + new Date().toISOString());
        properties.setProperty('SNAPSHOT_GAP_DAY', today);
      }
    }

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

    budget = readRuntimeBudget_(properties, 'RUNTIME_BUDGET', Date.now());
    if (budget.ms >= 75 * 60000) {
      const message = 'Rolling 24-hour runtime safety limit reached.';
      properties.setProperty('COLLECTOR_STATUS', message + ' ' + today);
      return;
    }

    properties.setProperty('ATTEMPT_' + mode, String(now.getTime()));

    const result = capture_(mode);

    if (mode === 'snapshot' && result.status === 'complete') {
      properties.setProperty('LAST_SNAPSHOT_SLOT', slot);
      properties.setProperty('LAST_SNAPSHOT_AT_MS', String(Date.now()));
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
    const ended = Date.now();
    const b = readRuntimeBudget_(properties, 'RUNTIME_BUDGET', ended);
    b.ms += ended - started;
    properties.setProperty('RUNTIME_BUDGET', JSON.stringify(b));
    lock.releaseLock();
  }
}

/**
 * Reconcile prior-session partial EOD captures between 07:00 and 09:00 IST.
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
    // Deliberately a no-op: raw minute archives are retained indefinitely.
    attempted = true;
    try { pruneOldMinuteSnapshots_(CONFIG.snapshotRetentionDays); } catch (pruneErr) {}
    if (Date.now() - started > 60000) return;

    let pending = loadPendingEod_();
    let completedMap = capCompletedEodDays_(
      readJsonProperty_(properties, 'COMPLETED_EOD_DAYS', {}),
      CONFIG.completedEodMaxDays
    );

    if (!properties.getProperty('RECONCILIATION_MIGRATED')) {
      const last = readJsonProperty_(properties, 'LAST_EOD', null);
      if (last && last.status !== 'complete' && last.file_id) {
        pending[last.day] = last.file_id;
      }
      savePendingEod_(pending);
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

    let budget = readRuntimeBudget_(properties, 'RUNTIME_BUDGET', now.getTime());
    let reconciliationBudget = readRuntimeBudget_(properties, 'RECONCILIATION_BUDGET', now.getTime());

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
    if (Date.now() - started > 60000) return;

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
        const ended = Date.now();
        const b = readRuntimeBudget_(properties, key, ended);
        b.ms += ended - started;
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
    last_snapshot: readJsonProperty_(properties, 'LAST_SNAPSHOT', null),
    last_eod: readJsonProperty_(properties, 'LAST_EOD', null)
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
        budget: properties.getProperty('RUNTIME_BUDGET'),
        last_snapshot_at_ms: properties.getProperty('LAST_SNAPSHOT_AT_MS'),
        snapshot_gap: properties.getProperty('SNAPSHOT_GAP'),
        raw_snapshot_retention: 'indefinite'
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
      // A lost file-id must not make recovery dependent on Drive's arbitrary
      // iterator order or a small prefix of an indefinitely retained archive.
      const page = listDriveFilesPage_(folder_().getId(), '', 'modifiedTime desc');
      const latest = (page.files || []).find(f =>
        /^\d{4}-\d{2}-\d{2}_snapshot_\d{6}_complete\.json\.gz$/.test(f.name)
      );
      if (latest && latest.id) file = DriveApp.getFileById(latest.id);
    }

    if (!file) {
      return ContentService.createTextOutput(JSON.stringify({ error: 'No snapshot files found' }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    const uncompressed = Utilities.ungzip(file.getBlob());
    const payload = JSON.parse(uncompressed.getDataAsString());
    if (payload.schema !== 1 || payload.provider !== 'Upstox' || payload.mode !== 'snapshot'
        || payload.status !== 'complete' || payload.nfo_session_validated !== true
        || !Array.isArray(payload.records) || !payload.records.length) {
      throw new Error('Latest archived snapshot is incomplete or invalid');
    }
    const capturedAt = timestampMillis_(payload.retrieved_at);
    payload.capture_integrity = {
      status: properties.getProperty('COLLECTOR_STATUS'),
      captured_at: payload.retrieved_at || null,
      age_seconds: Number.isFinite(capturedAt) ? Math.max(0, (Date.now() - capturedAt) / 1000) : null,
      snapshot_gap: properties.getProperty('SNAPSHOT_GAP_DAY') === String(payload.day || '').slice(0, 10)
        ? (properties.getProperty('SNAPSHOT_GAP') || 'Detected snapshot gap')
        : null,
      raw_snapshot_retention: 'indefinite'
    };
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
  // NSE equity-options transaction charge plus IPFT effective 2026-03-01.
  const exch = turnover * 0.0003553;
  const sebi = turnover * 0.000001;
  const ipft = turnover * 0.000005;
  const stamp = isSell ? 0.0 : turnover * 0.00003;
  const gst = 0.18 * (brokerage + exch + ipft);
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
const MAX_FORWARD_QUOTE_AGE_MS = 120000;

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

function forwardQuoteTimestamp_(marketData) {
  if (!marketData || typeof marketData !== 'object') return null;
  for (const key of ['quote_timestamp', 'feed_timestamp', 'currentTs', 'ts']) {
    if (marketData[key] !== undefined && marketData[key] !== null) return marketData[key];
  }
  return null;
}

function forwardIvPercent_(value) {
  const iv = Number(value);
  if (!Number.isFinite(iv) || iv <= 0 || iv > 500) return NaN;
  if (iv <= 1) return iv * 100;
  if (iv < 5) return NaN; // Unit cannot be inferred safely in this interval.
  return iv;
}

function forwardQuoteFresh_(timestamp, referenceTimestamp) {
  const observed = timestampMillis_(timestamp);
  const reference = referenceTimestamp === undefined ? Date.now() : timestampMillis_(referenceTimestamp);
  if (!Number.isFinite(observed) || !Number.isFinite(reference)) return false;
  const age = reference - observed;
  return age >= -30000 && age <= MAX_FORWARD_QUOTE_AGE_MS;
}

function executableQuote_(node, type, side, quantity, isExit, referenceTimestamp) {
  if (!node) return 0;
  const prefix = type === 'CE' ? 'c' : 'p';
  if (!forwardQuoteFresh_(node[prefix + 'QuoteAt'], referenceTimestamp)) return 0;
  const bid = Number(node[prefix + 'Bid'] || 0);
  const ask = Number(node[prefix + 'Ask'] || 0);
  const bidQty = Number(node[prefix + 'BidQty'] || 0);
  const askQty = Number(node[prefix + 'AskQty'] || 0);
  if (![bid, ask, bidQty, askQty].every(Number.isFinite)) return 0;
  if (!(bid > 0 && ask >= bid && ask > 0)) return 0;
  const buy = isExit ? Number(side) < 0 : Number(side) > 0;
  const depth = buy ? askQty : bidQty;
  if (!Number.isFinite(depth) || depth < quantity) return 0;
  return buy ? ask : bid;
}

const FORWARD_SESSION_WINDOWS_ = {};

function getNseMarketStatusIST(nowOpt) {
  const d = nowOpt instanceof Date ? nowOpt : (nowOpt ? new Date(nowOpt) : new Date());
  const isoDay = day_(d);
  const mins = minute_(d);
  const hmLabel = Utilities.formatDate(d, CONFIG.timeZone, 'HH:mm');
  const timeIstLabel = Utilities.formatDate(d, CONFIG.timeZone, 'dd MMM HH:mm') + ' IST';
  let session = FORWARD_SESSION_WINDOWS_[isoDay];
  if (!session || session.label === 'Market calendar unavailable') {
    session = marketWindow_(d);
    FORWARD_SESSION_WINDOWS_[isoDay] = session;
  }
  const isOpen = !session.closed && mins >= session.startMins && mins <= session.endMins;
  return {
    open: isOpen, isOpen: isOpen, isoDay: isoDay, mins: mins,
    hmLabel: hmLabel, timeIstLabel: timeIstLabel,
    reason: isOpen ? (session.label || 'OPEN') : (session.label || 'CLOSED')
  };
}

function isValidForwardRecord_(rec) {
  if (!rec || typeof rec !== 'object') return false;
  const sDate = String(rec.session_date || rec.date || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(sDate) || sDate < MIN_FORWARD_DATE) return false;
  const trig = String(rec.triggerTime || rec.trigTime || rec.entryTime || '');
  if (/^\d{4}-\d{2}-\d{2}T/.test(trig) && !/(Z|[+-]\d{2}:\d{2})$/i.test(trig)) return false;
  const parsed = timestampMillis_(trig);
  let triggerDate = '';
  let triggerMins = NaN;
  if (Number.isFinite(parsed)) {
    const date = new Date(parsed);
    triggerDate = day_(date);
    triggerMins = minute_(date);
  } else {
    const hm = trig.match(/(?:^|\D)(\d{1,2}):(\d{2})(?:\s*IST)?\b/i);
    if (!hm) return false;
    triggerDate = sDate;
    triggerMins = Number(hm[1]) * 60 + Number(hm[2]);
    const monthDay = trig.match(/\b(\d{1,2})\s+([A-Za-z]{3})\b/i);
    if (monthDay) {
      const parsedDay = new Date(Date.parse(monthDay[1] + ' ' + monthDay[2] + ' ' + sDate.slice(0, 4) + ' 00:00:00 UTC'));
      if (!Number.isFinite(parsedDay.getTime()) || day_(parsedDay) !== sDate) return false;
    }
  }
  return triggerDate === sDate && triggerMins >= 560 && triggerMins < 915;
}

function inScopeForwardRecord_(rec) {
  const sDate = String((rec && (rec.session_date || rec.date)) || '').slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(sDate) && sDate >= MIN_FORWARD_DATE;
}

function retainForwardRecords_(items, isOpenPosition) {
  if (!Array.isArray(items)) return [];
  return items.filter(inScopeForwardRecord_).map(item => {
    const saved = JSON.parse(JSON.stringify(item));
    if (!isValidForwardRecord_(saved)) {
      saved.integrity_status = 'UNVERIFIED_RECORD';
      saved.freshness = 'STALE';
      saved.staleReason = 'Forward record lacks a verifiable in-session trigger timestamp; automatic marking and exit are withheld.';
      if (isOpenPosition) saved.manual_reconciliation_required = true;
    }
    return saved;
  });
}

function cleanSlateForwardLedger_() {
  return {
    schema: 2,
    version: 'forward-test-v20.1-event-time-gated',
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
  base.open_positions = retainForwardRecords_(raw.open_positions, true);
  base.closed_trades = retainForwardRecords_(raw.closed_trades, false);
  base.intraday_mtm_series = Array.isArray(raw.intraday_mtm_series)
    ? raw.intraday_mtm_series.filter(pt => pt && String(pt.session_date || pt.date || MIN_FORWARD_DATE).slice(0, 10) >= MIN_FORWARD_DATE)
    : [];
  if (raw.updated_at) base.updated_at = raw.updated_at;
  base.status = base.open_positions.some(pos => pos.manual_reconciliation_required)
    ? 'INTEGRITY_REVIEW_REQUIRED'
    : ((base.open_positions.length || base.closed_trades.length)
      ? (raw.status || 'ACTIVE_FORWARD_TESTING')
      : 'ARMED_WAITING_FOR_NEXT_SESSION');
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

function forwardRiskMetrics_(report, sym, snapshotDay) {
  const all = report && report.risk_metrics;
  const item = all && all[sym];
  if (!item || typeof item !== 'object') return { hv10: NaN, hv30: NaN, item: null };
  const asOf = String(item.as_of || '').slice(0, 10);
  const asOfMs = Date.parse(asOf + 'T00:00:00Z');
  const sessionMs = Date.parse(snapshotDay + 'T00:00:00Z');
  const ageDays = (sessionMs - asOfMs) / 86400000;
  if (!Number.isFinite(ageDays) || ageDays < 0 || ageDays > 5) {
    return { hv10: NaN, hv30: NaN, item: null };
  }
  return {
    hv10: forwardIvPercent_(item.hv10),
    hv30: forwardIvPercent_(item.hv30),
    item: item
  };
}

/**
 * Evaluates all active expiries against S1–S6 during capture_('snapshot'),
 * using Black-76 Put-Call Parity Forward F = K + exp(r*T)*(C - P) and
 * strictly enforcing MIN_FORWARD_DATE = '2026-11-11' AND live market hours (09:15-15:31 IST).
 */
function evaluateForwardTest_(report) {
  if (!report || !Array.isArray(report.records)) return null;
  if (report.status !== 'complete') throw new Error('Forward test requires a complete snapshot');
  if (report.nfo_session_validated !== true) throw new Error('NFO trading session was not validated');
  const snapDate = report.retrieved_at ? new Date(report.retrieved_at) : new Date();
  if (!Number.isFinite(snapDate.getTime())) throw new Error('Invalid snapshot timestamp');
  const mkt = getNseMarketStatusIST(snapDate);
  const snapshotDay = String(report.day || mkt.isoDay).slice(0, 10);
  if (snapshotDay !== mkt.isoDay) throw new Error('Snapshot day does not match its timestamp');
  const mins = mkt.mins;
  const timeIstLabel = mkt.timeIstLabel;
  const hmLabel = mkt.hmLabel;

  const ledger = loadForwardLedger_();

  if (snapshotDay < MIN_FORWARD_DATE || !mkt.open) {
    report.forward_test_ledger = ledger;
    return ledger;
  }

  if (!report.records.length) {
    ledger.open_positions.forEach(pos => {
      pos.freshness = 'STALE';
      pos.staleReason = 'No live option-chain records in snapshot';
      if (mins >= 915) pos.exit_pending = true;
    });
    ledger.intraday_mtm_series = (Array.isArray(ledger.intraday_mtm_series) ? ledger.intraday_mtm_series : [])
      .filter(point => !(String(point.session_date || point.date || '').slice(0, 10) === snapshotDay && String(point.t || '') === hmLabel));
    ledger.intraday_mtm_series.push({
      session_date: snapshotDay,
      t: hmLabel,
      time_ist: timeIstLabel,
      gross_mtm: null,
      net_mtm: null,
      open_count: ledger.open_positions.length,
      closed_count: ledger.closed_trades.filter(trade => String(trade.session_date || trade.date || '').slice(0, 10) === snapshotDay).length,
      integrity_status: 'WITHHELD_NO_LIVE_OPTION_CHAIN'
    });
    ledger.updated_at = snapDate.toISOString();
    saveForwardLedger_(ledger);
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
        cLtp: Number(cm.ltp || 0),
        pLtp: Number(pm.ltp || 0),
        cBid: Number(cm.bid_price || 0),
        cAsk: Number(cm.ask_price || 0),
        cBidQty: Number(cm.bid_qty || 0),
        cAskQty: Number(cm.ask_qty || 0),
        pBid: Number(pm.bid_price || 0),
        pAsk: Number(pm.ask_price || 0),
        pBidQty: Number(pm.bid_qty || 0),
        pAskQty: Number(pm.ask_qty || 0),
        cIv: forwardIvPercent_(cg.iv),
        pIv: forwardIvPercent_(pg.iv),
        cQuoteAt: forwardQuoteTimestamp_(cm),
        pQuoteAt: forwardQuoteTimestamp_(pm)
      };
    });
    const recTime = rec.retrieved_at || null;
    chainMap[rec.label] = {
      spot: spot,
      strikes: strikes,
      retrievedAt: recTime,
      mkt: recTime ? getNseMarketStatusIST(new Date(recTime)) : null,
      fresh: Boolean(recTime) && forwardQuoteFresh_(recTime)
    };
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
      const chainMkt = ch && ch.mkt;
      if (!ch || !Number.isFinite(ch.spot) || ch.spot <= 0 || !ch.fresh || !chainMkt
          || chainMkt.isoDay !== snapshotDay || !chainMkt.open
          || chainMkt.mins < 560 || chainMkt.mins >= 915) return;
      const step = sym === 'NIFTY' ? 50 : 100;
      const lotQty = sym === 'NIFTY' ? 65 : 30;
      const K = Math.round(ch.spot / step) * step;
      const atmNode = ch.strikes[K];
      if (!atmNode || !(Number.isFinite(atmNode.cIv) && atmNode.cIv > 0)
          || !(Number.isFinite(atmNode.pIv) && atmNode.pIv > 0)
          || !forwardQuoteFresh_(atmNode.cQuoteAt, ch.retrievedAt)
          || !forwardQuoteFresh_(atmNode.pQuoteAt, ch.retrievedAt)) return;

      const expiryMs = Date.parse(exp + 'T15:30:00+05:30');
      const secondsToExpiry = (expiryMs - new Date(ch.retrievedAt).getTime()) / 1000.0;
      if (!Number.isFinite(secondsToExpiry) || secondsToExpiry <= 0) return;
      const dte = Math.ceil(secondsToExpiry / 86400.0);
      const years = secondsToExpiry / (365.0 * 86400.0);
      const iv = Number((0.5 * (atmNode.cIv + atmNode.pIv)).toFixed(2));
      const riskMetrics = forwardRiskMetrics_(report, sym, snapshotDay);
      const hv10 = riskMetrics.hv10;
      const hv30 = riskMetrics.hv30;
      const hv10Valid = Number.isFinite(hv10) && hv10 > 0;
      const hv30Valid = Number.isFinite(hv30) && hv30 > 0;
      const vrp30 = hv30Valid ? Number((iv - hv30).toFixed(2)) : NaN;
      const vrp10 = hv10Valid ? Number((iv - hv10).toFixed(2)) : NaN;
      const wShort = sym === 'NIFTY' ? 200 : 400;
      const p25Node = ch.strikes[K - wShort];
      const c25Node = ch.strikes[K + wShort];
      const wingsFresh = Boolean(p25Node && c25Node
        && Number.isFinite(p25Node.pIv) && p25Node.pIv > 0
        && Number.isFinite(c25Node.cIv) && c25Node.cIv > 0
        && forwardQuoteFresh_(p25Node.pQuoteAt, ch.retrievedAt)
        && forwardQuoteFresh_(c25Node.cQuoteAt, ch.retrievedAt));
      const rev25 = wingsFresh ? Number((p25Node.pIv - c25Node.cIv).toFixed(2)) : NaN;
      const bfly25 = wingsFresh ? Number((0.5 * (p25Node.pIv + c25Node.cIv) - iv).toFixed(2)) : NaN;
      const metricsItem = riskMetrics.item;
      const basisExpiry = metricsItem ? String(metricsItem.forward_basis_expiry || '').slice(0, 10) : '';
      const basisTime = metricsItem ? metricsItem.forward_basis_timestamp : null;
      const basis = metricsItem && basisExpiry === exp && forwardQuoteFresh_(basisTime, ch.retrievedAt)
        ? Number(metricsItem.forward_basis) : NaN;
      const basisValid = Number.isFinite(basis);

      const trigs = [
        ['S1', hv30Valid && vrp30 >= 1.50 && hv30 <= 16.5 && dte >= 2 && dte <= 35, hv30Valid ? 'IV ' + iv + '% · HV30 ' + hv30 + '% · VRP ' + (vrp30 >= 0 ? '+' : '') + vrp30 + ' pp' : 'HV30 unavailable'],
        ['S2', hv10Valid && hv30Valid && (vrp30 <= -0.25 || iv < hv10), hv10Valid && hv30Valid ? 'IV ' + iv + '% · HV10 ' + hv10 + '% · IV−HV10 ' + (vrp10 >= 0 ? '+' : '') + vrp10 + ' pp' : 'IV/HV inputs unavailable'],
        ['S3', hv30Valid && iv >= 15.2 && vrp30 >= 1.0 && dte <= 22, hv30Valid ? 'ATM IV ' + iv + '% · VRP ' + (vrp30 >= 0 ? '+' : '') + vrp30 + ' pp · DTE ' + dte + 'd' : 'HV30 unavailable'],
        ['S4', wingsFresh && rev25 >= 1.50 && dte >= 5, wingsFresh ? 'RR25 ' + (rev25 >= 0 ? '+' : '') + rev25 + ' pp · BF25 ' + (bfly25 >= 0 ? '+' : '') + bfly25 + ' pp' : '25-delta wing IV unavailable'],
        ['S5', hv10Valid && vrp10 >= 0.80 && dte <= 16, hv10Valid ? 'IV−HV10 ' + (vrp10 >= 0 ? '+' : '') + vrp10 + ' pp · HV10 ' + hv10 + '% · DTE ' + dte + 'd' : 'HV10 unavailable'],
        ['S6', (basisValid && Math.abs(basis) >= (sym === 'NIFTY' ? 10.0 : 35.0)) || (wingsFresh && Math.abs(bfly25) >= 0.25), basisValid ? 'Forward basis ' + (basis >= 0 ? '+' : '') + basis + ' pts · BF25 ' + (Number.isFinite(bfly25) ? (bfly25 >= 0 ? '+' : '') + bfly25 : 'unavailable') + ' pp' : (wingsFresh ? 'BF25 ' + (bfly25 >= 0 ? '+' : '') + bfly25 + ' pp' : 'Validated forward basis and wing IV unavailable')]
      ];

      trigs.forEach(item => {
        const sCode = item[0], isTrig = item[1], metricsTxt = item[2];
        if (!isTrig || existingKeys.has(sym + '|' + exp + '|' + sCode)) return;
        const legs = buildStrategyLegs_(sym, K, sCode);
        let invalidQuote = false;
        legs.forEach(l => {
          const node = ch.strikes[l.strike];
          const px = executableQuote_(node, l.type, l.side, lotQty * Number(l.mult || 1), false, ch.retrievedAt);
          if (!(Number.isFinite(px) && px > 0)) {
            invalidQuote = true;
            return;
          }
          l.entryPx = Number(px.toFixed(2));
          l.curPx = Number(px.toFixed(2));
        });
        if (invalidQuote) return;
        const cap = (STRATEGY_CAPITAL_PER_LOT[sym] || STRATEGY_CAPITAL_PER_LOT.NIFTY)[sCode];
        const credPts = Math.max(15.0, legs.reduce((acc, l) => acc + (-l.side) * l.mult * l.entryPx, 0));
        const debitInr = Math.round(legs.reduce((acc, l) => acc + l.entryPx * lotQty * l.mult, 0));
        const maxCr = sCode === 'S2' ? Math.round(debitInr * 0.65) : Math.round(credPts * lotQty);
        const maxLoss = sCode === 'S2' ? debitInr : Math.round((sym === 'NIFTY' ? 120 : 300) * lotQty);
        const chg = positionCharges_(legs, sym, true);

        ledger.open_positions.push({
          id: 'OPEN-' + snapshotDay.replace(/-/g, '') + '-' + sym.slice(0, 3) + '-' + exp.replace(/-/g, '').slice(4) + '-' + sCode,
          date: snapshotDay,
          session_date: snapshotDay,
          triggerTime: snapshotDay + 'T' + chainMkt.hmLabel + ':00+05:30',
          trigTime: chainMkt.timeIstLabel,
          markedAt: chainMkt.timeIstLabel,
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
    if (!isValidForwardRecord_(pos)) {
      pos.freshness = 'STALE';
      pos.integrity_status = 'UNVERIFIED_RECORD';
      pos.manual_reconciliation_required = true;
      pos.staleReason = 'Forward record lacks a verifiable in-session trigger timestamp; automatic marking and exit are withheld.';
      stillOpen.push(pos);
      return;
    }
    const key = pos.sym + '|' + pos.exp;
    const ch = chainMap[key];
    const positionMkt = ch && ch.mkt ? ch.mkt : mkt;
    const lotQty = pos.sym === 'NIFTY' ? 65 : 30;
    const legs = Array.isArray(pos.legs) ? pos.legs : [];
    let allLegsFresh = Boolean(ch && ch.fresh && legs.length);
    let gross = 0.0;
    legs.forEach(l => {
      const node = ch && ch.strikes ? ch.strikes[l.strike] : null;
      const qty = lotQty * Number(l.mult || 1);
      const px = executableQuote_(node, l.type, l.side, qty, true, ch && ch.retrievedAt);
      if (!(Number.isFinite(px) && px > 0)) {
        allLegsFresh = false;
        return;
      }
      l.curPx = Number(px.toFixed(2));
      const ePx = Number(l.entryPx || 0);
      gross += (Number(l.side) < 0 ? (ePx - px) : (px - ePx)) * qty;
    });

    if (!allLegsFresh) {
      pos.freshness = 'STALE';
      pos.staleReason = !ch || !ch.fresh ? 'Missing or stale option-chain timestamp' : 'Missing or insufficient executable bid/ask depth';
      if (positionMkt.mins >= 915 || pos.exit_pending) pos.exit_pending = true;
      stillOpen.push(pos);
      return;
    }

    pos.freshness = 'FRESH';
    delete pos.staleReason;
    pos.integrity_status = 'VALIDATED_FRESH_EXECUTABLE_MARK';
    pos.grossMtm = Number(gross.toFixed(2));
    pos.estCharges = positionCharges_(pos.legs, pos.sym, true);
    pos.netMtm = Number((pos.grossMtm - pos.estCharges).toFixed(2));
    pos.markedAt = positionMkt.timeIstLabel;

    const slThresh = -0.50 * Number(pos.maxLoss || 9000);
    const tpThresh = pos.strat === 'S2' ? 0.30 * Number(pos.maxLoss || 37590) : 0.50 * Number(pos.maxCredit || 12000);
    const isIntradayExit = positionMkt.mins >= 915 || Boolean(pos.exit_pending);

    if (pos.grossMtm <= slThresh || pos.grossMtm >= tpThresh || isIntradayExit) {
      const reason = pos.grossMtm <= slThresh
        ? 'Stop-Loss Hit (-50% MaxLoss)'
        : (pos.grossMtm >= tpThresh ? 'Profit Target Hit (+50% Cr)' : (pos.exit_pending ? '15:15 IST Auto-Close (delayed until fresh quote)' : '15:15 IST Auto-Close (fresh quote)'));
      (pos.legs || []).forEach(l => { l.exitPx = l.curPx; });
      ledger.closed_trades.unshift({
        id: String(pos.id || '').replace('OPEN-', 'FWD-'),
        date: pos.date || snapshotDay,
        session_date: pos.session_date || pos.date || snapshotDay,
        triggerTime: pos.triggerTime || (snapshotDay + 'T09:20:00+05:30'),
        entryTime: pos.trigTime || '09:20 IST',
        exitTime: positionMkt.hmLabel + ' IST',
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
  // Do not carry a prior aggregate MTM forward across a missing or
  // unverified position mark.  The series explicitly withholds the point so
  // display consumers cannot mistake an old total for a current valuation.
  const sessionClosed = ledger.closed_trades.filter(trade =>
    String(trade.session_date || trade.date || '').slice(0, 10) === snapshotDay
  );
  const marksWithheld = stillOpen.some(pos =>
    String(pos.freshness || '').toUpperCase() !== 'FRESH'
    || String(pos.integrity_status || '') === 'UNVERIFIED_RECORD'
    || Boolean(pos.manual_reconciliation_required)
  );
  const mtmPoint = {
    session_date: snapshotDay,
    t: hmLabel,
    time_ist: timeIstLabel,
    open_count: stillOpen.length,
    closed_count: sessionClosed.length
  };
  if (marksWithheld) {
    mtmPoint.gross_mtm = null;
    mtmPoint.net_mtm = null;
    mtmPoint.integrity_status = 'WITHHELD_STALE_OR_UNVERIFIED_POSITION';
  } else {
    mtmPoint.gross_mtm = Number((
      stillOpen.reduce((sum, pos) => sum + Number(pos.grossMtm || 0), 0)
      + sessionClosed.reduce((sum, trade) => sum + Number(trade.gross || 0), 0)
    ).toFixed(2));
    mtmPoint.net_mtm = Number((
      stillOpen.reduce((sum, pos) => sum + Number(pos.netMtm || 0), 0)
      + sessionClosed.reduce((sum, trade) => sum + Number(trade.net || 0), 0)
    ).toFixed(2));
    mtmPoint.integrity_status = 'VALIDATED_FRESH_EXECUTABLE_MARKS';
  }
  ledger.intraday_mtm_series = (Array.isArray(ledger.intraday_mtm_series) ? ledger.intraday_mtm_series : [])
    .filter(point => !(String(point.session_date || point.date || '').slice(0, 10) === snapshotDay && String(point.t || '') === hmLabel));
  ledger.intraday_mtm_series.push(mtmPoint);
  ledger.status = stillOpen.some(pos => pos.manual_reconciliation_required)
    ? 'INTEGRITY_REVIEW_REQUIRED'
    : (stillOpen.length ? 'ACTIVE_LIVE_SESSION' : (ledger.closed_trades.length ? 'SESSION_COMPLETED_1515_IST' : 'ARMED_WAITING_FOR_NEXT_SESSION'));

  saveForwardLedger_(ledger);
  report.forward_test_ledger = ledger;
  return ledger;
}
