/** Private, read-only Upstox archive. No orders, no public web app, no model fitting. */
const VL = {zone:'Asia/Kolkata', indices:{NIFTY:'NSE_INDEX|Nifty 50',BANKNIFTY:'NSE_INDEX|Nifty Bank'}};
function props_(){return PropertiesService.getScriptProperties();}
function day_(d){return Utilities.formatDate(d,VL.zone,'yyyy-MM-dd');}
function minute_(d){return Number(Utilities.formatDate(d,VL.zone,'H'))*60+Number(Utilities.formatDate(d,VL.zone,'m'));}
function folder_(){
  const p=props_(); let id=p.getProperty('ARCHIVE_FOLDER_ID');
  if(!id){const f=DriveApp.createFolder('Volatility Lab - private Upstox archive');id=f.getId();p.setProperty('ARCHIVE_FOLDER_ID',id);}
  return DriveApp.getFolderById(id);
}
function api_(path,params){
  if(!/^\/v2\/(option\/(chain|contract)|market-quote\/quotes)$/.test(path)&&!path.startsWith('/v3/historical-candle/'))throw Error('Endpoint not allowed');
  const token=props_().getProperty('UPSTOX_TOKEN');if(!token)throw Error('Set UPSTOX_TOKEN in Script Properties');
  const query=Object.keys(params||{}).map(k=>encodeURIComponent(k)+'='+encodeURIComponent(params[k])).join('&');
  const url='https://api.upstox.com'+path+(query?'?'+query:'');
  for(let attempt=0;attempt<2;attempt++){
    const r=UrlFetchApp.fetch(url,{headers:{Authorization:'Bearer '+token,Accept:'application/json'},muteHttpExceptions:true,followRedirects:false});
    const code=r.getResponseCode();
    if(code===200){let b;try{b=JSON.parse(r.getContentText());}catch(e){throw Error('Invalid broker JSON');}
      if(b.status!=='success'||!b.data||(Array.isArray(b.data)&&!b.data.length)||(!Array.isArray(b.data)&&!Object.keys(b.data).length))throw Error('Empty broker data');return b.data;}
    if(attempt===0&&(code===429||code>=500)){Utilities.sleep(1000);continue;}
    throw Error('Broker HTTP '+code);
  }
}
function save_(name,value){
  const blob=Utilities.gzip(Utilities.newBlob(JSON.stringify(value),'application/json',name+'.json'));
  blob.setName(name+'.json.gz');return folder_().createFile(blob).getId();
}
function catalogue_(now){
  const p=props_(),day=day_(now);let cached=p.getProperty('CATALOGUE');
  if(cached){cached=JSON.parse(cached);if(cached.day===day)return cached;}
  const r=UrlFetchApp.fetch('https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz',{muteHttpExceptions:true,followRedirects:false});
  if(r.getResponseCode()!==200)throw Error('Instrument master unavailable');
  const master=JSON.parse(Utilities.ungzip(r.getBlob()).getDataAsString());const cat={day:day,indices:{}};
  for(const symbol of Object.keys(VL.indices)){
    const key=VL.indices[symbol];
    const futures=master.filter(x=>x.underlying_key===key&&x.instrument_type==='FUT').map(x=>({instrument_key:x.instrument_key,expiry:typeof x.expiry==='number'?day_(new Date(x.expiry)):String(x.expiry).slice(0,10),trading_symbol:x.trading_symbol})).filter(x=>x.expiry>=day).sort((a,b)=>a.expiry.localeCompare(b.expiry)).slice(0,3);
    if(futures.length!==3)throw Error('Three futures unavailable for '+symbol);
    const contracts=api_('/v2/option/contract',{instrument_key:key});
    let dates=[...new Set(contracts.map(x=>String(x.expiry).slice(0,10)))].filter(x=>x>=day&&x<=futures[2].expiry).sort();
    if(symbol==='BANKNIFTY')dates=dates.filter(x=>futures.some(f=>f.expiry===x));
    if(!dates.length)throw Error('No option expiries for '+symbol);
    cat.indices[symbol]={futures:futures,option_expiries:dates};
  }
  p.setProperty('CATALOGUE',JSON.stringify(cat));return cat;
}
function capture_(mode,recovery){
  const started=Date.now(),now=new Date(),day=day_(now),p=props_();
  const report={schema:1,provider:'Upstox',day:day,mode:mode,retrieved_at:now.toISOString(),status:'partial',records:[],errors:[],pending:[]};
  try{
    const cat=recovery?recovery.catalogue:catalogue_(now);report.catalogue=cat;
    const target=recovery?recovery.target_day:day;
    if(recovery){report.target_day=target;report.purpose='prior-session reconciliation';}
    const keys=Object.values(VL.indices).concat(['NSE_INDEX|India VIX']);
    Object.values(cat.indices).forEach(x=>x.futures.forEach(f=>keys.push(f.instrument_key)));
    const requests=recovery?[]:[{label:'quotes',path:'/v2/market-quote/quotes',params:{instrument_key:keys.join(',')}}];
    if(mode==='eod'){
      const from=day_(new Date(Date.parse(target+'T12:00:00+05:30')-10*86400000));
      keys.forEach(k=>requests.push({label:k,path:'/v3/historical-candle/'+encodeURIComponent(k)+'/days/1/'+target+'/'+from}));
    }else{
      Object.keys(cat.indices).forEach(s=>cat.indices[s].option_expiries.forEach(e=>requests.push({label:s+'|'+e,path:'/v2/option/chain',params:{instrument_key:VL.indices[s],expiry_date:e}})));
    }
    for(const req of requests){
      if(Date.now()-started>240000){report.errors.push({item:req.label,reason:'Runtime budget exhausted'});continue;}
      try{
        const data=api_(req.path,req.params);
        if(req.label==='quotes'){
          const returned=new Set(Object.values(data).map(q=>q.instrument_token));
          if(keys.some(k=>!returned.has(k)))throw Error('Incomplete quote response');
        }else if(req.path==='/v2/option/chain'&&data.some(r=>r.expiry!==req.params.expiry_date))throw Error('Wrong option expiry');
        report.records.push({label:req.label,retrieved_at:new Date().toISOString(),data:data});
        if(mode==='eod'&&req.label!=='quotes'&&!(data.candles||[]).some(c=>String(c[0]).slice(0,10)===target))report.pending.push(req.label);
      }catch(e){report.errors.push({item:req.label,reason:safeError_(e)});}
      Utilities.sleep(150);
    }
    report.status=report.errors.length||report.pending.length?'partial':'complete';
  }catch(e){report.errors.push({item:'catalogue',reason:safeError_(e)});}
  report.fetch_seconds=(Date.now()-started)/1000;
  const name=day+'_'+mode+'_'+Utilities.formatDate(now,VL.zone,'HHmmss')+'_'+report.status;
  const id=save_(name,report);report.file_id=id;report.total_seconds=(Date.now()-started)/1000;
  p.setProperty(recovery?'LAST_RECONCILIATION':'LAST_'+mode.toUpperCase(),JSON.stringify({day:day,target_day:report.target_day||day,status:report.status,file_id:id,seconds:report.total_seconds,errors:report.errors,pending:report.pending}));
  if(mode==='eod'){
    const pending=JSON.parse(p.getProperty('EOD_PENDING')||'{}'),target=report.target_day||day;
    if(report.status==='complete')delete pending[target];else if(report.catalogue)pending[target]=id;
    p.setProperty('EOD_PENDING',JSON.stringify(pending));
  }
  if(report.status==='complete'&&!recovery){
    p.setProperty('SUCCESS_'+mode.toUpperCase(),day);
    p.setProperty('SUCCESS_'+mode.toUpperCase()+'_AT',report.retrieved_at);
  }
  console.log(JSON.stringify({mode:mode,status:report.status,seconds:report.total_seconds,records:report.records.length,errors:report.errors,pending:report.pending,file_id:id}));
  return report;
}
function safeError_(e){const m=String(e.message||'');return /^(Broker HTTP \d{3}|Empty broker data|Invalid broker JSON|Incomplete quote response|Wrong option expiry|Instrument master unavailable|Three futures unavailable for (NIFTY|BANKNIFTY)|No option expiries for (NIFTY|BANKNIFTY)|Set UPSTOX_TOKEN in Script Properties)$/.test(m)?m:'Capture failed; check permissions, network or quota';}
function testCapture(){
  const lock=LockService.getScriptLock();if(!lock.tryLock(1000))throw Error('Capture already running');
  try{const result=capture_('test');if(result.status!=='complete')throw Error('Test incomplete; see sanitized execution log');}finally{lock.releaseLock();}
}
function verifyArchive(){
  const last=JSON.parse(props_().getProperty('LAST_TEST')||'null');
  if(!last)throw Error('Run testCapture first');
  const f=DriveApp.getFileById(last.file_id);
  const r=JSON.parse(Utilities.ungzip(f.getBlob()).getDataAsString());
  if(r.status!=='complete'||!r.records.length)throw Error('Archive verification failed');
  console.log(JSON.stringify({verified:true,records:r.records.length,bytes:f.getSize(),folder_url:folder_().getUrl(),sharing:String(folder_().getSharingAccess())}));
}
function installIntradaySchedule(){
  const p=props_();
  if(!p.getProperty('UPSTOX_TOKEN'))throw Error('Set UPSTOX_TOKEN first');
  if(!p.getProperty('SUCCESS_TEST'))throw Error('Run successful testCapture first');
  folder_();
  ScriptApp.getProjectTriggers().filter(t=>t.getHandlerFunction()==='scheduledCapture').forEach(t=>ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('scheduledCapture').timeBased().everyMinutes(1).create();
  p.setProperty('INTERVAL_MINUTES','3');
  p.setProperty('SCHEDULE_MODE','intraday');
  console.log('Enabled: three-minute snapshots, weekdays 09:00-16:00 IST; EOD retries 18:00-22:00 IST. Google trigger timing is approximate.');
}
function scheduledCapture(){
  const now=new Date(),day=day_(now),weekday=Number(Utilities.formatDate(now,VL.zone,'u')),mins=minute_(now),p=props_();
  if(p.getProperty('SCHEDULE_MODE')!=='intraday')return;
  if(mins>=420&&mins<540){reconcilePriorSession();return;}
  if(weekday>5)return;
  const mode=mins>=540&&mins<960?'snapshot':mins>=1080&&mins<1320?'eod':null;
  if(!mode)return;
  let closed;
  try{closed=marketClosed_(now);}catch(e){
    const message='Market calendar unavailable; capture paused.';
    p.setProperty('COLLECTOR_STATUS',message+' '+day);
    recordCollectorFailure_(message,now);
    throw e;
  }
  if(closed){p.setProperty('COLLECTOR_STATUS','Exchange closed '+day);return;}
  const lock=LockService.getScriptLock();if(!lock.tryLock(100))return;
  const started=Date.now();let attempted=false,failureMessage=null;
  try{
    const interval=Number(p.getProperty('INTERVAL_MINUTES')||3);
    const slot=day+'|'+Math.floor((mins-540)/interval);
    if(mode==='snapshot'&&p.getProperty('LAST_SNAPSHOT_SLOT')===slot)return;
    if(mode==='eod'&&(p.getProperty('SUCCESS_EOD')===day||now.getTime()-Number(p.getProperty('ATTEMPT_eod')||0)<30*60000))return;
    let budget=JSON.parse(p.getProperty('RUNTIME_BUDGET')||'{}');
    if(budget.day!==day)budget={day:day,ms:0};
    if(budget.ms>=55*60000){
      const message='Daily runtime safety limit reached.';
      p.setProperty('COLLECTOR_STATUS',message+' '+day);
      recordCollectorFailure_(message,now);
      return;
    }
    attempted=true;p.setProperty('ATTEMPT_'+mode,String(now.getTime()));
    const r=capture_(mode);
    if(mode==='snapshot'&&r.status==='complete')p.setProperty('LAST_SNAPSHOT_SLOT',slot);
    p.setProperty('COLLECTOR_STATUS',mode+' '+r.status+' '+new Date().toISOString());
    if(r.status!=='complete'){
      const details=(r.errors||[]).map(x=>x.reason).concat(r.pending||[]).filter(Boolean).slice(0,3).join('; ');
      failureMessage='Capture '+mode+' incomplete'+(details?': '+details:'');
      throw Error('Capture incomplete; sanitized details in execution log and Drive archive');
    }
    resolveCollectorAlert_('Collector recovered after a complete '+mode+' capture.',new Date());
  }catch(e){
    const message=failureMessage||safeError_(e);
    p.setProperty('COLLECTOR_STATUS','failed '+mode+' '+new Date().toISOString()+' · '+message);
    recordCollectorFailure_(message,new Date());
    throw e;
  }finally{
    if(attempted){let b=JSON.parse(p.getProperty('RUNTIME_BUDGET')||'{}');if(b.day!==day)b={day:day,ms:0};b.ms+=Date.now()-started+1000;p.setProperty('RUNTIME_BUDGET',JSON.stringify(b));}
    lock.releaseLock();
  }
}

// Runs through the existing one-minute trigger, before live capture (07:00–09:00 IST).
// Callable manually for recovery; the same lock, quota guard and 30-minute backoff apply.
function reconcilePriorSession(){
  const p=props_(),now=new Date(),day=day_(now),started=Date.now();
  const lock=LockService.getScriptLock();if(!lock.tryLock(100))return;
  let attempted=false;
  try{
    let pending=JSON.parse(p.getProperty('EOD_PENDING')||'{}');
    // Migrate the existing last EOD report without reading credentials or listing Drive.
    if(!p.getProperty('RECONCILIATION_MIGRATED')){
      const last=JSON.parse(p.getProperty('LAST_EOD')||'null');
      if(last&&last.status!=='complete'&&last.file_id)pending[last.day]=last.file_id;
      p.setProperty('EOD_PENDING',JSON.stringify(pending));p.setProperty('RECONCILIATION_MIGRATED','1');
    }
    const targets=Object.keys(pending).filter(d=>d<day).sort();
    if(!targets.length)return;
    if(now.getTime()-Number(p.getProperty('ATTEMPT_RECONCILIATION')||0)<30*60000)return;
    let budget=JSON.parse(p.getProperty('RUNTIME_BUDGET')||'{}');
    if(budget.day!==day)budget={day:day,ms:0};
    // Reserve most of the shared daily budget for live snapshots.
    let used=JSON.parse(p.getProperty('RECONCILIATION_BUDGET')||'{}');
    if(used.day!==day)used={day:day,ms:0};
    if(budget.ms>=55*60000||used.ms>=8*60000)return;
    attempted=true;p.setProperty('ATTEMPT_RECONCILIATION',String(now.getTime()));
    const target=targets[0],file=DriveApp.getFileById(pending[target]);
    const old=JSON.parse(Utilities.ungzip(file.getBlob()).getDataAsString());
    if(!old.catalogue||old.mode!=='eod')throw Error('Invalid reconciliation archive');
    const result=capture_('eod',{target_day:target,catalogue:old.catalogue});
    p.setProperty('RECONCILIATION_STATUS',target+' '+result.status+' '+new Date().toISOString());
    if(result.status==='complete')resolveCollectorAlert_('Prior-session data recovery completed.',new Date());
    else recordCollectorFailure_('Prior-session candle recovery is still incomplete.',new Date());
  }catch(e){
    const message=safeError_(e);
    p.setProperty('RECONCILIATION_STATUS','Retry pending: '+message);
    recordCollectorFailure_('Prior-session reconciliation failed: '+message,new Date());
    throw e;
  }
  finally{
    if(attempted)for(const key of ['RUNTIME_BUDGET','RECONCILIATION_BUDGET']){
      let b=JSON.parse(p.getProperty(key)||'{}');if(b.day!==day)b={day:day,ms:0};
      b.ms+=Date.now()-started+1000;p.setProperty(key,JSON.stringify(b));
    }
    lock.releaseLock();
  }
}

function alertCalendar_(){
  const id=props_().getProperty('ALERT_CALENDAR_ID');
  const calendar=id?CalendarApp.getCalendarById(id):CalendarApp.getDefaultCalendar();
  if(!calendar)throw Error('Alert calendar unavailable');
  return calendar;
}
function alertDate_(day){
  const parts=day.split('-').map(Number);
  return new Date(parts[0],parts[1]-1,parts[2],12,0,0);
}
function alertState_(){
  try{return JSON.parse(props_().getProperty('ACTIVE_ALERT')||'null');}catch(e){return null;}
}
function alertEventForDay_(calendar,day,state){
  if(state&&state.event_id){
    const byId=CalendarApp.getEventById(state.event_id);
    if(byId&&day_(byId.getAllDayStartDate())===day)return byId;
  }
  const marker='VOLATILITY_LAB_COLLECTOR_ALERT:'+day;
  return calendar.getEventsForDay(alertDate_(day)).find(e=>String(e.getDescription()||'').includes(marker))||null;
}
function recordCollectorFailure_(message,now){
  const p=props_(),stamp=now||new Date(),day=day_(stamp),label=String(message||'Collector failure').slice(0,300);
  const state=alertState_()||{first_seen:stamp.toISOString()};
  const previousDay=state.day,previousMessage=state.message,previousUpdate=Date.parse(state.calendar_updated||'');
  state.day=day;state.last_seen=stamp.toISOString();state.message=label;
  const marker='VOLATILITY_LAB_COLLECTOR_ALERT:'+day;
  const description=marker+'\nStatus: Needs attention\nFirst detected: '+state.first_seen+
    '\nLast detected: '+state.last_seen+'\nDetails: '+label+
    '\nOpen Apps Script Executions and the private Drive archive for capture details.';
  try{
    const calendar=alertCalendar_();
    let event=alertEventForDay_(calendar,day,state);
    if(!event){
      event=calendar.createAllDayEvent('Volatility Lab collector needs attention',alertDate_(day),{description:description});
      state.calendar_updated=stamp.toISOString();
    }else if(previousDay!==day||previousMessage!==label||!Number.isFinite(previousUpdate)||stamp.getTime()-previousUpdate>=60*60000){
      event.setTitle('Volatility Lab collector needs attention');event.setDescription(description);
      state.calendar_updated=stamp.toISOString();
    }
    state.event_id=event.getId();
    p.setProperty('ALERT_CALENDAR_ERROR','');
  }catch(e){
    p.setProperty('ALERT_CALENDAR_ERROR',safeError_(e));
    console.log(JSON.stringify({alert:'calendar_write_failed',reason:safeError_(e)}));
  }
  p.setProperty('ACTIVE_ALERT',JSON.stringify(state));
}
function resolveCollectorAlert_(message,now){
  const p=props_(),state=alertState_();if(!state)return;
  const stamp=now||new Date(),start=alertDate_(String(state.first_seen||state.day).slice(0,10));
  const end=alertDate_(day_(stamp));end.setDate(end.getDate()+1);
  try{
    const calendar=alertCalendar_(),events=calendar.getEvents(start,end);
    const marker='VOLATILITY_LAB_COLLECTOR_ALERT:';
    events.filter(e=>String(e.getDescription()||'').includes(marker)).forEach(e=>{
      const description=String(e.getDescription()||'').replace(/\nStatus: Needs attention/g,'\nStatus: Resolved')+
        '\nResolved at: '+stamp.toISOString()+'\nResolution: '+String(message||'Collection recovered.');
      e.setTitle('Resolved · Volatility Lab collector');e.setDescription(description);
    });
    p.deleteProperty('ACTIVE_ALERT');p.setProperty('ALERT_CALENDAR_ERROR','');
  }catch(e){
    p.setProperty('ALERT_CALENDAR_ERROR',safeError_(e));
    console.log(JSON.stringify({alert:'calendar_resolution_failed',reason:safeError_(e)}));
  }
}
function installFailureWatchdog(){
  const calendar=CalendarApp.getDefaultCalendar();
  if(!calendar)throw Error('Open Google Calendar and retry authorization.');
  const p=props_();p.setProperty('ALERT_CALENDAR_ID',calendar.getId());
  const installed=ScriptApp.getProjectTriggers().some(t=>t.getHandlerFunction()==='failureWatchdog');
  if(!installed)ScriptApp.newTrigger('failureWatchdog').timeBased().everyMinutes(15).create();
  console.log('Failure watchdog enabled: checks capture health every 15 minutes and keeps one all-day Calendar alert per failure day.');
}
function failureWatchdog(){
  const p=props_(),now=new Date(),day=day_(now),weekday=Number(Utilities.formatDate(now,VL.zone,'u')),mins=minute_(now);
  if(p.getProperty('SCHEDULE_MODE')!=='intraday'||weekday>5)return;
  let issue=null,closed;
  const lock=LockService.getScriptLock();if(!lock.tryLock(100))return;
  try{
    try{closed=marketClosed_(now);}catch(e){
      recordCollectorFailure_('Market calendar unavailable; trading-session health could not be checked.',now);return;
    }
    if(closed)return;
    if(mins>=560&&mins<960){
      const last=Date.parse(p.getProperty('SUCCESS_SNAPSHOT_AT')||'');
      if(!Number.isFinite(last)||day_(new Date(last))!==day)issue='No complete market snapshot has been recorded today.';
      else if(now.getTime()-last>20*60000)issue='No complete market snapshot has succeeded in the last 20 minutes.';
    }else if(mins>=960&&mins<=990){
      const last=Date.parse(p.getProperty('SUCCESS_SNAPSHOT_AT')||'');
      if(!Number.isFinite(last)||day_(new Date(last))!==day||now.getTime()-last>35*60000)
        issue='The final market snapshot appears to be missing.';
    }else if(mins>=1350&&p.getProperty('SUCCESS_EOD')!==day){
      issue='End-of-day candle capture has not completed by 22:30 IST.';
    }
    if(issue)recordCollectorFailure_(issue,now);
    else if(alertState_())resolveCollectorAlert_('Scheduled collection is healthy again.',now);
  }finally{lock.releaseLock();}
}

// Weekly cleanup requires a recent, independently verified laptop receipt.
function installWeeklyCleanup(){
  if(!ScriptApp.getProjectTriggers().some(t=>t.getHandlerFunction()==='weeklyCleanup'))
    ScriptApp.newTrigger('weeklyCleanup').timeBased().everyDays(1).atHour(20).inTimezone(VL.zone).create();
  console.log('Weekly cleanup enabled: keep 14 days; only checksum-verified local copies can move to Drive Trash. Large batches resume next day.');
}
function cleanupFiles_(folder,receipt,now){
  if(receipt.schema!==1||!receipt.files||typeof receipt.files!=='object'||!Number.isFinite(Date.parse(receipt.verified_at))||now-Date.parse(receipt.verified_at)>8*86400000||Date.parse(receipt.verified_at)>now){return {count:0,completed:false,reason:'receipt expired or invalid'};}
  const files=folder.getFiles();let count=0;
  while(files.hasNext()){
    if(Date.now()-now>200000)return {count:count,completed:false,reason:'batch limit'};
    const f=files.next(),name=f.getName();
    if(!/^\d{4}-\d{2}-\d{2}_(snapshot|test|eod)_\d{6}_(complete|partial)\.json\.gz$/.test(name))continue;
    const dated=Date.parse(name.slice(0,10)+'T23:59:59+05:30');
    if(!Number.isFinite(dated)||now-dated<14*86400000)continue;
    const r=receipt.files[name];if(!r||!Number.isSafeInteger(r.size)||r.size<=0||!/^[a-f0-9]{64}$/.test(r.sha256)||r.size!==f.getSize())continue;
    const digest=Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,f.getBlob().getBytes()).map(b=>('0'+((b+256)%256).toString(16)).slice(-2)).join('');
    if(digest!==r.sha256)continue;
    f.setTrashed(true);count++;
  }
  return {count:count,completed:true};
}
function weeklyCleanup(){
  const p=props_(),now=Date.now(),last=Number(p.getProperty('CLEANUP_COMPLETED')||0);
  if(now-last<7*86400000){console.log('Weekly cleanup not due yet');return;}
  const lock=LockService.getScriptLock();if(!lock.tryLock(100))return;
  try{
    const folder=folder_(),receipts=folder.getFilesByName('local_verified_receipts.json');
    if(!receipts.hasNext()){console.log('Cleanup skipped: laptop receipt not available');return;}
    const receipt=JSON.parse(receipts.next().getBlob().getDataAsString());
    const result=cleanupFiles_(folder,receipt,now);
    p.setProperty('LAST_CLEANUP',JSON.stringify({checked:new Date(now).toISOString(),...result}));
    if(result.completed)p.setProperty('CLEANUP_COMPLETED',String(now));
    console.log(JSON.stringify(result));
  }finally{lock.releaseLock();}
}
function testCleanupIsolation(){
  // Only this new disposable folder is passed to the production cleanup helper.
  const f=DriveApp.createFolder('Volatility Lab DISPOSABLE cleanup test '+new Date().toISOString());
  const content='cleanup-safe-test-2026-10-21',expected='a13f2b8298438f15fab24c23e7a9f34222b608c07a1c72ce64a5009a97abd616';
  const old=f.createFile('2026-09-01_test_120000_complete.json.gz',content);
  const recent=f.createFile(day_(new Date())+'_test_120000_complete.json.gz',content);
  const receipt={schema:1,verified_at:new Date().toISOString(),files:{}};
  const missing=cleanupFiles_(f,receipt,Date.now());if(old.isTrashed())throw Error('Missing receipt guard failed');
  receipt.files[old.getName()]={size:old.getSize(),sha256:'0'.repeat(64)};
  const mismatch=cleanupFiles_(f,receipt,Date.now());if(old.isTrashed())throw Error('Checksum guard failed');
  receipt.files[old.getName()].sha256=expected;
  receipt.files[recent.getName()]={size:recent.getSize(),sha256:expected};
  const eligible=cleanupFiles_(f,receipt,Date.now());
  console.log(JSON.stringify({eligible:eligible,old_trashed:DriveApp.getFileById(old.getId()).isTrashed(),recent_trashed:DriveApp.getFileById(recent.getId()).isTrashed()}));
  if(!DriveApp.getFileById(old.getId()).isTrashed()||DriveApp.getFileById(recent.getId()).isTrashed()||eligible.count!==1)throw Error('Retention test failed');
  console.log(JSON.stringify({test:'PASS',missing_receipt_retained:missing.count===0,wrong_checksum_retained:mismatch.count===0,verified_old_file_trashed:DriveApp.getFileById(old.getId()).isTrashed(),recent_file_retained:!DriveApp.getFileById(recent.getId()).isTrashed(),folder:f.getUrl(),market_files_touched:0}));
}

function marketClosed_(now){
  const p=props_(),day=day_(now),year=day.slice(0,4);
  let c=JSON.parse(p.getProperty('MARKET_CALENDAR')||'null');
  if(!c||c.checked!==day){
    try{
      const response=UrlFetchApp.fetch('https://api.upstox.com/v2/market/holidays',{headers:{Accept:'application/json',Authorization:'Bearer '+p.getProperty('UPSTOX_TOKEN')},muteHttpExceptions:true,followRedirects:false});
      if(response.getResponseCode()!==200)throw Error('Calendar unavailable');
      const body=JSON.parse(response.getContentText());
      if(body.status!=='success'||!Array.isArray(body.data)||!body.data.length)throw Error('Calendar unavailable');
      c={year:year,checked:day,closed:body.data.filter(r=>r.date.startsWith(year)&&r.holiday_type==='TRADING_HOLIDAY'&&(r.closed_exchanges||[]).includes('NFO')&&!(r.open_exchanges||[]).some(e=>e.exchange==='NFO')).map(r=>r.date)};
      p.setProperty('MARKET_CALENDAR',JSON.stringify(c));
    }catch(e){
      if(!c||c.year!==year||Date.parse(day)-Date.parse(c.checked)>7*86400000){p.setProperty('COLLECTOR_STATUS','Calendar unavailable; capture paused');throw Error('Market calendar unavailable; retry next trigger');}
    }
  }
  return c.closed.includes(day);
}
function auditStatus(){
  console.log(JSON.stringify({closed_today:marketClosed_(new Date()),sharing:String(folder_().getSharingAccess()),cleanup_completed:props_().getProperty('CLEANUP_COMPLETED'),last_cleanup:props_().getProperty('LAST_CLEANUP'),handlers:ScriptApp.getProjectTriggers().map(t=>t.getHandlerFunction()),interval:props_().getProperty('INTERVAL_MINUTES'),status:props_().getProperty('COLLECTOR_STATUS'),last_snapshot:JSON.parse(props_().getProperty('LAST_SNAPSHOT')||'null'),last_eod:JSON.parse(props_().getProperty('LAST_EOD')||'null')}));
}
