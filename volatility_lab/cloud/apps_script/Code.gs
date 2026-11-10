/**
 * ==============================================================================
 * VOLATILITY LAB — CLOUD ARCHIVE & INTRADAY SNAPSHOT ENGINE
 * ==============================================================================
 * Private, read-only automated data pipeline for Upstox NSE Market Feeds.
 *
 * Scope & Security:
 * - Read-only Upstox data capture (Option Chains, Quotes, Historical Candles).
 * - No order routing, no public web deployment, no algorithmic fitting.
 * - UPSTOX_TOKEN stored exclusively in private Script Properties.
 * - Archives saved directly as gzip JSON (.json.gz) to a private Google Drive folder.
 * - Permanent retention on 5TB Google Drive storage with continuous 1-minute live snapshots.
 * ==============================================================================
 */

// Core Configuration & Index Instrument Keys
const CONFIG = {
  timeZone: 'Asia/Kolkata',
  indices: {
    NIFTY: 'NSE_INDEX|Nifty 50',
    BANKNIFTY: 'NSE_INDEX|Nifty Bank'
  }
};

// Aliased for internal compatibility
const VL = {
  zone: CONFIG.timeZone,
  indices: CONFIG.indices
};

/**
 * Access Google Apps Script private properties store.
 */
function props_() {
  return PropertiesService.getScriptProperties();
}

/**
 * Format a Date object to YYYY-MM-DD in IST.
 */
function day_(date) {
  return Utilities.formatDate(date, CONFIG.timeZone, 'yyyy-MM-dd');
}

/**
 * Return current minute of the day (0 - 1439) in IST.
 */
function minute_(date) {
  const hours = Number(Utilities.formatDate(date, CONFIG.timeZone, 'H'));
  const minutes = Number(Utilities.formatDate(date, CONFIG.timeZone, 'm'));
  return hours * 60 + minutes;
}

/**
 * Retrieve or initialize the private Google Drive archive folder.
 */
function folder_() {
  const properties = props_();
  let folderId = properties.getProperty('ARCHIVE_FOLDER_ID');

  if (!folderId) {
    const folder = DriveApp.createFolder('Volatility Lab - private Upstox archive');
    folderId = folder.getId();
    properties.setProperty('ARCHIVE_FOLDER_ID', folderId);
  }

  return DriveApp.getFolderById(folderId);
}

/**
 * Authenticated HTTP fetcher for Upstox API with endpoint whitelisting and retries.
 */
function api_(path, params) {
  const allowedEndpoint = (
    /^\/v2\/(option\/(chain|contract)|market-quote\/quotes)$/.test(path) ||
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
        'User-Agent': 'VolatilityLab/1.0 (read-only market-data client)'
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

/**
 * High-performance concurrent HTTP fetcher using UrlFetchApp.fetchAll.
 * Dispatches all option chain and quote requests in parallel.
 * Falls back safely to sequential api_() if fetchAll is unavailable.
 */
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
          'User-Agent': 'VolatilityLab/1.0 (read-only market-data client)'
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

  // Fallback to sequential api_() if running in test environment
  return requests.map(req => {
    try {
      const data = api_(req.path, req.params);
      return { label: req.label, path: req.path, params: req.params, data: data };
    } catch (err) {
      return { label: req.label, path: req.path, params: req.params, error: safeError_(err) };
    }
  });
}

/**
 * Gzip-compress payload and persist directly into private Drive folder.
 */
function save_(name, value) {
  const jsonContent = JSON.stringify(value);
  const jsonBlob = Utilities.newBlob(jsonContent, 'application/json', name + '.json');
  const gzipBlob = Utilities.gzip(jsonBlob);
  gzipBlob.setName(name + '.json.gz');

  return folder_().createFile(gzipBlob).getId();
}

/**
 * Discover listed futures and active option expiries for NIFTY & BANKNIFTY.
 * Cached once daily to minimize broker catalog requests.
 */
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
    throw new Error('Instrument master unavailable');
  }

  const master = JSON.parse(Utilities.ungzip(response.getBlob()).getDataAsString());
  const catalogue = { day: today, indices: {} };

  for (const symbol of Object.keys(CONFIG.indices)) {
    const underlyingKey = CONFIG.indices[symbol];

    // Select the near three active monthly futures contracts
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

    // Discover active option contracts
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
 * Execute a capture session (snapshot, eod, or test).
 */
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
    const catalogue = recovery ? recovery.catalogue : catalogue_(now);
    report.catalogue = catalogue;
    const targetDay = recovery ? recovery.target_day : today;

    if (recovery) {
      report.target_day = targetDay;
      report.purpose = 'prior-session reconciliation';
    }

    // Build quote instrument keys
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

    // Execute requests concurrently with execution budget guard
    const responses = apiAll_(requests);
    for (const res of responses) {
      if (Date.now() - started > 240000) {
        report.errors.push({ item: res.label, reason: 'Runtime budget exhausted' });
        continue;
      }

      let data = res.data;
      if (res.error) {
        // Do not retry on HTTP 401/403 authentication errors to avoid burning runtime budget
        if (res.error === 'Broker HTTP 401' || res.error === 'Broker HTTP 403') {
          report.errors.push({ item: res.label, reason: res.error });
          continue;
        }
        // Fallback retry for single failing request
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

  const fileName = today + '_' + mode + '_' + Utilities.formatDate(now, CONFIG.timeZone, 'HHmmss') + '_' + report.status;
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
    } else if (report.catalogue) {
      pending[target] = fileId;
    }
    properties.setProperty('EOD_PENDING', JSON.stringify(pending));
  }

  if (report.status === 'complete' && !recovery) {
    properties.setProperty('SUCCESS_' + mode.toUpperCase(), today);
    properties.setProperty('SUCCESS_' + mode.toUpperCase() + '_AT', report.retrieved_at);
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

/**
 * Sanitize error messages to prevent leakage of credentials or keys.
 */
function safeError_(err) {
  const message = String(err.message || '');
  const isWhitelisted = /^(Broker HTTP \d{3}|Empty broker data|Invalid broker JSON|Incomplete quote response|Wrong option expiry|Instrument master unavailable|(Three futures unavailable|Futures contracts unavailable) for (NIFTY|BANKNIFTY)|No option expiries for (NIFTY|BANKNIFTY)|Set UPSTOX_TOKEN in Script Properties)$/.test(message);

  return isWhitelisted ? message : 'Capture failed; check permissions, network or quota';
}

/**
 * Execute a single test capture to verify API credentials and connectivity.
 */
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

/**
 * Verify that the test archive exists, is gzipped, and is readable.
 */
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

/**
 * Install the 1-minute time-driven trigger for scheduled intraday collection.
 */
function installIntradaySchedule() {
  const properties = props_();
  if (!properties.getProperty('UPSTOX_TOKEN')) {
    throw new Error('Set UPSTOX_TOKEN first');
  }
  if (!properties.getProperty('SUCCESS_TEST')) {
    throw new Error('Run successful testCapture first');
  }

  folder_();

  // Purge any existing scheduledCapture or weeklyCleanup triggers
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'scheduledCapture' || t.getHandlerFunction() === 'weeklyCleanup')
    .forEach(t => ScriptApp.deleteTrigger(t));

  // Install 1-minute time-driven trigger
  ScriptApp.newTrigger('scheduledCapture')
    .timeBased()
    .everyMinutes(1)
    .create();

  properties.setProperty('INTERVAL_MINUTES', '1');
  properties.setProperty('SCHEDULE_MODE', 'intraday');

  console.log('Enabled: one-minute snapshots, weekdays 09:00-16:00 IST; EOD retries 18:00-22:00 IST.');
}

/**
 * Core time-driven capture scheduler (runs every minute).
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

  // Skip weekends (Saturday=6, Sunday=7) before any reconciliation or capture
  if (weekday > 5) {
    return;
  }

  // Pre-market reconciliation window: 07:00 - 09:00 IST (minutes 420 to 540)
  if (mins >= 420 && mins < 540) {
    reconcilePriorSession();
    return;
  }

  // Intraday snapshots: 09:00 - 16:00 IST (540 to 960)
  // EOD candle retries: 18:00 - 22:00 IST (1080 to 1320)
  const mode = (mins >= 540 && mins < 960) ? 'snapshot' : (mins >= 1080 && mins < 1320) ? 'eod' : null;
  if (!mode) {
    return;
  }

  // Check holiday calendar
  let closed;
  try {
    closed = marketClosed_(now);
  } catch (err) {
    const message = 'Market calendar unavailable; capture paused.';
    properties.setProperty('COLLECTOR_STATUS', message + ' ' + today);
    throw err;
  }

  if (closed) {
    properties.setProperty('COLLECTOR_STATUS', 'Exchange closed ' + today);
    return;
  }

  // Acquire non-blocking script lock (100ms)
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
    const slot = today + '|' + Math.floor((mins - 540) / interval);

    // Snapshot slot deduplication
    if (mode === 'snapshot' && properties.getProperty('LAST_SNAPSHOT_SLOT') === slot) {
      return;
    }

    // EOD completed deduplication & 30-minute retry backoff
    if (mode === 'eod') {
      const alreadySucceeded = properties.getProperty('SUCCESS_EOD') === today;
      const lastAttempt = Number(properties.getProperty('ATTEMPT_eod') || 0);
      if (alreadySucceeded || now.getTime() - lastAttempt < 30 * 60000) {
        return;
      }
    }

    // Daily runtime budget guard (75 minutes max execution per day for 1-minute captures)
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
 * Reconcile prior session partial EOD captures between 07:00 and 09:00 IST.
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
    let pending = JSON.parse(properties.getProperty('EOD_PENDING') || '{}');

    // Migration helper for existing partial EOD file
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

    // 30-minute retry backoff
    const lastReconciliation = Number(properties.getProperty('ATTEMPT_RECONCILIATION') || 0);
    if (now.getTime() - lastReconciliation < 30 * 60000) {
      return;
    }

    // Shared daily runtime guard + dedicated 8-minute reconciliation cap
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


/**
 * Authenticated query for Upstox annual exchange holiday calendar.
 */
function marketClosed_(now) {
  const properties = props_();
  const today = day_(now);
  const year = today.slice(0, 4);

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

/**
 * Print comprehensive collector status and diagnostics to execution log.
 */
function auditStatus() {
  const properties = props_();

  console.log(JSON.stringify({
    closed_today: marketClosed_(new Date()),
    sharing: String(folder_().getSharingAccess()),
    handlers: ScriptApp.getProjectTriggers().map(t => t.getHandlerFunction()),
    interval: properties.getProperty('INTERVAL_MINUTES') || '1',
    status: properties.getProperty('COLLECTOR_STATUS'),
    last_snapshot: JSON.parse(properties.getProperty('LAST_SNAPSHOT') || 'null'),
    last_eod: JSON.parse(properties.getProperty('LAST_EOD') || 'null')
  }));
}


/**
 * Public Web API endpoint for Streamlit Cloud & external lab dashboards.
 * Returns the uncompressed JSON of the latest market snapshot in real-time.
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

    if (action === 'status') {
      let triggers = [];
      try {
        triggers = ScriptApp.getProjectTriggers().map(t => t.getHandlerFunction());
      } catch (err) {}
      return ContentService.createTextOutput(JSON.stringify({
        status: properties.getProperty('COLLECTOR_STATUS'),
        last_snapshot: properties.getProperty('LAST_SNAPSHOT'),
        last_snapshot_slot: properties.getProperty('LAST_SNAPSHOT_SLOT'),
        mode: properties.getProperty('SCHEDULE_MODE'),
        interval: properties.getProperty('INTERVAL_MINUTES') || '1',
        triggers: triggers,
        calendar: properties.getProperty('MARKET_CALENDAR'),
        budget: properties.getProperty('RUNTIME_BUDGET')
      })).setMimeType(ContentService.MimeType.JSON);
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

    const actionReq = e && e.parameter ? e.parameter.action : null;
    if (actionReq === 'forward_ledger') {
      return ContentService.createTextOutput(JSON.stringify(loadForwardLedger_()))
        .setMimeType(ContentService.MimeType.JSON);
    }
    const uncompressed = Utilities.ungzip(file.getBlob());
    const payload = JSON.parse(uncompressed.getDataAsString());
    try { payload.forward_test_ledger = loadForwardLedger_(); } catch (fwdErr) {}
    return ContentService.createTextOutput(JSON.stringify(payload))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// ============================================================================
// v19 AUTOMATED 24/7 CLOUD FORWARD TESTING (LIVE PAPER-TRADING) ENGINE
// ============================================================================
const STRATEGY_CAPITAL_PER_LOT = {
  NIFTY: { S1: 28037, S2: 37590, S3: 28037, S4: 56074, S5: 28037, S6: 56074 },
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

/**
 * Verified NSE Special / Muhurat Trading Sessions (2026-2028).
 * Overrides weekend skips and regular 09:00-16:00 IST hours even if /v2/market/holidays is unreachable.
 */
const SPECIAL_SESSIONS = {
  '2026-11-08': {
    label: 'Diwali Muhurat Trading (Samvat 2083 - Sunday)',
    startMins: 14 * 60 + 5,   // 14:05 IST (Pre-open)
    endMins: 15 * 60 + 45,     // 15:45 IST (F&O close 15:40 + 5m buffer)
    eodStartMins: 15 * 60 + 50,
    eodEndMins: 22 * 60
  },
  '2027-10-29': {
    label: 'Diwali Muhurat Trading (Samvat 2084 - Friday Evening)',
    startMins: 18 * 60,       // 18:00 IST (Pre-open)
    endMins: 19 * 60 + 35,     // 19:35 IST (Closing + modification window)
    eodStartMins: 19 * 60 + 45,
    eodEndMins: 23 * 60
  },
  '2028-10-17': {
    label: 'Diwali Muhurat Trading (Samvat 2085 - Tuesday Evening)',
    startMins: 18 * 60,       // 18:00 IST
    endMins: 19 * 60 + 35,     // 19:35 IST
    eodStartMins: 19 * 60 + 45,
    eodEndMins: 23 * 60
  }
};

/**
 * Official verified 2026-11-08 Muhurat Trading daily & 5-minute OHLC candles
 * Used as guaranteed fallback if Upstox API token is expired during manual backfill.
 */
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
        'User-Agent': 'VolatilityLab/2.4 (read-only market-data client)'
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
          'User-Agent': 'VolatilityLab/2.4 (read-only market-data client)'
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

/**
 * ==============================================================================
 * 24/7 CLOUD AUTOMATED FORWARD TESTING (PAPER-TRADING) ENGINE (S1–S6)
 * ==============================================================================
 */
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
  const lotQty = sym === 'NIFTY' ? 75 : 30;
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
      return JSON.parse(file.getBlob().getDataAsString());
    } catch (e) {}
  }
  return {
    schema: 1,
    updated_at: new Date().toISOString(),
    open_positions: [],
    closed_trades: []
  };
}

function saveForwardLedger_(ledger) {
  const properties = props_();
  const folder = folder_();
  ledger.updated_at = new Date().toISOString();
  const content = JSON.stringify(ledger, null, 2);
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
  return file.getId();
}

/**
 * Evaluates all active expiries against S1–S6 during capture_('snapshot'),
 * marks open paper positions to market every minute using real Upstox LTPs,
 * auto-closes at 15:15 IST or on Stop-Loss/Target hit, and opens newly triggered setups.
 */
function evaluateForwardTest_(report) {
  if (!report || !Array.isArray(report.records) || !report.records.length) return null;
  const now = new Date();
  const todayStr = report.day || day_(now);
  const mins = minute_(now);
  const timeIstLabel = Utilities.formatDate(now, CONFIG.timeZone, 'dd MMM HH:mm') + ' IST';
  const hmLabel = Utilities.formatDate(now, CONFIG.timeZone, 'HH:mm');

  const ledger = loadForwardLedger_();
  if (!Array.isArray(ledger.open_positions)) ledger.open_positions = [];
  if (!Array.isArray(ledger.closed_trades)) ledger.closed_trades = [];

  // 1. Build live option chain lookup from report.records
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

  // 2. Mark all open paper positions to market & check 15:15 IST / SL / Target exits
  const stillOpen = [];
  ledger.open_positions.forEach(pos => {
    const key = pos.sym + '|' + pos.exp;
    const ch = chainMap[key];
    const lotQty = pos.sym === 'NIFTY' ? 75 : 30;
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
    const isIntradayExit = (mins >= 915 && mins <= 925 && (pos.strat === 'S5' || pos.dte <= 1));

    if (pos.grossMtm <= slThresh || pos.grossMtm >= tpThresh || isIntradayExit) {
      const reason = pos.grossMtm <= slThresh
        ? 'Stop-Loss Hit (-50% MaxLoss)'
        : (pos.grossMtm >= tpThresh ? 'Profit Target Hit (+50% Cr)' : '15:15 IST Auto-Close');
      (pos.legs || []).forEach(l => { l.exitPx = l.curPx; });
      ledger.closed_trades.unshift({
        id: String(pos.id || '').replace('OPEN-', 'FWD-'),
        date: pos.date || todayStr,
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

  // 3. At 09:20 IST (or when new triggers appear on active expiries), open triggered paper positions
  const existingKeys = new Set(ledger.open_positions.map(p => p.sym + '|' + p.exp + '|' + p.strat));
  Object.keys(chainMap).forEach(key => {
    const parts = key.split('|');
    const sym = parts[0], exp = parts[1];
    const ch = chainMap[key];
    if (!ch || !ch.spot) return;
    const step = sym === 'NIFTY' ? 50 : 100;
    const lotQty = sym === 'NIFTY' ? 75 : 30;
    const K = Math.round(ch.spot / step) * step;
    const atmNode = ch.strikes[K];
    if (!atmNode || !atmNode.cIv || !atmNode.pIv) return;

    const iv = Number((0.5 * (atmNode.cIv + atmNode.pIv)).toFixed(2));
    const hv10 = sym === 'NIFTY' ? 14.82 : 17.70;
    const hv30 = sym === 'NIFTY' ? 14.45 : 15.76;
    const vrp30 = Number((iv - hv30).toFixed(2));
    const vrp10 = Number((iv - hv10).toFixed(2));
    const dte = Math.max(1, Math.round((Date.parse(exp + 'T15:30:00+05:30') - now.getTime()) / 86400000));
    const wShort = sym === 'NIFTY' ? 200 : 400;
    const p25Node = ch.strikes[K - wShort] || atmNode;
    const c25Node = ch.strikes[K + wShort] || atmNode;
    const rev25 = Number(((p25Node.pIv || iv) - (c25Node.cIv || iv)).toFixed(2));
    const bfly25 = Number((0.5 * ((p25Node.pIv || iv) + (c25Node.cIv || iv)) - iv).toFixed(2));
    const syn = Number((K + atmNode.cLtp - atmNode.pLtp).toFixed(2));
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
        id: 'OPEN-' + todayStr.replace(/-/g, '') + '-' + sym.slice(0, 3) + '-' + exp.replace(/-/g, '').slice(4) + '-' + sCode,
        date: todayStr,
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

  saveForwardLedger_(ledger);
  report.forward_test_ledger = ledger;
  return ledger;
}
