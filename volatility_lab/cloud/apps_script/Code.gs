/** One-minute Upstox market data collector. Stores compressed raw captures and daily
 * candles with a bounded runtime budget. The public desk reads recorded data.
 * Pre-market EOD reconciliation retries candles published after the evening cutoff.
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

function runtimePolicy_(properties) {
  const workspace = properties.getProperty('COLLECTOR_QUOTA_TIER') === 'verified_workspace';
  const maximum = workspace ? 355 : 85;
  const requested = Number(properties.getProperty('COLLECTOR_RUNTIME_MINUTES'));
  // A smaller explicit budget is valid even when all of it is reserved for EOD.
  const total = Number.isInteger(requested) && requested > 0 ? Math.min(requested, maximum) : maximum;
  const reserve = Math.min(5, total);
  return { tier: workspace ? 'operator_verified_workspace' : 'consumer_default',
    total_minutes: total, snapshot_minutes: total - reserve, eod_reserve_minutes: reserve };
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
  return { pruned: 0, retention: 'indefinite', disabled: true,
    reason: 'EOD and historical-candle bundles omit option books and Greeks; full-fidelity consolidation is unverified' };
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
  const kept = capCompletedEodDays_(pending, CONFIG.completedEodMaxDays);
  const overflow = Object.keys(pending).filter(day => !Object.prototype.hasOwnProperty.call(kept, day));
  // Preserve unresolved old file IDs before retiring property-index entries.
  if (overflow.length) {
    save_('eod_pending_overflow_' + new Date().toISOString().replace(/[:.]/g, '-'),
      {schema:1, type:'eod_pending_archive_index', days:Object.fromEntries(overflow.map(d => [d, pending[d]]))});
  }
  Object.keys(all).forEach(key => {
    const match = /^EOD_PENDING_DAY_(\d{4}-\d{2}-\d{2})$/.exec(key);
    if (match && !Object.prototype.hasOwnProperty.call(kept, match[1])) {
      properties.deleteProperty(key);
    }
  });
  Object.keys(kept).forEach(day => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !kept[day]) return;
    const value = String(kept[day]);
    if (!/^[\w-]{1,256}$/.test(value)) throw new Error('Invalid pending archive file ID');
    if (all[EOD_PENDING_PREFIX_ + day] !== value) properties.setProperty(EOD_PENDING_PREFIX_ + day, value);
  });
  properties.deleteProperty('EOD_PENDING');
}

/** Read-only view of queued EOD days, so recovery state is visible without the editor. */
function pendingEodDays_() {
  const properties = props_();
  const all = properties.getProperties();
  const days = Object.keys(readJsonProperty_(properties, 'EOD_PENDING', {}));
  Object.keys(all).forEach(key => {
    const match = /^EOD_PENDING_DAY_(\d{4}-\d{2}-\d{2})$/.exec(key);
    if (match) days.push(match[1]);
  });
  return Array.from(new Set(days)).sort();
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

// Full quotes supply feed timestamps and executable depth absent from option/chain.
// Never substitute the HTTP retrieval time or last-trade time for the feed time.
function snapshotRiskMetrics_(candles, sessionDay, instrumentKey, includeSession) {
  if (!Array.isArray(candles)) throw new Error('Missing daily candles');
  const closes = new Map();
  candles.forEach(c => {
    const date = String(c && c[0] || '').slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error('Invalid daily candle date');
    if (date > sessionDay || (date === sessionDay && !includeSession)) return;
    const close = Number(c[4]);
    if (!Number.isFinite(close) || close <= 0 || closes.has(date)) throw new Error('Invalid or duplicate daily close');
    closes.set(date, close);
  });
  const days = Array.from(closes.keys()).sort();
  if (days.length < 31) throw new Error('Need 31 completed daily closes for HV30');
  const asOf = days[days.length - 1];
  const age = (Date.parse(sessionDay) - Date.parse(asOf)) / 86400000;
  if (includeSession ? asOf !== sessionDay : age <= 0 || age > 5) throw new Error('Historical volatility inputs are stale');
  const returns = days.slice(1).map((d, i) => Math.log(closes.get(d) / closes.get(days[i])));
  const hv = n => {
    const values = returns.slice(-n);
    const mean = values.reduce((a, b) => a + b, 0) / n;
    return Math.sqrt(values.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / (n - 1) * 245) * 100;
  };
  const variance = returns.reduce((v, r, i) => i ? 0.94 * v + 0.06 * r * r : r * r, 0);
  return { as_of: asOf, hv10: hv(10), hv20: hv(20), hv30: hv(30), ewma: Math.sqrt(variance * 245) * 100,
    units: 'annualized_percent', annualization_sessions: 245, method: 'sample_std_log_close_returns',
    source: 'Upstox historical-candle v3 days/1', instrument_key: instrumentKey, observations: days.length, close:closes.get(asOf) };
}

// Snapshot callers hold the capture lock. Keep one bounded map for the session.
function closingRiskRows_(report, day) {
  const rows = {};
  for (const [symbol, key] of Object.entries(CONFIG.indices)) {
    const record = report.records.find(r => r.label === key);
    if (!record) continue;
    try {
      const {close,hv10,hv20,hv30,ewma} = snapshotRiskMetrics_(record.data.candles,day,key,true);
      rows[symbol] = [close,hv10,hv20,hv30,ewma];
    } catch (err) {
      if (!report.pending.includes(key)) report.pending.push(key);
    }
  }
  return rows;
}

function saveClosingRisk_(properties, day, rows) {
  if (!Object.keys(rows).length) return;
  const prefix = 'CLOSING_HV_', key = prefix + day.slice(0,7);
  const days = readJsonProperty_(properties,key,{});
  days[day] = Object.assign({},days[day],rows);
  properties.setProperty(key, JSON.stringify(days));
  // Retain the same one-year horizon as the captured IV index.
  const cutoff = dailyIndexCutoff_(day).slice(0,7);
  for (const other of properties.getKeys()) {
    if (other.startsWith(prefix) && other.slice(prefix.length) < cutoff) properties.deleteProperty(other);
  }
}

function publishedClosingRisk_(values) {
  const days = {};
  for (const [key, value] of Object.entries(values)) {
    if (!key.startsWith('CLOSING_HV_')) continue;
    try { Object.assign(days,JSON.parse(value)); } catch (err) {}
  }
  return days;
}

function dailyIvRow_(report, symbol, archiveFile) {
  if (report.status !== 'complete' || !report.nfo_session_validated) return null;
  const first = report.records.filter(r => r.label.startsWith(symbol + '|') &&
    /^\d{4}-\d{2}-\d{2}$/.test(r.label.slice(symbol.length+1)) && r.label.slice(symbol.length+1) > report.day)
    .sort((a,b) => a.label.localeCompare(b.label))[0];
  if (!first || !Array.isArray(first.data)) return null;
  const stamp = first.chain_retrieved_at || first.retrieved_at;
  const time = timestampMillis_(stamp);
  if (!Number.isFinite(time) || day_(new Date(time)) !== report.day || !captureQuoteFresh_(time,report.retrieved_at)) return null;
  const spot = Number(first.data[0]?.underlying_spot_price);
  if (!Number.isFinite(spot) || !(spot > 0)) return null;
  let nearest = null;
  for (const row of first.data) {
    const strike = Number(row.strike_price);
    if (!(strike > 0) || !Number.isFinite(strike)) continue;
    const distance = Math.abs(strike - spot);
    if (!nearest || distance < nearest.distance || (distance === nearest.distance && strike < nearest.strike)) {
      nearest = {strike,distance,row};
    }
  }
  if (!nearest) return null;
  const legs = [nearest.row.call_options,nearest.row.put_options];
  const ivs = legs.map(leg => Number(leg?.option_greeks?.iv));
  if (!ivs.every(v => Number.isFinite(v) && v > 0)) return null;
  const risk = report.risk_metrics?.[symbol];
  const metric = key => Number.isFinite(risk?.[key]) ? risk[key] : null;
  return {d:report.day,c:spot,iv:(ivs[0]+ivs[1])/2,expiry:first.label.split('|')[1],atm:nearest.strike,
    snapshotId:new Date(time).toISOString(),ivSource:'Upstox option_greeks.iv',archiveFile,
    h10:metric('hv10'),h20:metric('hv20'),h30:metric('hv30'),ew:metric('ewma'),riskAsOf:risk?.as_of || null};
}

function dailyIndexKey_(symbol, day) {
  return 'DAILY_IV_' + symbol + '_' + day.slice(0,7) + '_' + (Number(day.slice(8)) <= 15 ? 'A' : 'B');
}

function dailyIndexCutoff_(day) {
  return new Date(Date.parse(day + 'T00:00:00Z') - 365 * 86400000).toISOString().slice(0,10);
}

function updateDailyIndex_(properties, report, archiveFile) {
  const last = readJsonProperty_(properties,'LAST_SNAPSHOT',null);
  const prior = last?.daily_rows || {};
  const rows = last?.day === report.day ? Object.assign({},prior) : {};
  if (last?.day && last.day < report.day) {
    const cutoff = dailyIndexCutoff_(report.day);
    // Roll over idempotently before the existing LAST_SNAPSHOT write replaces prior.
    for (const symbol of Object.keys(CONFIG.indices)) {
      const row = prior[symbol];
      if (!row || row.d !== last.day || row.d < cutoff) continue;
      const key = dailyIndexKey_(symbol,row.d);
      const days = readJsonProperty_(properties,key,{});
      days[row.d] = row;
      properties.setProperty(key,JSON.stringify(days));
    }
    for (const key of properties.getKeys()) {
      const match = /^DAILY_IV_(NIFTY|BANKNIFTY)_(\d{4}-\d{2})_([AB])$/.exec(key);
      if (match && match[2] + (match[3] === 'A' ? '-15' : '-31') < cutoff) properties.deleteProperty(key);
    }
  }
  for (const symbol of Object.keys(CONFIG.indices)) {
    const row = dailyIvRow_(report,symbol,archiveFile);
    if (row && (!rows[symbol] || row.snapshotId >= rows[symbol].snapshotId)) rows[symbol] = row;
  }
  return rows;
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

function straddleHistoryTuple_(rec, stamp) {
  const markAt = timestampMillis_(stamp);
  const row = rec.data.find(r => Number(r.strike_price) === rec.straddle_anchor_strike);
  const ivTime = timestampMillis_(rec.chain_retrieved_at || rec.retrieved_at);
  const c = row?.call_options, p = row?.put_options;
  const ivs = [c,p].map(leg => Number(leg?.option_greeks?.iv));
  const iv = captureQuoteFresh_(ivTime, markAt) && ivs.every(v => Number.isFinite(v) && v > 0)
    ? (ivs[0] + ivs[1]) / 2 : null;
  const books = [c,p].map(leg => {
    const md = leg?.market_data;
    if (md?.quote_source !== 'Upstox /v2/market-quote/quotes') return null;
    const time = timestampMillis_(md.quote_timestamp);
    const bid = Number(md.bid_price), ask = Number(md.ask_price);
    if (!captureQuoteFresh_(time, markAt) || ![bid,ask].every(Number.isFinite)
        || !(bid > 0 && ask >= bid && md.bid_qty > 0 && md.ask_qty > 0)) return null;
    return {time, mid:(bid + ask) / 2};
  });
  const matched = books[0] && books[1] && Math.abs(books[0].time - books[1].time) <= 30000;
  return [iv === null ? null : ivTime, markAt, iv, matched ? books[0].mid + books[1].mid : null,
    matched ? books[0].time : null, matched ? books[1].time : null];
}

function attachStraddleHistory_(report, cache) {
  const records = report.records.filter(r => r.straddle_anchor_strike > 0 && Array.isArray(r.data));
  const keys = records.map(r => ['STRADDLE_V1',report.day,r.label,r.straddle_anchor_strike].join(':'));
  let cached = {};
  try { if (keys.length && cache) cached = cache.getAll(keys) || {}; } catch (err) {}
  const updates = {};
  records.forEach((rec,i) => {
    const point = straddleHistoryTuple_(rec,report.retrieved_at);
    let prior;
    try { prior = JSON.parse(cached[keys[i]] || '[]'); } catch (err) { prior = []; }
    const history = Array.isArray(prior) ? prior.filter(p => Array.isArray(p) && p.length === 6
      && Number.isFinite(p[1]) && p[1] < point[1]).slice(-399) : [];
    history.push(point);
    rec.straddle_history = history;
    updates[keys[i]] = JSON.stringify(history);
  });
  return updates;
}

// Expiry-day desk: a rolling spot-ATM straddle and the fixed open-anchor
// straddle, both from paired two-sided books. Nothing is derived from spot
// distance or LTP, so a mark only exists when both legs quoted together.
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

function attachDualStraddleHistory_(report, cache, updates) {
  const records = report.records.filter(r => r.straddle_anchor_strike > 0 && Array.isArray(r.data)
    && /^(NIFTY|BANKNIFTY)\|\d{4}-\d{2}-\d{2}$/.test(r.label));
  const keys = records.map(r => ['DUAL_V1', report.day, r.label].join(':'));
  let cached = {};
  try { if (keys.length && cache) cached = cache.getAll(keys) || {}; } catch (err) {}
  records.forEach((rec, i) => {
    const point = dualStraddlePoint_(rec, report.retrieved_at);
    let prior;
    try { prior = JSON.parse(cached[keys[i]] || '[]'); } catch (err) { prior = []; }
    const cutoff = point ? point.markAt : Infinity;
    const history = Array.isArray(prior) ? prior.filter(p => p && Number.isFinite(p.markAt)
      && p.markAt < cutoff).slice(-399) : [];
    if (point) history.push(point);
    if (!history.length) return;
    rec.dual_straddle = point || history[history.length - 1];
    rec.dual_straddle_history = history;
    updates[keys[i]] = JSON.stringify(history);
  });
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
  report.risk_metrics = {};
  report.input_diagnostics = [];
  const cache = scriptCache_();
  const requests = [];
  const dayMs = Date.parse(report.day + 'T12:00:00+05:30');
  const toDay = day_(new Date(dayMs - 86400000));
  const fromDay = day_(new Date(dayMs - 100 * 86400000));
  Object.keys(CONFIG.indices).forEach(sym => {
    const cacheKey = 'HV_V1_' + report.day + '_' + sym;
    let cached = null;
    try { cached = cache && JSON.parse(cache.get(cacheKey) || 'null'); } catch (e) {}
    if (cached && cached.as_of < report.day && cached.units === 'annualized_percent'
        && Number.isFinite(cached.hv30) && (Date.parse(report.day) - Date.parse(cached.as_of)) <= 5 * 86400000) {
      report.risk_metrics[sym] = cached;
    } else {
      requests.push({ label: 'risk:' + sym, path: '/v3/historical-candle/' + encodeURIComponent(CONFIG.indices[sym])
        + '/days/1/' + toDay + '/' + fromDay });
    }
  });
  const chains = report.records.filter(r => /^(NIFTY|BANKNIFTY)\|\d{4}-\d{2}-\d{2}$/.test(r.label) && Array.isArray(r.data));
  const options = [];
  for (const rec of chains) {
    const selected = fullDepth ? null : quoteStrikes_(rec);
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
    if (Date.now() - started > CAPTURE_SAFE_DEADLINE_MS - 60000) throw new Error('Snapshot enrichment time budget exhausted');
    previousGroupStarted = Date.now();
    apiAll_(requests.slice(offset, offset + 40)).forEach(res => {
      try {
        if (res.error) throw new Error(res.error);
        if (res.label.indexOf('risk:') === 0) {
          const sym = res.label.slice(5);
          const metrics = snapshotRiskMetrics_(res.data.candles, report.day, CONFIG.indices[sym]);
          metrics.retrieved_at = new Date().toISOString();
          report.risk_metrics[sym] = metrics;
          if (cache) { try { cache.put('HV_V1_' + report.day + '_' + sym, JSON.stringify(metrics), 21600); } catch (e) {} }
        } else {
          Object.values(res.data || {}).forEach(q => { if (q && q.instrument_token) quotes.set(q.instrument_token, q); });
        }
      } catch (err) {
        if (res.label.indexOf('risk:') === 0) report.input_diagnostics.push({ item: res.label, reason: String(err.message || err) });
        else report.input_diagnostics.push({ item: res.label, reason: String(err.message || err) });
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
      report.errors.push({ item: rec.label, reason: 'Option-chain Greeks became stale during quote enrichment' });
    }
  });
}

function capture_(mode, recovery) {
  const started = Date.now();
  const now = new Date();
  const today = day_(now);
  const properties = props_();

  const report = {
    schema: 1,
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
    const catalogue = recovery ? recovery.catalogue : catalogue_(now);
    report.catalogue = catalogue;
    const targetDay = recovery ? recovery.target_day : today;

    if (recovery) {
      report.target_day = targetDay;
      report.purpose = 'prior-session reconciliation';
      report.recovery_source_file_id = recovery.source_file_id || null;
    }

    const keys = Object.values(CONFIG.indices).concat(['NSE_INDEX|India VIX']);
    Object.values(catalogue.indices).forEach(idx => {
      idx.futures.forEach(f => keys.push(f.instrument_key));
    });

    const requests = recovery
      ? []
      : [{ label: 'quotes', path: '/v2/market-quote/quotes', params: { instrument_key: keys.join(',') } }];

    if (mode === 'eod') {
      const targetMs = Date.parse(targetDay + 'T12:00:00+05:30');
      keys.forEach(key => {
        const lookback = Object.values(CONFIG.indices).includes(key) ? 100 : 10;
        const fromDay = day_(new Date(targetMs - lookback * 86400000));
        let path = '/v3/historical-candle/' + encodeURIComponent(key) + '/days/1/' + targetDay + '/' + fromDay;
        // Exchange tokens can be reused after expiry. Resolve the original
        // expired contract at the broker rather than sending its live token.
        const symbol = Object.keys(catalogue.indices).find(sym => catalogue.indices[sym].futures.some(f => f.instrument_key === key));
        const future = symbol && catalogue.indices[symbol].futures.find(f => f.instrument_key === key);
        if (future && future.expiry < today) {
          try {
            const contracts = api_('/v2/expired-instruments/future/contract',
              {instrument_key:CONFIG.indices[symbol],expiry_date:future.expiry});
            const contract = Array.isArray(contracts) && contracts.find(f => f.expiry === future.expiry
              && String(f.instrument_key).startsWith(key + '|'));
            if (!contract) throw new Error('Expired contract unavailable');
            path = '/v2/expired-instruments/historical-candle/' + encodeURIComponent(contract.instrument_key)
              + '/day/' + targetDay + '/' + fromDay;
          } catch (err) {
            report.errors.push({item:key,reason:safeError_(err)});
            report.pending.push(key);
            return;
          }
        }
        requests.push({label:key,path:path});
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
    if (mode === 'eod') report.closing_risk = closingRiskRows_(report,targetDay);
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
  const historyCache = mode === 'snapshot' && report.status === 'complete' ? scriptCache_() : null;
  const history = mode === 'snapshot' && report.status === 'complete' ? attachStraddleHistory_(report,historyCache) : {};
  if (historyCache) {
    try { attachDualStraddleHistory_(report, historyCache, history); }
    catch (err) { report.input_diagnostics = (report.input_diagnostics || []).concat([{item:'dual_straddle',reason:safeError_(err)}]); }
  }
  const fileId = save_(fileName, report);
  // Only committed archives can supply observations to the next snapshot.
  if (historyCache && Object.keys(history).length) {
    try { historyCache.putAll(history,21600); } catch (err) {}
  }

  report.archive_seconds = (Date.now() - archiveStarted) / 1000;
  report.file_id = fileId;
  report.total_seconds = (Date.now() - started) / 1000;

  const propertyKey = recovery ? 'LAST_RECONCILIATION' : 'LAST_' + mode.toUpperCase();
  properties.setProperty(propertyKey, JSON.stringify({
    day: today,
    target_day: report.target_day || today,
    daily_rows: mode === 'snapshot' ? updateDailyIndex_(properties,report,fileName + '.json.gz') : undefined,
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

  if (mode === 'eod') {
    const pending = loadPendingEod_();
    const target = report.target_day || today;
    if (report.closing_risk) saveClosingRisk_(properties,target,report.closing_risk);
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
      try { cache.remove('DOGET_CAPTURE_DATA_V1'); } catch (e) {}
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

  ScriptApp.newTrigger('scheduledWatchdog')
    .timeBased()
    .everyHours(1)
    .create();

  properties.setProperty('INTERVAL_MINUTES', '1');
  properties.setProperty('SCHEDULE_MODE', 'intraday');

  console.log('Enabled: one-minute snapshots, weekdays exchange session hours; EOD candle polling five minutes after close through 22:00 IST; raw snapshots retained indefinitely; watchdog enabled.');
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
  if (weekend && !sp) {
    if (mins >= 420 && mins < 540) reconcilePriorSession();
    return;
  }

  // Pre-market reconciliation window: 07:00 - 09:00 IST (minutes 420 to 540).
  // The exchange is closed and no snapshot slot is due, so recovery runs in every
  // capture mode. It keeps its own eight-minute daily budget plus the shared guard,
  // which is what retries an EOD candle the broker only publishes after 22:00.
  if (mins >= 420 && mins < 540) {
    reconcilePriorSession();
    return;
  }

  const snapStart = sp ? sp.startMins : CONFIG.regularStartMins; // 555 (09:15 IST)
  const snapEnd = sp ? sp.endMins : regularCloseMinutes_(today);
  const eodStart = sp ? sp.eodStartMins : snapEnd + 5;
  const eodEnd = sp ? sp.eodEndMins : CONFIG.eodEndMins;         // 1320 (22:00 IST)

  if (mins >= eodStart && mins < eodEnd && mins % 5 !== 0) {
    reconcilePriorSession();
    return;
  }

  // Intraday snapshots: regular or published special session, inclusive
  // EOD candle polling begins five minutes after the regular session closes.
  const mode = (mins >= snapStart && mins <= snapEnd)
    ? 'snapshot'
    : (mins >= eodStart && mins < eodEnd)
      ? 'eod'
      : null;

  // Outside active capture windows: return immediately with ZERO PropertiesService writes
  if (!mode) {
    return;
  }

  const policy = runtimePolicy_(properties);
  // Preserve one bounded capture attempt within the snapshot allowance for the first
  // late-session quote capture. The separate EOD allowance still serves daily candles.
  const lateWindowMinute = Math.max(snapStart, Math.min(915, snapEnd-15));
  const snapshotMs = policy.snapshot_minutes * 60000;
  const reserve = mins < lateWindowMinute ? Math.min(CAPTURE_SAFE_DEADLINE_MS, snapshotMs) : 0;
  const limit = mode === 'eod' ? policy.total_minutes * 60000 : snapshotMs-reserve;
  const pauseMessage = mode + ' runtime admission limit reached (' + limit / 60000 + ' minutes). ' + today;
  let budget = readRuntimeBudget_(properties, 'RUNTIME_BUDGET', Date.now());
  if (budget.ms >= limit) {
    properties.setProperty('COLLECTOR_STATUS', pauseMessage);
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
      if (alreadySucceeded || now.getTime() - lastAttempt < (mins < eodStart+30 ? 5 : 30) * 60000) {
        return;
      }
    }

    budget = readRuntimeBudget_(properties, 'RUNTIME_BUDGET', Date.now());
    if (budget.ms >= limit) {
      properties.setProperty('COLLECTOR_STATUS', pauseMessage);
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
 * Recover one queued EOD capture from the original contracts stored with the partial.
 * Recovery validates the target session rather than today's date.
 */
function recoveryArchive_(targetDay, fileId) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(targetDay)) throw new Error('Invalid session date');
  const read = file => {
    const blob = file.getBlob();
    blob.setContentType('application/x-gzip');
    const report = JSON.parse(Utilities.ungzip(blob).getDataAsString());
    if (!report.catalogue || report.mode !== 'eod' || (report.target_day || report.day) !== targetDay)
      throw new Error('Invalid reconciliation archive');
    return {report:report,file_id:file.getId()};
  };
  let originalError;
  try { return read(DriveApp.getFileById(fileId)); }
  catch (err) { originalError = err; }
  // A stale file reference must not strand surviving original archives. Search
  // only the configured private folder and exact session's EOD filenames.
  const files = folder_().searchFiles("trashed = false and title contains '" + targetDay + "_eod_'");
  const candidates = [];
  for (let i=0; i<100 && files.hasNext(); i++) {
    const file=files.next();
    if (file.getName().startsWith(targetDay + '_eod_') && file.getName().endsWith('.json.gz')) candidates.push(file);
  }
  candidates.sort((a,b)=>b.getDateCreated().getTime()-a.getDateCreated().getTime());
  for (const file of candidates.slice(0,5)) {
    try { return read(file); } catch (err) {}
  }
  if (String(originalError.message || '').includes('Invalid reconciliation archive')) throw originalError;
  throw new Error('Recovery archive unavailable or unreadable');
}

function recoverEod_(targetDay, fileId) {
  const source = recoveryArchive_(targetDay,fileId);
  return capture_('eod', {target_day:targetDay,catalogue:source.report.catalogue,source_file_id:source.file_id});
}

/**
 * Drain queued prior-session EOD captures. With no argument it recovers up to five
 * pending days before today, so one editor run repairs a backlog; with a day it
 * repairs just that session. The HTTP action wraps this behind WEBHOOK_KEY.
 */
function backfillPendingEod(requestedDay) {
  const today = day_(new Date());
  const wanted = String(requestedDay || '').trim();
  if (wanted && (!/^\d{4}-\d{2}-\d{2}$/.test(wanted) || !Number.isFinite(Date.parse(wanted)))) {
    throw new Error('Invalid session date');
  }
  const queued = loadPendingEod_();
  const days = (wanted ? [wanted] : Object.keys(queued))
    .filter(day => day < today && queued[day]).sort()
    .slice(0, wanted ? 1 : 5);
  if (!days.length) throw new Error(wanted ? 'No pending EOD for ' + wanted : 'No pending EOD sessions');
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) throw new Error('Capture in progress');
  try {
    const sessions = days.map(day => {
      try {
        const pending = loadPendingEod_();
        if (!pending[day]) throw new Error('Session is no longer queued');
        const result = recoverEod_(day, pending[day]);
        return {day: day, status: result.status, file_id: result.file_id,
          pending: result.pending || [], closing_risk: result.closing_risk || {}};
      } catch (err) {
        return {day: day, status: 'error', error: safeError_(err)};
      }
    });
    return {recovered: sessions.filter(row => row.status === 'complete').length, sessions: sessions,
      remaining: pendingEodDays_().filter(day => day < today)};
  } finally {
    lock.releaseLock();
  }
}

/** Automatic, fair EOD queue worker, called only outside snapshot hours. */
function reconcilePriorSession() {
  const properties = props_(), started = Date.now();
  const now = new Date(), today = day_(now);
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(100)) return;
  let attempted = false;
  try {
    const pending = loadPendingEod_();
    const state = readJsonProperty_(properties, 'EOD_RECOVERY_STATE', {});
    // The archive-reference repair gets one immediate retry for an earlier error.
    for (const entry of Object.values(state)) if (entry.status === 'error' && !entry.error) entry.next_at = 0;
    // One bad date must never starve the rest of the backlog.
    const targets = Object.keys(pending).filter(d => d < today &&
      !(Number(state[d]?.next_at) > started)).sort((a,b) =>
      (Number(state[a]?.attempt_at) || 0) - (Number(state[b]?.attempt_at) || 0) || a.localeCompare(b));
    if (!targets.length || started - Number(properties.getProperty('ATTEMPT_RECONCILIATION') || 0) < 55000) return;
    const total = readRuntimeBudget_(properties, 'RUNTIME_BUDGET', started);
    const own = readRuntimeBudget_(properties, 'RECONCILIATION_BUDGET', started);
    // Reserve a worst-case capture plus five minutes for the current session EOD.
    const admission = runtimePolicy_(properties).total_minutes * 60000 - 300000 - CAPTURE_SAFE_DEADLINE_MS;
    if (total.ms >= admission || own.ms >= 8 * 60000 - CAPTURE_SAFE_DEADLINE_MS) return;
    properties.setProperty('ATTEMPT_RECONCILIATION', String(started));
    for (const targetDay of targets.slice(0,3)) {
      if (Date.now() - started > 45000 || total.ms + Date.now() - started >= admission
          || own.ms + Date.now() - started >= 8 * 60000 - CAPTURE_SAFE_DEADLINE_MS) break;
      attempted = true;
      const entry = {attempt_at:Date.now(), attempts:(Number(state[targetDay]?.attempts) || 0) + 1};
      // Save before the request too: interrupted executions receive a cooldown.
      entry.next_at = entry.attempt_at + 30 * 60000;
      state[targetDay] = entry;
      properties.setProperty('EOD_RECOVERY_STATE', JSON.stringify(capCompletedEodDays_(state,30)));
      try {
        const result = recoverEod_(targetDay,pending[targetDay]);
        entry.status = result.status;
        entry.pending = result.pending || [];
        entry.errors = (result.errors || []).slice(0,10);
        if (result.status === 'complete') {
          entry.completed_at = Date.now();
          entry.file_id = result.file_id;
          entry.next_at = null;
        } else {
          entry.next_at = Date.now() + Math.min(360,30 * Math.pow(2,Math.min(entry.attempts-1,4))) * 60000;
        }
      } catch (err) {
        entry.status = 'error'; entry.error = safeError_(err);
        entry.next_at = Date.now() + Math.min(360,30 * Math.pow(2,Math.min(entry.attempts-1,4))) * 60000;
      }
      // Keep the property bounded even for repetitive broker errors.
      state[targetDay] = {attempt_at:entry.attempt_at,attempts:entry.attempts,status:entry.status,
        next_at:entry.next_at,completed_at:entry.completed_at,
        error:entry.error || (entry.errors?.[0]?.reason) || null};
      properties.setProperty('EOD_RECOVERY_STATE', JSON.stringify(capCompletedEodDays_(state,30)));
      properties.setProperty('LAST_RECOVERY_RESULT', JSON.stringify({day:targetDay,...entry}));
      properties.setProperty('RECONCILIATION_STATUS', targetDay + ' ' + entry.status + ' ' + new Date().toISOString());
      if (entry.error === 'Set UPSTOX_TOKEN in Script Properties'
          || entry.errors?.some(e => /Broker HTTP (401|403)/.test(e.reason))) break;
    }
  } finally {
    if (attempted) for (const key of ['RUNTIME_BUDGET','RECONCILIATION_BUDGET']) {
      addRuntimeBudget_(properties,key,Date.now() - started);
    }
    try { scriptCache_()?.remove('DOGET_COLLECTOR_STATUS_V1'); } catch (err) {}
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
    completed_eod_cap_days: CONFIG.completedEodMaxDays,
    status: properties.getProperty('COLLECTOR_STATUS'),
    last_snapshot: marketFieldsOnly_(readJsonProperty_(properties, 'LAST_SNAPSHOT', null)),
    last_eod: readJsonProperty_(properties, 'LAST_EOD', null)
  }));
}

/**
 * Public Web API endpoint for volatilitylab.in.
 * Caches responses in CacheService for 55 seconds to eliminate redundant Drive/Gzip reads.
 */
function manualDownloadSession(targetDay) {
  const started = Date.now();
  const day = String(targetDay || '2026-11-08');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !Number.isFinite(Date.parse(day)) ||
      new Date(day).toISOString().slice(0, 10) !== day) throw new Error('Invalid session date');
  console.log('Starting manual 1-minute download for session: ' + day);

  const keys = [
    CONFIG.indices.NIFTY,
    CONFIG.indices.BANKNIFTY,
    'NSE_INDEX|India VIX'
  ];

  let catalogue = null;
  try {
    catalogue = catalogue_(new Date(Date.parse(day + 'T12:00:00+05:30')));
    Object.values(catalogue.indices).forEach(idx => {
      idx.futures.forEach(f => keys.push(f.instrument_key));
    });
  } catch (err) {
    catalogue = null;
    console.warn('Catalogue discovery fallback: ' + safeError_(err));
  }

  const results = {};
  const errors = catalogue ? [] : [{instrument:'catalogue', error:'Historical futures catalogue unavailable'}];

  keys.forEach(key => {
    try {
      if (Date.now() - started > CAPTURE_SAFE_DEADLINE_MS) throw new Error('Historical download time budget exhausted');
      const path = '/v3/historical-candle/' + encodeURIComponent(key) + '/minutes/1/' + day + '/' + day;
      const data = api_(path);
      if (!Array.isArray(data.candles)) throw new Error('Historical candle response is malformed');
      results[key] = data.candles;
    } catch (err) {
      errors.push({ instrument: key, error: safeError_(err) });
    }
  });

  const observedMinutes = new Set();
  Object.keys(results).forEach(key => {
    const rows = results[key];
    const valid = rows.filter(c => {
      if (!Array.isArray(c) || c.length < 5) return false;
      const timestamp = timestampMillis_(c[0]);
      if (!Number.isFinite(timestamp) || day_(new Date(timestamp)) !== day) return false;
      const prices = c.slice(1, 5);
      return prices.every(p => typeof p === 'number' && Number.isFinite(p) && p > 0)
        && c[2] >= Math.max(c[1], c[4]) && c[3] <= Math.min(c[1], c[4]) && c[3] <= c[2];
    });
    if (!valid.length || valid.length !== rows.length) {
      errors.push({instrument:key, error:'Missing or invalid historical minute candles'});
    }
    valid.forEach(c => observedMinutes.add(timestampMillis_(c[0])));
  });
  const status = !observedMinutes.size ? 'empty' : errors.length ? 'partial' : 'available';
  const bundle = {
    schema:1, provider:'Upstox', day:day, mode:'historical_candles',
    type:'1minute_historical_session', downloaded_at:new Date().toISOString(),
    instruments:results, minutes_count:observedMinutes.size, status:status, errors:errors
  };
  // A historical OHLC interval carries no contemporaneous option book or Greeks.
  // Keep the raw candle arrays once, with the actual time they were downloaded.
  const fileId = observedMinutes.size ? save_(day + '_1minute_historical_' + status, bundle) : null;
  return {day:day, status:status, saved_snapshots:0, consolidated_file_id:fileId,
    minutes:observedMinutes.size, errors:errors};
}



/**
 * Backfill missing EOD archives for Nov 4 to Nov 8.
 */
function backfillAllMissingEod_2026_11_04_to_08() {
  const missingDays = ['2026-11-04', '2026-11-05', '2026-11-06', '2026-11-08'];
  const results = {};
  missingDays.forEach(day => {
    try {
      console.log('Backfilling EOD for ' + day);
      results[day] = capture_('eod', { target_day: day, catalogue: catalogue_(new Date(day + 'T12:00:00+05:30')) });
    } catch (err) {
      results[day] = { error: safeError_(err) };
    }
  });
  return results;
}

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
    const providedKey = e && e.parameter ? e.parameter.key : null;
    const mutation = ['manual_download', 'download_muhurat', 'backfill_eod', 'eod_backfill'].includes(action);
    if ((mutation && !requiredKey) || (requiredKey && providedKey !== requiredKey)) {
      return ContentService.createTextOutput(JSON.stringify({ error: 'Unauthorized' }))
        .setMimeType(ContentService.MimeType.JSON);
    }
    const cache = scriptCache_();
    if (action === 'backfill_eod') {
      const result = backfillAllMissingEod_2026_11_04_to_08();
      return ContentService.createTextOutput(JSON.stringify(result))
        .setMimeType(ContentService.MimeType.JSON);
    }

    if (action === 'manual_download' || action === 'download_muhurat') {
      const targetDay = (e && e.parameter && e.parameter.day) ? e.parameter.day : '2026-11-08';
      const result = manualDownloadSession(targetDay);
      return ContentService.createTextOutput(JSON.stringify(result))
        .setMimeType(ContentService.MimeType.JSON);
    }

    if (action === 'eod_backfill') {
      const requested = (e && e.parameter && e.parameter.day) ? e.parameter.day : '';
      const result = backfillPendingEod(requested);
      return ContentService.createTextOutput(JSON.stringify(result))
        .setMimeType(ContentService.MimeType.JSON);
    }

    if (action === 'candles') {
      const instrument = (e && e.parameter && e.parameter.instrument) ? e.parameter.instrument : 'NSE_INDEX|Nifty 50';
      const interval = (e && e.parameter && e.parameter.interval) ? e.parameter.interval : 'minutes/1';
      const toDate = (e && e.parameter && e.parameter.to_date) ? e.parameter.to_date : '2026-11-08';
      const fromDate = (e && e.parameter && e.parameter.from_date) ? e.parameter.from_date : toDate;
      const path = '/v3/historical-candle/' + encodeURIComponent(instrument) + '/' + interval + '/' + toDate + '/' + fromDate;
      const data = api_(path);
      return ContentService.createTextOutput(JSON.stringify(data))
        .setMimeType(ContentService.MimeType.JSON);
    }

    if (action === 'list_days') {
      const folder = folder_();
      const files = folder.getFiles();
      const days = new Set();
      let count = 0;
      while (files.hasNext() && count < 200) {
        const f = files.next();
        count++;
        const name = f.getName();
        if ((name.indexOf('_snapshot_') !== -1 || name.indexOf('_eod_') !== -1) && name.endsWith('_complete.json.gz')) {
          const match = name.match(/^(\d{4}-\d{2}-\d{2})/);
          if (match) days.add(match[1]);
        }
      }
      return ContentService.createTextOutput(JSON.stringify({
        days: Array.from(days).sort().reverse()
      })).setMimeType(ContentService.MimeType.JSON);
    }

    const requestedDay = e && e.parameter && e.parameter.day;
    if (requestedDay && action === 'latest') {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(requestedDay)) throw new Error('Invalid archive date');
      let file = null;
      const folder = folder_();
      const files = folder.getFiles();
      let latestTime = 0;
      let count = 0;
      while (files.hasNext() && count < 150) {
        const f = files.next();
        count++;
        const name = f.getName();
        if (name.startsWith(requestedDay) && (name.indexOf('_snapshot_') !== -1 || name.indexOf('_eod_') !== -1) && name.endsWith('_complete.json.gz')) {
          const time = f.getLastUpdated().getTime();
          if (time > latestTime) {
            latestTime = time;
            file = f;
          }
        }
      }
      if (!file) throw new Error('No complete archive for ' + requestedDay);
      return ContentService.createTextOutput(JSON.stringify(marketFieldsOnly_(JSON.parse(Utilities.ungzip(file.getBlob()).getDataAsString()))))
        .setMimeType(ContentService.MimeType.JSON);
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
        eod_pending: pendingEodDays_(),
        reconciliation_status: properties.getProperty('RECONCILIATION_STATUS') || null,
        recovery_state: readJsonProperty_(properties, 'EOD_RECOVERY_STATE', {}),
        last_recovery_result: readJsonProperty_(properties, 'LAST_RECOVERY_RESULT', null),
        collection_only: true,
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
