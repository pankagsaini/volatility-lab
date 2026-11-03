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
 * - Automatic weekly retention with cryptographic SHA-256 receipt verification.
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
        Accept: 'application/json'
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

    // Execute requests with execution budget guard
    for (const req of requests) {
      if (Date.now() - started > 240000) {
        report.errors.push({ item: req.label, reason: 'Runtime budget exhausted' });
        continue;
      }

      try {
        const data = api_(req.path, req.params);

        if (req.label === 'quotes') {
          const returnedTokens = new Set(Object.values(data).map(q => q.instrument_token));
          if (keys.some(k => !returnedTokens.has(k))) {
            throw new Error('Incomplete quote response');
          }
        } else if (req.path === '/v2/option/chain' && data.some(r => r.expiry !== req.params.expiry_date)) {
          throw new Error('Wrong option expiry');
        }

        report.records.push({
          label: req.label,
          retrieved_at: new Date().toISOString(),
          data: data
        });

        if (mode === 'eod' && req.label !== 'quotes') {
          const candles = data.candles || [];
          if (!candles.some(c => String(c[0]).slice(0, 10) === targetDay)) {
            report.pending.push(req.label);
          }
        }
      } catch (err) {
        report.errors.push({ item: req.label, reason: safeError_(err) });
      }

      Utilities.sleep(150);
    }

    report.status = (report.errors.length || report.pending.length) ? 'partial' : 'complete';
  } catch (err) {
    report.errors.push({ item: 'catalogue', reason: safeError_(err) });
  }

  report.fetch_seconds = (Date.now() - started) / 1000;

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

  // Purge any existing scheduledCapture triggers
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'scheduledCapture')
    .forEach(t => ScriptApp.deleteTrigger(t));

  // Install 1-minute time-driven trigger
  ScriptApp.newTrigger('scheduledCapture')
    .timeBased()
    .everyMinutes(1)
    .create();

  properties.setProperty('INTERVAL_MINUTES', '3');
  properties.setProperty('SCHEDULE_MODE', 'intraday');

  console.log('Enabled: three-minute snapshots, weekdays 09:00-16:00 IST; EOD retries 18:00-22:00 IST.');
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

  // Pre-market reconciliation window: 07:00 - 09:00 IST (minutes 420 to 540)
  if (mins >= 420 && mins < 540) {
    reconcilePriorSession();
    return;
  }

  // Skip weekends (Saturday=6, Sunday=7)
  if (weekday > 5) {
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
    const interval = Number(properties.getProperty('INTERVAL_MINUTES') || 3);
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

    // Daily runtime budget guard (55 minutes max execution per day)
    let budget = JSON.parse(properties.getProperty('RUNTIME_BUDGET') || '{}');
    if (budget.day !== today) {
      budget = { day: today, ms: 0 };
    }
    if (budget.ms >= 55 * 60000) {
      const message = 'Daily runtime safety limit reached.';
      properties.setProperty('COLLECTOR_STATUS', message + ' ' + today);
      return;
    }

    attempted = true;
    properties.setProperty('ATTEMPT_' + mode, String(now.getTime()));

    const result = capture_(mode);

    if (mode === 'snapshot' && result.status === 'complete') {
      properties.setProperty('LAST_SNAPSHOT_SLOT', slot);
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
      b.ms += Date.now() - started + 1000;
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
        b.ms += Date.now() - started + 1000;
        properties.setProperty(key, JSON.stringify(b));
      }
    }
    lock.releaseLock();
  }
}

/**
 * Install the daily 20:00 trigger for weekly retention cleanup.
 */
function installWeeklyCleanup() {
  const triggerExists = ScriptApp.getProjectTriggers().some(t => t.getHandlerFunction() === 'weeklyCleanup');

  if (!triggerExists) {
    ScriptApp.newTrigger('weeklyCleanup')
      .timeBased()
      .everyDays(1)
      .atHour(20)
      .inTimezone(CONFIG.timeZone)
      .create();
  }

  console.log('Weekly cleanup enabled: keep 14 days; checksum-verified local files moved to Trash.');
}

/**
 * Prune archived files older than 14 days if cryptographically verified against laptop receipts.
 */
function cleanupFiles_(folder, receipt, now) {
  const validReceipt = (
    receipt.schema === 1 &&
    receipt.files &&
    typeof receipt.files === 'object' &&
    Number.isFinite(Date.parse(receipt.verified_at)) &&
    now - Date.parse(receipt.verified_at) <= 8 * 86400000 &&
    Date.parse(receipt.verified_at) <= now
  );

  if (!validReceipt) {
    return { count: 0, completed: false, reason: 'receipt expired or invalid' };
  }

  const files = folder.getFiles();
  let count = 0;

  while (files.hasNext()) {
    if (Date.now() - now > 200000) {
      return { count: count, completed: false, reason: 'batch limit' };
    }

    const file = files.next();
    const name = file.getName();

    if (!/^\d{4}-\d{2}-\d{2}_(snapshot|test|eod)_\d{6}_(complete|partial)\.json\.gz$/.test(name)) {
      continue;
    }

    const fileDate = Date.parse(name.slice(0, 10) + 'T23:59:59+05:30');
    if (!Number.isFinite(fileDate) || now - fileDate < 14 * 86400000) {
      continue;
    }

    const receiptEntry = receipt.files[name];
    if (
      !receiptEntry ||
      !Number.isSafeInteger(receiptEntry.size) ||
      receiptEntry.size <= 0 ||
      !/^[a-f0-9]{64}$/.test(receiptEntry.sha256) ||
      receiptEntry.size !== file.getSize()
    ) {
      continue;
    }

    const digestBytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, file.getBlob().getBytes());
    const fileDigest = digestBytes
      .map(b => ('0' + ((b + 256) % 256).toString(16)).slice(-2))
      .join('');

    if (fileDigest !== receiptEntry.sha256) {
      continue;
    }

    file.setTrashed(true);
    count++;
  }

  return { count: count, completed: true };
}

/**
 * Weekly cleanup job handler.
 */
function weeklyCleanup() {
  const properties = props_();
  const now = Date.now();
  const lastCleanup = Number(properties.getProperty('CLEANUP_COMPLETED') || 0);

  if (now - lastCleanup < 7 * 86400000) {
    console.log('Weekly cleanup not due yet');
    return;
  }

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(100)) {
    return;
  }

  try {
    const folder = folder_();
    const receipts = folder.getFilesByName('local_verified_receipts.json');

    if (!receipts.hasNext()) {
      console.log('Cleanup skipped: laptop receipt not available');
      return;
    }

    const receipt = JSON.parse(receipts.next().getBlob().getDataAsString());
    const result = cleanupFiles_(folder, receipt, now);

    properties.setProperty('LAST_CLEANUP', JSON.stringify({
      checked: new Date(now).toISOString(),
      ...result
    }));

    if (result.completed) {
      properties.setProperty('CLEANUP_COMPLETED', String(now));
    }

    console.log(JSON.stringify(result));
  } finally {
    lock.releaseLock();
  }
}

/**
 * Automated test ensuring cleanup isolation in a disposable test folder.
 */
function testCleanupIsolation() {
  const disposableFolder = DriveApp.createFolder('Volatility Lab DISPOSABLE cleanup test ' + new Date().toISOString());
  const content = 'cleanup-safe-test-2026-10-21';
  const expectedSha = 'a13f2b8298438f15fab24c23e7a9f34222b608c07a1c72ce64a5009a97abd616';

  const oldFile = disposableFolder.createFile('2026-09-01_test_120000_complete.json.gz', content);
  const recentFile = disposableFolder.createFile(day_(new Date()) + '_test_120000_complete.json.gz', content);

  const receipt = { schema: 1, verified_at: new Date().toISOString(), files: {} };

  // Guard test: missing receipt
  const missing = cleanupFiles_(disposableFolder, receipt, Date.now());
  if (oldFile.isTrashed()) {
    throw new Error('Missing receipt guard failed');
  }

  // Guard test: wrong checksum
  receipt.files[oldFile.getName()] = { size: oldFile.getSize(), sha256: '0'.repeat(64) };
  const mismatch = cleanupFiles_(disposableFolder, receipt, Date.now());
  if (oldFile.isTrashed()) {
    throw new Error('Checksum guard failed');
  }

  // Verified eligible cleanup
  receipt.files[oldFile.getName()].sha256 = expectedSha;
  receipt.files[recentFile.getName()] = { size: recentFile.getSize(), sha256: expectedSha };

  const eligible = cleanupFiles_(disposableFolder, receipt, Date.now());

  if (!DriveApp.getFileById(oldFile.getId()).isTrashed() ||
      DriveApp.getFileById(recentFile.getId()).isTrashed() ||
      eligible.count !== 1) {
    throw new Error('Retention test failed');
  }

  console.log(JSON.stringify({
    test: 'PASS',
    missing_receipt_retained: missing.count === 0,
    wrong_checksum_retained: mismatch.count === 0,
    verified_old_file_trashed: DriveApp.getFileById(oldFile.getId()).isTrashed(),
    recent_file_retained: !DriveApp.getFileById(recentFile.getId()).isTrashed(),
    folder: disposableFolder.getUrl(),
    market_files_touched: 0
  }));
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
    cleanup_completed: properties.getProperty('CLEANUP_COMPLETED'),
    last_cleanup: properties.getProperty('LAST_CLEANUP'),
    handlers: ScriptApp.getProjectTriggers().map(t => t.getHandlerFunction()),
    interval: properties.getProperty('INTERVAL_MINUTES'),
    status: properties.getProperty('COLLECTOR_STATUS'),
    last_snapshot: JSON.parse(properties.getProperty('LAST_SNAPSHOT') || 'null'),
    last_eod: JSON.parse(properties.getProperty('LAST_EOD') || 'null')
  }));
}
