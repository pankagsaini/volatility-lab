/* Chronological replay of recorded marks. No inferred intrabar fills or IV. */
(function(root) {
  'use strict';
  const finite = x => typeof x === 'number' && Number.isFinite(x);
  const round = x => Math.round(x * 100) / 100;
  const minute = at => Number(at.slice(11,13))*60 + Number(at.slice(14,16)) + Number(at.slice(17,19))/60;
  const clock = v => /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(v || '') ? Number(v.slice(0,2))*60+Number(v.slice(3,5)) : NaN;
  function validate(c) {
    if (!finite(clock(c.start)) || !finite(clock(c.end)) || !finite(clock(c.exit)) ||
        clock(c.start)>clock(c.end) || clock(c.end)>=clock(c.exit) || clock(c.exit)>1439)
      throw new Error('Use a valid entry window ending before the time exit.');
    for (const key of ['stop','target','trail','hold','spotMove','ivMove'])
      if (c[key] !== null && (!finite(c[key]) || c[key]<=0)) throw new Error('Exit limits must be positive or left blank.');
    for (const f of c.criteria || []) if ((f.min!==null && !finite(f.min)) || (f.max!==null && !finite(f.max)) ||
      (f.min!==null && f.max!==null && f.min>f.max)) throw new Error('Check the minimum and maximum entry bounds.');
    if (!Number.isInteger(c.lots) || c.lots<1 || c.lots>50 || !finite(c.slip) || c.slip<0 || c.slip>10)
      throw new Error('Use 1–50 lots and 0–10 slippage ticks.');
    if (c.from && c.to && c.from>c.to) throw new Error('Start date must be before end date.');
  }
  function fees(price,quantity,buy,r) {
    const turnover=price*quantity, exch=turnover*r.opt_exch, ipft=turnover*r.ipft_opt;
    return 20 + (buy?0:turnover*r.opt_stt) + exch + ipft + turnover*r.sebi
      + (buy?turnover*r.opt_stamp:0) + .18*(20+exch+ipft);
  }
  function capital(legs,spot,sym) {
    const shift=sym==='NIFTY'?.08:.10;
    const credit=legs.reduce((s,l)=>s-l.side*l.entry*l.quantity,0);
    const levels=[-.15,-shift,-shift/2,0,shift/2,shift,.15].map(x=>spot*(1+x)).concat(legs.map(l=>l.strike));
    let worst=0;
    for (const s of levels) {
      const payoff=legs.reduce((v,l)=>v+l.side*l.quantity*Math.max(l.kind==='CE'?s-l.strike:l.strike-s,0),0);
      worst=Math.max(worst,-payoff-credit);
    }
    const qty=kind=>legs.filter(l=>l.kind===kind).reduce((s,l)=>s+l.side*l.quantity,0);
    const short=kind=>legs.filter(l=>l.kind===kind&&l.side<0).reduce((s,l)=>s+l.quantity,0);
    const shortQty=Math.max(short('CE'),short('PE'));
    return Math.round(qty('CE')<0||qty('PE')<0 ? Math.max(worst+.02*spot*shortQty,.05*spot*shortQty)
      : shortQty===0 ? Math.max(0,-credit) : Math.max(worst,.015*spot*shortQty));
  }
  function marks(point,legs) {
    const chain=new Map(point.chain.map(x=>[x[0],x]));
    const values=legs.map(l=>chain.get(l.strike)?.[l.kind==='CE'?1:6]);
    return values.every(x=>finite(x)&&x>0)?values:null;
  }
  function metrics(session,p,legs) {
    const chain=new Map(p.chain.map(x=>[x[0],x])), atm=chain.get(p.atm);
    const iv=atm&&atm[2]>0&&atm[7]>0?(atm[2]+atm[7])/2:null;
    const deltas=legs.map(l=>chain.get(l.strike)?.[l.kind==='CE'?3:8]);
    const oi=legs.map(l=>chain.get(l.strike)?.[l.kind==='CE'?4:9]);
    const spreads=legs.map(l=>chain.get(l.strike)?.[l.kind==='CE'?5:10]);
    return {iv,hv10:session.hv10,hv30:session.hv30,rr25:p.rr25,bf25:p.bf25,
      dte:Math.round((Date.parse(session.expiry)-Date.parse(session.date))/86400000),
      vrp:finite(iv)&&finite(session.hv30)?iv-session.hv30:null,
      ivhv10:finite(iv)&&finite(session.hv10)?iv-session.hv10:null,
      entryMin:minute(p.at)-555,
      ivhvRatio:finite(iv)&&session.hv30>0?iv/session.hv30:null,
      spotChange:session.previousClose>0?100*(p.spot/session.previousClose-1):null,
      netDelta:deltas.every(finite)?Math.abs(deltas.reduce((s,d,i)=>s+d*legs[i].side*legs[i].ratio,0)):null,
      minOi:oi.every(x=>finite(x)&&x>=0)?Math.min(...oi):null,
      maxSpread:spreads.every(x=>finite(x)&&x>=0)?Math.max(...spreads):null};
  }
  function matches(m,c) {
    const results=(c.criteria||[]).map(f=>finite(m[f.key])&&(f.min===null||m[f.key]>=f.min)&&(f.max===null||m[f.key]<=f.max));
    return !results.length || (c.logic==='OR'?results.some(Boolean):results.every(Boolean));
  }
  function pnl(session,legs,exit,c) {
    let gross=0,cost=0;
    legs.forEach((l,i)=>{
      const quantity=l.quantity*c.lots, slip=c.slip*.05;
      const buy=l.side>0, entryFill=Math.max(0,l.entry+l.side*slip), exitFill=Math.max(0,exit[i]-l.side*slip);
      const raw=l.side*(exit[i]-l.entry)*quantity;
      const filled=l.side*(exitFill-entryFill)*quantity;
      gross+=raw;cost+=raw-filled+fees(entryFill,quantity,buy,session.rates)+fees(exitFill,quantity,!buy,session.rates);
    });
    return {gross:round(gross),cost:round(cost),net:round(gross-cost)};
  }
  function sessionTrade(s,code,geometry,c) {
    if (s.hvAsOf && s.hvAsOf>=s.date) throw new Error('Historical volatility must precede the entry session.');
    const points=s.points.slice().sort((a,b)=>a.at.localeCompare(b.at));
    let entry=null;
    for (let i=0;i<points.length;i++) {
      const p=points[i],t=minute(p.at);
      if (t<clock(c.start) || t>clock(c.end)+.999999) continue;
      const legs=geometry.map(([side,kind,offset,ratio])=>({side,kind,strike:p.atm+offset,ratio,quantity:s.lot*ratio}));
      const prices=marks(p,legs);
      if (!prices) continue;
      const m=metrics(s,p,legs);
      if (!matches(m,c)) continue;
      legs.forEach((l,j)=>{l.entry=prices[j];});
      entry={i,p,legs,m};break;
    }
    if (!entry) return {skip:'No qualifying entry'};
    const {i,p,legs,m}=entry,cap=capital(legs,p.spot,s.symbol)*c.lots;
    if (!(cap>0)) return {skip:'Capital unavailable'};
    const deadline=Math.min(clock(c.exit),c.hold===null?Infinity:minute(p.at)+c.hold);
    let peak=0,low=0,high=0,used=0,total=0,maxGap=0,lastTime=Date.parse(p.at);
    for (const q of points.slice(i+1)) {
      if (minute(q.at)>deadline+5) break;
      total++;
      const values=marks(q,legs);
      if (!values) continue;
      used++;const at=Date.parse(q.at);maxGap=Math.max(maxGap,(at-lastTime)/60000);lastTime=at;
      const result=pnl(s,legs,values,c);
      peak=Math.max(peak,result.net);low=Math.min(low,result.net);high=Math.max(high,result.net);
      const qm=metrics(s,q,legs);
      // All rules act on this same observed mark. Never fill at a threshold or
      // infer which unseen intrabar level was touched first.
      const reasons=[];
      if (c.stop!==null && result.net<=-c.stop*c.lots) reasons.push('Stop loss');
      if (c.target!==null && result.net>=c.target*c.lots) reasons.push('Profit target');
      if (c.trail!==null && peak>0 && peak-result.net>=c.trail*c.lots) reasons.push('Trailing stop');
      if (c.spotMove!==null && 100*Math.abs(q.spot/p.spot-1)>=c.spotMove) reasons.push('Spot move');
      if (c.ivMove!==null && finite(qm.iv)&&finite(m.iv)&&Math.abs(qm.iv-m.iv)>=c.ivMove) reasons.push('ATM IV change');
      if (minute(q.at)>=deadline) reasons.push(deadline<clock(c.exit)?'Maximum holding time':'Time exit');
      if (!reasons.length) continue;
      const legText=legs.map((l,j)=>(l.side>0?'Buy ':'Sell ')+l.ratio+'× '+l.strike+l.kind+' ('+l.entry.toFixed(2)+'→'+values[j].toFixed(2)+')').join(' / ');
      return {row:{...m,...result,date:s.date,exp:s.expiry,strat:code,sym:s.symbol,
        capital:cap,roc:100*result.net/cap,entryAt:p.at.slice(11,19),exitAt:q.at.slice(11,19),
        holdMin:round((at-Date.parse(p.at))/60000),entrySpot:p.spot,hvAsOf:s.hvAsOf,
        pathN:used,pathTotal:total,maePct:round(-low/cap*100),mfePct:round(high/cap*100),
        exitReason:reasons.join(' + '),maxGap:round(maxGap),entrySource:p.source,exitSource:q.source,
        rulePass:true,ruleFail:'',sig:'Custom entry rules',sigCalc:'Custom entry rules',legs:legText,
        dteBand:null,dteInBand:true,expiryKind:'listed',source:'recorded_ltp_replay'}};
    }
    return {skip:'No complete exit capture within five minutes of the exit deadline',open:true};
  }
  function replay(sessions,manifest,c) {
    validate(c);const rows=[],skips=[];
    for (const s of sessions) {
      if (s.symbol!==c.symbol || (c.from&&s.date<c.from)||(c.to&&s.date>c.to)) continue;
      const day=new Date(s.date+'T12:00:00Z').getUTCDay();
      if (c.weekday!=='ALL'&&day!==Number(c.weekday)) continue;
      const codes=c.structure==='ANY'?Object.keys(manifest.geometry[s.symbol]):[c.structure];
      for (const code of codes) {
        const g=manifest.geometry[s.symbol]?.[code];if(!g) throw new Error('Unknown structure');
        const out=sessionTrade(s,code,g,c);
        if(out.row)rows.push(out.row);else skips.push({date:s.date,strat:code,reason:out.skip,unclosed:!!out.open});
      }
    }
    rows.sort((a,b)=>a.date.localeCompare(b.date)||a.exitAt.localeCompare(b.exitAt)||a.strat.localeCompare(b.strat));
    return {rows,skips};
  }
  root.RecordedReplay={replay,sessionTrade,validate,fees,capital};
  if (typeof module!=='undefined') module.exports=root.RecordedReplay;
})(typeof globalThis!=='undefined'?globalThis:this);
