const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.resolve(__dirname,'..'),html=fs.readFileSync(path.join(root,'index.html'),'utf8');
const start=html.indexOf('      async function refreshDesk() {');
assert(start>=0);
const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{textContent:'',disabled:false,attributes:{},setAttribute(k,v){this.attributes[k]=v;}});return elements.get(id);};
const context=vm.createContext({Date,console,document:{getElementById:element},manualRefreshInFlight:false,lastCloudRefresh:{kind:'idle'}});
vm.runInContext(html.slice(start,html.indexOf('\n      }',start)+8),context);
async function verify(kind,forecastKind,expected) {
  context.syncLiveCloudSnapshot=async()=>{context.lastCloudRefresh={kind};return kind==='updated';};
  context.refreshPublishedForecasts=async()=>({kind:forecastKind});
  await context.refreshDesk();
  assert.match(element('refreshStatus').textContent,expected);
  assert.equal(element('syncDesk').disabled,false);
  assert.equal(element('syncDesk').attributes['aria-busy'],'false');
  assert.equal(element('refreshButtonLabel').textContent,'Refresh');
}
(async()=>{
 await verify('updated','unchanged',/Market data updated/);
 await verify('unchanged','unchanged',/No newer market capture available/);
 await verify('older','unchanged',/latest accepted snapshot retained/);
 await verify('unavailable','unchanged',/Market refresh failed/);
 await verify('unchanged','unavailable',/Forecast refresh unavailable/);
 await verify('unchanged','updated',/Forecasts updated/);
 let release,calls=0;
 context.syncLiveCloudSnapshot=()=>{calls++;return new Promise(resolve=>{release=resolve;});};
 const first=context.refreshDesk();
 assert.equal(element('syncDesk').disabled,true);
 assert.equal(element('syncDesk').attributes['aria-busy'],'true');
 assert.match(element('refreshStatus').textContent,/Checking/);
 await context.refreshDesk();assert.equal(calls,1,'Double clicks share one manual refresh');
 context.lastCloudRefresh={kind:'unavailable',timedOut:true};release(false);await first;
 assert.match(element('refreshStatus').textContent,/timed out/);
 context.syncLiveCloudSnapshot=async()=>{throw new Error('network failure');};
 await context.refreshDesk();assert.equal(element('syncDesk').disabled,false,'Rejected requests cannot leave the button stuck');
 assert.match(element('refreshStatus').textContent,/refresh (failed|timed out)/);
 let hiddenPanel=true,now=100000,marketCalls=0,closingCalls=0;
 const poll=vm.createContext({Date:{now:()=>now},document:{hidden:false,getElementById:()=>({classList:{contains:()=>hiddenPanel}})},
   getMarketSessionState:()=>({isOpen:true,day:'2026-11-19',mins:560}),TradingSessions:{session:()=>({endMins:930})},
   loadedCloudCapture:true,liveSnapshotRequest:null,loadPricingInputs:async()=>{},syncLiveCloudSnapshot:()=>marketCalls++,
   refreshClosingRisk:()=>closingCalls++});
 const pollStart=html.indexOf('      let lastMarketPoll');
 assert(pollStart>0);vm.runInContext(html.slice(pollStart,html.indexOf('      function initVolatilityLabDesk()',pollStart)),poll);
 for(let i=0;i<4;i++){now+=30000;poll.pollMarketDesk();}
 assert.equal(marketCalls,0);hiddenPanel=false;poll.document.hidden=true;poll.pollMarketDesk(true);await Promise.resolve();assert.equal(marketCalls,0);
 poll.document.hidden=false;poll.pollMarketDesk(true);poll.pollMarketDesk();await new Promise(r=>setImmediate(r));assert.equal(marketCalls,1);
 now+=30000;poll.liveSnapshotRequest={};poll.pollMarketDesk(true);await Promise.resolve();assert.equal(marketCalls,1);
 poll.liveSnapshotRequest=null;poll.pollMarketDesk();await new Promise(r=>setImmediate(r));assert.equal(marketCalls,2);
 poll.getMarketSessionState=()=>({isOpen:false,day:'2026-11-19',mins:940});hiddenPanel=true;poll.pollMarketDesk();assert.equal(closingCalls,0);
 hiddenPanel=false;poll.pollMarketDesk(true);poll.pollMarketDesk();assert.equal(closingCalls,1);
 now+=120000;poll.document.hidden=true;poll.pollMarketDesk();assert.equal(closingCalls,1);
 const css=fs.readFileSync(path.join(root,'scripts/desk-presentation.css'),'utf8');
 assert.equal(css,fs.readFileSync(path.join(root,'docs/scripts/desk-presentation.css'),'utf8'));
 console.log('Refresh UI passed: updated, unchanged, failure, timeout, forecast status and concurrent clicks.');
})().catch(error=>{console.error(error);process.exitCode=1;});
