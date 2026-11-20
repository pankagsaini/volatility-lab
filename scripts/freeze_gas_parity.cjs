/* Compare the deployed evaluator source with the Python freeze cohort using read-only archives.
   Run from the repository: node scripts/freeze_gas_parity.cjs <private archive directory> */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {gunzipSync} = require('node:zlib');

const root = path.resolve(__dirname, '..');
const day = '2026-11-19';
const folder = process.argv[2];
if (!folder) throw Error('Provide the private archive directory.');
const sources = new Map();
for (const sym of ['NIFTY','BANKNIFTY']) {
  const manifest = JSON.parse(fs.readFileSync(path.join(root,'docs','replay',sym+'-'+day+'.json')));
  for (const p of manifest.points) {
    assert(new RegExp('^'+day+'_snapshot_[0-9]{6}_complete[.]json[.]gz$').test(p.source),p.source);
    sources.set(p.source, Date.parse(p.at));
  }
}
let now = 0;
class SnapshotClock extends Date {
  constructor(...args) { super(...(args.length ? args : [now])); }
  static now() { return now; }
}
function formatDate(d, tz, spec) {
  assert.equal(tz,'Asia/Kolkata');
  const ist=new Date(d.getTime()+19800000);
  const iso=ist.toISOString();
  const hhmm=iso.slice(11,16);
  const formats={'yyyy-MM-dd':iso.slice(0,10),H:iso.slice(11,13),m:iso.slice(14,16),u:ist.getUTCDay() || 7,
    'HH:mm':hhmm,'dd MMM HH:mm':iso.slice(8,10)+' '+ist.toLocaleString('en',{month:'short',timeZone:'UTC'})+' '+hhmm};
  if (!(spec in formats)) throw Error('Unhandled Apps Script date format '+spec);
  return String(formats[spec]);
}
const env=vm.createContext({Date:SnapshotClock,Utilities:{formatDate},console});
vm.runInContext(fs.readFileSync(path.join(root,'volatility_lab/cloud/apps_script/Code.gs'),'utf8'),env);
const calendar={year:'2026',checked:day,closed:[],special:{}};
const mockProperties=new Map([['MARKET_CALENDAR',JSON.stringify(calendar)]]);
env.props_=()=>({getProperty:key=>mockProperties.get(key)||null,setProperty:(k,v)=>mockProperties.set(k,v)});
env.saveForwardLedger_=()=>{}; // Ledger mutates in memory. Drive and network are not installed in the VM.
let ledger=env.cleanSlateForwardLedger_();
let processed=0;
for (const [name] of [...sources].sort((a,b)=>a[1]-b[1])) {
  const report=JSON.parse(gunzipSync(fs.readFileSync(path.join(folder,name))));
  now=Date.parse(report.retrieved_at);
  assert(Number.isFinite(now),name);
  ledger=env.evaluateForwardTest_(report,ledger,{});
  processed++;
}
const csv=fs.readFileSync(path.join(root,'handbook','freeze-policy-closed.csv'),'utf8').trim().split(/\r?\n/);
const header=csv.shift().split(',');
const expected=csv.map(line=>Object.fromEntries(line.split(',').map((v,i)=>[header[i],v]))).filter(r=>r.date===day && r.ticks==='0');
const got=ledger.closed_trades.map(t=>({sym:t.sym,strat:t.strat,expiry:t.exp,entry:t.triggerTime,exit:t.exit_at,reason:t.exitReason,
  lots:t.lots,legs:t.legs.map(l=>[l.strike,l.type,l.side,l.mult,l.entryPx,l.curPx]),gross:t.gross,charges:t.charges,net:t.net,capital:t.capital}));
console.log(JSON.stringify({day,processed,open:ledger.open_positions.length,closed:got},null,2));
assert.equal(got.length,expected.length);
assert.equal(ledger.open_positions.length,0);
for (const row of expected) {
  const found=got.find(t=>t.sym===row.symbol && t.strat===row.strategy);
  assert(found);
  assert.equal(Date.parse(found.entry),Date.parse(row.entry_time),'entry');
  assert.equal(Date.parse(found.exit),Date.parse(row.exit_at),'exit');
  for(const k of ['gross','net','charges','capital']) assert.equal(found[k],Number(row[k]),k);
  assert.match(row.exit_reason,/target/i);
  assert.match(found.reason,/target/i);
  assert.equal(found.lots,1);
  assert.equal(found.legs.length,2);
}
