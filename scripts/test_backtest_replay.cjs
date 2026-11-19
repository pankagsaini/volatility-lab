const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const vm = require('node:vm');
const context = {module:{exports:{}}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'backtest-replay.js'),'utf8'),context);
const engine = context.module.exports;
const rates = {opt_exch:0,opt_stt:0,ipft_opt:0,sebi:0,opt_stamp:0};
const point = (time, price, extras={}) => ({at:'2026-11-18T'+time+':00+05:30',spot:100,atm:100,
  chain:[[100,price,15,.5,1000,1,price,15,-.5,1000,1]],source:time,...extras});
const session = points => ({symbol:'NIFTY',date:'2026-11-18',expiry:'2026-11-24',lot:10,
  hvAsOf:'2026-11-17',hv10:12,hv30:13,previousClose:100,rates,points});
const config = {symbol:'NIFTY',structure:'S2',logic:'AND',criteria:[],lots:1,slip:0,
  from:'',to:'',weekday:'ALL',start:'09:20',end:'09:30',exit:'15:15',
  stop:null,target:null,trail:null,hold:null,spotMove:null,ivMove:null};
const geometry = [[1,'CE',0,1],[1,'PE',0,1]];
const trade = (points, overrides={}) => engine.sessionTrade(session(points),'S2',geometry,{...config,...overrides});
let out = trade([point('09:20',100),point('09:21',80),point('09:22',140),point('15:15',150)],{stop:100,target:100});
assert.equal(out.row.exitAt,'09:21:00');
assert.equal(out.row.exitReason,'Stop loss');
assert.equal(out.row.net,-494.4,'Observed overshoot, not a fictional fill at the stop threshold');
assert.equal(out.row.gross,-400);
assert.equal(out.row.hvAsOf,'2026-11-17');
out = trade([point('09:20',100),point('09:21',120),point('09:22',110)],{trail:100});
assert.equal(out.row.exitReason,'Trailing stop');
assert.equal(out.row.exitAt,'09:22:00');
out = trade([point('09:20',100),point('09:21',120,{atm:150,chain:[[100,120,15,.5,1000,1,120,15,-.5,1000,1],[150,1,15,.5,1000,1,1,15,-.5,1000,1]]})],{target:100});
assert.equal(out.row.gross,400,'A moving ATM must not change the entered contracts');
assert.match(out.row.legs,/100CE/);
out = trade([point('09:20',100),point('09:21',120,{chain:[]}),point('09:24',125)],{target:100});
assert.equal(out.row.exitAt,'09:24:00');
assert.equal(out.row.maxGap,4);
assert.equal(out.row.pathTotal,2);
assert.equal(out.row.pathN,1);
assert(trade([point('09:20',100),point('15:21',120)]).open,'Late captures cannot manufacture a time exit');
assert(trade([point('09:20',100),point('15:14',120)]).open,'Do not close before the requested deadline');
assert.equal(trade([point('09:20',100),point('15:17',120)]).row.exitReason,'Time exit');
assert.equal(trade([point('09:20',100),point('09:25',101)],{hold:5}).row.exitReason,'Maximum holding time');
assert.equal(trade([point('09:20',100),point('09:21',101,{spot:102})],{spotMove:1}).row.exitReason,'Spot move');
assert.equal(trade([point('09:20',100),point('09:21',101,{chain:[[100,101,18,.5,1000,1,101,18,-.5,1000,1]]})],{ivMove:2}).row.exitReason,'ATM IV change');
out = trade([point('09:20',100),point('09:21',120),point('15:15',140)],{criteria:[{key:'spotChange',min:1,max:null}]});
assert.equal(out.skip,'No qualifying entry','Future profits do not qualify an entry');
assert.equal(trade([point('09:20',100),point('15:15',120)],{lots:2}).row.cost,94.4,'Brokerage is per leg order, not per lot');
assert.equal(trade([point('09:20',100),point('15:15',120)],{slip:1}).row.cost,96.4);
assert.throws(()=>engine.validate({...config,start:'09:99'}),/valid entry/);
assert.throws(()=>engine.validate({...config,stop:-1}),/positive/);
assert.throws(()=>engine.sessionTrade({...session([]),hvAsOf:'2026-11-18'},'S2',geometry,config),/precede/);
const manifest={geometry:{NIFTY:{S2:geometry}}};
assert.equal(engine.replay([session([point('09:20',100),point('15:15',120)])],manifest,{...config,weekday:'1'}).rows.length,0);

// Audit every published file and exercise all real sessions with six structures.
const root=path.resolve(__dirname,'..'), folder=path.join(root,'docs/replay');
const published=JSON.parse(fs.readFileSync(path.join(folder,'manifest.json')));
const sessions=published.sessions.map(item=>{
  const raw=fs.readFileSync(path.join(folder,item.file));
  assert.equal(crypto.createHash('sha256').update(raw).digest('hex'),item.sha256);
  assert(raw.equals(fs.readFileSync(path.join(root,'replay',item.file))),'Root/public replay parity');
  const s=JSON.parse(raw);
  assert.equal(s.symbol,item.symbol);assert.equal(s.date,item.date);assert.equal(s.points.length,item.points);
  assert(!s.hvAsOf||s.hvAsOf<s.date);
  assert.equal(new Set(s.points.map(p=>p.at)).size,s.points.length);
  for(const p of s.points) assert(p.at.startsWith(s.date)&&p.source.endsWith('.json.gz'));
  return s;
});
for(const symbol of ['NIFTY','BANKNIFTY']) {
  const result=engine.replay(sessions,published,{...config,symbol,structure:'ANY'});
  assert(result.rows.length>50,'Real capture paths produce usable trades');
  assert.equal(result.rows.length+result.skips.length,published.sessions.filter(s=>s.symbol===symbol).length*6);
  assert(result.rows.every(r=>r.entryAt<r.exitAt&&Number.isFinite(r.net)&&r.capital>0));
}
assert.equal(fs.readFileSync(path.join(root,'scripts/backtest-replay.js'),'utf8'),fs.readFileSync(path.join(root,'docs/scripts/backtest-replay.js'),'utf8'));
console.log('Chronological replay, costs, missing marks and published archive checks passed.');
