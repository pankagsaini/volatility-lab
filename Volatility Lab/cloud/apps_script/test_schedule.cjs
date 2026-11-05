const fs=require('fs'),vm=require('vm'),assert=require('assert'),crypto=require('crypto');
let now=Date.parse('2026-10-20T12:00:00+05:30');
class Clock extends Date{constructor(...a){super(...(a.length?a:[now]));}static now(){return now;}}
let props=new Map([['SCHEDULE_MODE','intraday'],['INTERVAL_MINUTES','1']]), captures=0,deleted=0,scans=0;
let receipt={schema:1,verified_at:'invalid',files:{}};
const archive={getFilesByName:()=>({hasNext:()=>true,next:()=>({getBlob:()=>({getDataAsString:()=>JSON.stringify(receipt)})})}),getFiles:()=>{scans++;let used=false;return {hasNext:()=>!used,next:()=>{used=true;return {getName:()=> '2026-09-01_snapshot_120000_complete.json.gz',getSize:()=>3,getBlob:()=>({getBytes:()=>[1,2,3]}),setTrashed:()=>deleted++}}};}};
const alertCalendar={getId:()=> 'primary',getEventsForDay:()=>[],getEvents:()=>[],createAllDayEvent:()=>({getId:()=> 'alert'})};
const c={console,Date:Clock,PropertiesService:{getScriptProperties:()=>({getProperty:k=>props.get(k)||null,setProperty:(k,v)=>props.set(k,v)})},LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock:()=>{}})},CalendarApp:{getDefaultCalendar:()=>alertCalendar,getCalendarById:()=>alertCalendar,getEventById:()=>null},Utilities:{DigestAlgorithm:{SHA_256:'sha256'},computeDigest:(alg,b)=>Array.from(crypto.createHash('sha256').update(Buffer.from(b)).digest()),formatDate:(d,z,f)=>{const local=new Date(d.getTime()+330*60000);return f==='yyyy-MM-dd'?local.toISOString().slice(0,10):f==='u'?String(local.getUTCDay()||7):f==='H'?String(local.getUTCHours()):String(local.getUTCMinutes());}},UrlFetchApp:{fetch:()=>({getResponseCode:()=>200,getContentText:()=>JSON.stringify({status:'success',data:[{date:'2026-10-20',holiday_type:'TRADING_HOLIDAY',closed_exchanges:['NFO'],open_exchanges:[]}]})})}};
vm.createContext(c);vm.runInContext(fs.readFileSync(__dirname+'/Code.gs','utf8'),c);
c.folder_=()=>archive;c.capture_=()=>{captures++;return {status:'complete'}};
c.scheduledCapture();assert.equal(captures,0);assert(props.get('COLLECTOR_STATUS').includes('closed'));
now+=86400000;c.scheduledCapture();assert.equal(captures,1);c.scheduledCapture();assert.equal(captures,1);
now+=60000;c.scheduledCapture();assert.equal(captures,2);
props.set('RUNTIME_BUDGET',JSON.stringify({day:'2026-10-21',ms:75*60000}));now+=60000;c.scheduledCapture();assert.equal(captures,2);
console.log('PASS: 1-minute live schedule, holiday guard, trading session slot dedup, next 1-min slot, runtime guard, permanent retention.');
