// Execute production browser pricing functions with deterministic quote/source fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
const now = Date.parse('2026-11-13T04:30:00Z');
class Clock extends Date {static now(){return now;}}
const web = vm.createContext({Date:Clock,console, B76_RISK_FREE_RATE:NaN,B76_INV_SQRT_2PI:1/Math.sqrt(2*Math.PI)});
for (const name of ['normPdf','normCdf','liveNumber','liveTimestamp','liveBookValid','computeBlack76ParityForward',
  'black76Greeks','black76Price','black76IvDec','bsmGreeks','bsmIvDec','relativeBookSpread',
  'selectOptionPricing','enrichItemWithBlack76','liveDeltaIv','currentPricingInputs']) {
  const a=html.indexOf('      function '+name+'('); assert(a>=0,name);
  vm.runInContext(html.slice(a,html.indexOf('\n      }',a)+8),web);
}
const clone = x=>JSON.parse(JSON.stringify(x));
function item(spread=2) {
  const expiry='2026-11-17', stamp=new Date(now).toISOString();
  return {expiry,atm:25000,spot:25000,dte:4,rate:.06,dividend:.0142,snapshotId:stamp,spotQuoteAt:stamp,
    brokerAtmIv:22,chain:[{k:25000,cBid:200-spread,cAsk:200+spread,pBid:200-spread,pAsk:200+spread,
      cBidQty:100,cAskQty:100,pBidQty:100,pAskQty:100,cQuoteAt:stamp,pQuoteAt:stamp,
      uCallIv:22,uPutIv:22}], futureBook:{expiry,fBid:25019,fAsk:25021,fBidQty:100,fAskQty:100,fQuoteAt:stamp}};
}
let r=item();
assert.equal(web.selectOptionPricing(r).source,'listed_future');
assert.equal(web.selectOptionPricing(r).forward,25020);
r.futureBook.expiry='2026-11-24';
assert.equal(web.selectOptionPricing(r).source,'synthetic_forward','Do not use a monthly future for a different weekly expiry');
assert.equal(web.selectOptionPricing(r).forward,25000);
for(const spread of [2.999,3,3.001,20]) {
  r=item(spread);delete r.futureBook;
  assert.equal(web.selectOptionPricing(r).model,spread<3?'black76':'bsm','3% boundary: '+spread);
}
r=item(3.1);delete r.futureBook;
const bsm=web.enrichItemWithBlack76(r);
assert.equal(bsm.pricingModel,'BSM');assert(bsm.modelAtmIv>0);assert.equal(bsm.b76AtmIv,null);
assert.equal(bsm.brokerAtmIv,22);assert.equal(bsm.chain[0].uCallIv,22,'Broker observation is immutable');
r=item();delete r.futureBook;r.chain[0].pQuoteAt=new Date(now-6000).toISOString();
assert.equal(web.selectOptionPricing(r).model,'bsm','Desynchronized pair is not a forward');
r.chain[0].cBidQty=0;assert.equal(web.selectOptionPricing(r).model,'bsm');
r=item();r.rate=null;assert.equal(web.selectOptionPricing(r).model,null,'No assumed rate');
r=item(3);delete r.futureBook;r.dividend=null;assert.equal(web.selectOptionPricing(r).model,null,'No assumed dividend');
r=item();r.futureBook.fQuoteAt=new Date(now-121000).toISOString();r.chain[0].pBid=201;
assert.equal(web.selectOptionPricing(r).model,'black76'); // Locked/narrow valid books still permitted.
r.chain[0].pBid=203;assert.equal(web.selectOptionPricing(r).model,'bsm','Crossed book cannot set parity');
r.spotQuoteAt=new Date(now-121000).toISOString();assert.equal(web.selectOptionPricing(r).model,null);
r=item();r.dte=0;assert.equal(web.selectOptionPricing(r).model,null);
r=item();r.dte=.01;web.enrichItemWithBlack76(r);assert(r.modelAtmIv>0,'Sub-day time is preserved');
for (const call of [true,false]) {
  const S=25000,K=25200,T=30/365,rate=.058,q=.0142,vol=.22,F=S*Math.exp((rate-q)*T);
  const g=web.bsmGreeks(S,K,T,rate,q,vol,call);
  assert(Math.abs(g.price-web.black76Price(F,K,T,rate,vol,call))<.001,'BSM/Black equivalence');
  assert(Math.abs(web.bsmIvDec(g.price,S,K,T,rate,q,call)-vol)<1e-6,'BSM inversion');
  const delta=(web.bsmGreeks(S+.1,K,T,rate,q,vol,call).price-web.bsmGreeks(S-.1,K,T,rate,q,vol,call).price)/.2;
  assert(Math.abs(g.delta-delta)<1e-5,'Dividend-adjusted spot delta: '+g.delta+' vs '+delta);
}
// Re-rendering must retain an interpolated 25-delta skew for both option sides.
r=item();delete r.futureBook;
r.chain=Array.from({length:21},(_,i)=>{
  const k=24500+50*i,node={...r.chain[0],k};
  for(const [prefix,call] of [['c',true],['p',false]]) {
    const px=web.black76Price(25000,k,r.dte/365,r.rate,.20,call);
    node[prefix+'Bid']=px-.05;node[prefix+'Ask']=px+.05;
  }
  return node;
});
web.enrichItemWithBlack76(r);assert(Math.abs(r.rev25)<1e-5);assert(Math.abs(r.bfly25)<1e-5);
web.enrichItemWithBlack76(r);assert(Number.isFinite(r.rev25),'Re-render must not discard skew');
const source={schema:1,rate:{source:'https://www.rbi.org.in/',sha256:'fixture',checked_at:new Date(now).toISOString(),model_rate:.06},
  dividends:{NIFTY:{source:'https://www.niftyindices.com/Factsheet/ind_nifty50.pdf',sha256:'fixture',as_of:'2026-10-30',model_yield:.0142}}};
web.pricingInputs=clone(source);assert.equal(web.currentPricingInputs('NIFTY',now).rate,.06);
web.pricingInputs.rate.checked_at=new Date(now-8*86400000).toISOString();
assert(Number.isNaN(web.currentPricingInputs('NIFTY',now).rate));
web.pricingInputs=clone(source);delete web.pricingInputs.rate.sha256;
assert(Number.isNaN(web.currentPricingInputs('NIFTY',now).rate));
web.pricingInputs=clone(source);web.pricingInputs.dividends.NIFTY.as_of='2026-01-01';
assert(Number.isNaN(web.currentPricingInputs('NIFTY',now).dividend));
web.pricingInputs.dividends.NIFTY.as_of='2026-12-01';
assert(Number.isNaN(web.currentPricingInputs('NIFTY',now).dividend));
console.log('Pricing policy passed: exact expiry, spread boundary, freshness, sources, BSM inversion/Greeks and immutable broker IV.');
