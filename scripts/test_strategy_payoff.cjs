// Compare the actual browser implementation to Python's independently checked fills.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const now = '2026-11-13T10:00:00+05:30';
class Clock extends Date {
  constructor(...args) { super(...(args.length ? args : [now])); }
  static now() { return Date.parse(now); }
}
const web = vm.createContext({Date: Clock});
vm.runInContext(fs.readFileSync(path.join(root,"scripts/freeze-policy.js"),"utf8"), web);
vm.runInContext(html.match(/var STRATEGY_CAPITAL_PER_LOT = \{[\s\S]*?\};/)[0], web);
for (const name of ['formatLiveNumber', 'liveNumber', 'liveTimestamp', 'liveBookValid', 'brokerIvPercent',
                    'brokerWingIv_', 'buildStrategyLegs_', 'expiryPayoff', 'positionalGateOpen', 'evaluateStrategiesForExpiry']) {
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
  for (const setup of setups) {
    const displayed = evaluated.find(s => s.code === setup.strat);
    for (const field of ['maxLoss', 'maxProfit', 'netPremium', 'netPremiumPoints', 'breakEvens']) {
      assert.deepEqual(plain(displayed[field]), setup[field], sym + ' ' + setup.strat + ' browser ' + field);
    }
    assert.equal(displayed.capital, setup.capital);
  }
  assert.equal(web.evaluateStrategiesForExpiry({...item, chain: []}, sym, lots).length, 0);
  chain.forEach(book => { book.cBidQty = 0; });
  assert.equal(web.evaluateStrategiesForExpiry(item, sym, lots).length, 0);
}
