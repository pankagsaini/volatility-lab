// Compare the actual browser and Apps Script implementations to Python's independently checked fills.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const gs = fs.readFileSync(path.join(root, 'volatility_lab/cloud/apps_script/Code.gs'), 'utf8');
const now = '2026-11-13T10:00:00+05:30';
class Clock extends Date {
  constructor(...args) { super(...(args.length ? args : [now])); }
  static now() { return Date.parse(now); }
}
const cloud = vm.createContext({Date: Clock});
vm.runInContext(gs, cloud);
const web = vm.createContext({Date: Clock});
vm.runInContext(html.match(/var STRATEGY_CAPITAL_PER_LOT = \{[\s\S]*?\};/)[0], web);
for (const name of ['formatLiveNumber', 'liveNumber', 'liveTimestamp', 'liveBookValid', 'brokerIvPercent',
                    'brokerWingIv_', 'buildStrategyLegs_', 'expiryPayoff', 'evaluateStrategiesForExpiry']) {
  const start = html.indexOf('      function ' + name + '(');
  assert(start >= 0, name);
  vm.runInContext(html.slice(start, html.indexOf('\n      }', start) + 8), web);
}
const plain = value => JSON.parse(JSON.stringify(value));
for (const {sym, spot, lots, rows, setups} of JSON.parse(fs.readFileSync(0, 'utf8'))) {
  const chain = rows.map(row => ({...row, k: row.strike, cQuoteAt: now, pQuoteAt: now,
    uCallIv: row.cIv, uPutIv: row.pIv, uCallDelta: row.cDelta, uPutDelta: row.pDelta}));
  const item = {atm: spot, chain, dte: 6, hv10: 14, hv30: 15, syn: NaN, fut: NaN};
  const evaluated = web.evaluateStrategiesForExpiry(item, sym, lots);
  assert.equal(evaluated.length, 6);
  const units = (sym === 'NIFTY' ? 65 : 30) * lots;
  for (const setup of setups) {
    const legs = cloud.buildStrategyLegs_(sym, spot, setup.strat);
    for (const leg of legs) {
      const book = chain.find(row => row.k === leg.strike);
      leg.entryPx = cloud.executableQuote_(book, leg.type, leg.side, units * leg.mult, false, now);
    }
    const bounds = cloud.expiryPayoff(legs, units);
    const displayed = evaluated.find(s => s.code === setup.strat);
    for (const field of ['maxLoss', 'maxProfit', 'netPremium', 'netPremiumPoints', 'breakEvens']) {
      assert.deepEqual(plain(bounds[field]), setup[field], sym + ' ' + setup.strat + ' cloud ' + field);
      assert.deepEqual(plain(displayed[field]), setup[field], sym + ' ' + setup.strat + ' browser ' + field);
    }
    const policy = cloud.forwardExitThresholds_(setup.strat, bounds);
    assert.deepEqual([policy.stop, policy.target], setup.exitThresholds);
    assert.equal(displayed.capital, setup.capital);
  }
  for (const prefix of ['c', 'p']) {
    assert.equal(web.brokerWingIv_(chain, prefix, now), cloud.brokerWingIv_(chain, prefix, now));
  }
  assert.equal(web.evaluateStrategiesForExpiry({...item, chain: []}, sym, lots).length, 0);
  chain.forEach(book => { book.cBidQty = 0; });
  assert.equal(web.evaluateStrategiesForExpiry(item, sym, lots).length, 0);
  assert.equal(cloud.executableQuote_(chain[0], 'CE', 1, units, false, now), 0);
}

// An unreadable authoritative Drive file must stop both reads and writes.
const corrupt = {getBlob: () => ({getDataAsString: () => '{'}), setContent: assert.fail};
cloud.ledgerFile_ = name => name.endsWith('.json') ? corrupt : null;
assert.throws(() => cloud.loadForwardLedger_(), /Invalid forward ledger/);
assert.throws(() => cloud.saveForwardLedger_(cloud.cleanSlateForwardLedger_()), /Invalid forward ledger/);
assert.throws(() => cloud.validateLedger_({open_positions: [], closed_trades: {}}), /ledger field/);
