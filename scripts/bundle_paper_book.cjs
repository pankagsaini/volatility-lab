// A failed feed check must leave the last successful Pages deployment serving.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const [source, target] = process.argv.slice(2);
const content = fs.readFileSync(source);
const book = JSON.parse(content);
assert.equal(book.schema, 2);
assert.equal(book.version, 'forward-test-v20.1-event-time-gated');
assert.equal(book.source_authority, 'python_paper_worker');
assert.equal(book.cohort_kind, 'prospective');
assert.equal(book.policy_version, 'research-v1');
assert.equal(book.provenance_schema, 'event-quotes-v1');
assert.equal(book.worker_checkpoint, undefined);
assert.equal(book._ledger_revision, undefined);
assert([book.inception_date,book.min_trigger_date,book.prospective_start].every(day => day === '2026-11-23'));
assert([book.open_positions,book.closed_trades,book.intraday_mtm_series].every(Array.isArray));
assert([...book.open_positions,...book.closed_trades].every(row =>
  row.policy_version === 'research-v1' && Number.isFinite(Date.parse(row.triggerTime)) &&
  (row.session_date || row.date) >= book.min_trigger_date));
const at = Date.parse(book.updated_at);
assert((Number.isFinite(at) && at <= Date.now()) || (book.updated_at === null &&
  book.status === 'ARMED_WAITING_FOR_FIRST_CAPTURE' && book.open_positions.length === 0 &&
  book.closed_trades.length === 0 && book.intraday_mtm_series.length === 0));
fs.writeFileSync(target,content);
console.log('Pages fallback capture:', book.updated_at || 'awaiting first prospective capture');
