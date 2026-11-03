const fs=require('fs'),vm=require('vm'),assert=require('assert');
let now=Date.parse('2026-10-20T09:25:00+05:30');
class Clock extends Date{constructor(...a){super(...(a.length?a:[now]));}static now(){return now;}}
const props=new Map([['SCHEDULE_MODE','intraday']]),triggers=[],events=[];
const localDay=d=>new Date(d.getTime()+330*60000).toISOString().slice(0,10);
function makeEvent(title,date,description){
  const event={id:'event-'+(events.length+1),title,date:new Date(date),description,
    getId(){return this.id;},getTitle(){return this.title;},getDescription(){return this.description;},
    getAllDayStartDate(){return this.date;},setTitle(value){this.title=value;return this;},
    setDescription(value){this.description=value;return this;}};
  events.push(event);return event;
}
const calendar={getId:()=> 'primary',createAllDayEvent:(title,date,options)=>makeEvent(title,date,options.description),
  getEventsForDay:date=>events.filter(e=>localDay(e.date)===localDay(date)),
  getEvents:(start,end)=>events.filter(e=>localDay(e.date)>=localDay(start)&&localDay(e.date)<=localDay(end))};
const c={console,Date:Clock,
  PropertiesService:{getScriptProperties:()=>({getProperty:k=>props.has(k)?props.get(k):null,setProperty:(k,v)=>props.set(k,String(v)),deleteProperty:k=>props.delete(k)})},
  LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock:()=>{}})},
  CalendarApp:{getDefaultCalendar:()=>calendar,getCalendarById:()=>calendar,getEventById:id=>events.find(e=>e.id===id)||null},
  ScriptApp:{getProjectTriggers:()=>triggers,newTrigger:handler=>({handler,timeBased(){return this;},everyMinutes(minutes){this.minutes=minutes;return this;},create(){triggers.push({handler:this.handler,minutes:this.minutes,getHandlerFunction(){return this.handler;}});return this;}})},
  Utilities:{formatDate:(date,zone,format)=>{const d=new Date(date.getTime()+330*60000);if(format==='yyyy-MM-dd')return d.toISOString().slice(0,10);if(format==='u')return String(d.getUTCDay()||7);if(format==='H')return String(d.getUTCHours());if(format==='m')return String(d.getUTCMinutes());throw Error('Unexpected date format '+format);}},
  UrlFetchApp:{fetch:()=>({getResponseCode:()=>200,getContentText:()=>JSON.stringify({status:'success',data:[]})})}};
vm.createContext(c);vm.runInContext(fs.readFileSync(__dirname+'/Code.gs','utf8'),c);

c.installFailureWatchdog();c.installFailureWatchdog();
assert.equal(triggers.length,1);assert.equal(triggers[0].handler,'failureWatchdog');assert.equal(triggers[0].minutes,15);
assert.equal(props.get('ALERT_CALENDAR_ID'),'primary');

c.recordCollectorFailure_('No complete snapshot has succeeded.',new Date(now));
const originalId=events[0].id;
now+=3*60000;c.recordCollectorFailure_('No complete snapshot has succeeded.',new Date(now));
assert.equal(events.length,1);assert.equal(events[0].id,originalId);
assert.equal(events[0].title,'Volatility Lab collector needs attention');
assert(events[0].description.includes('VOLATILITY_LAB_COLLECTOR_ALERT:2026-10-20'));

now+=2*60000;c.resolveCollectorAlert_('Snapshot recovered.',new Date(now));
assert.equal(events[0].title,'Resolved · Volatility Lab collector');assert(!props.has('ACTIVE_ALERT'));
now+=60000;c.recordCollectorFailure_('A later failure.',new Date(now));
assert.equal(events.length,1);assert.equal(events[0].id,originalId);assert.equal(events[0].title,'Volatility Lab collector needs attention');

props.set('MARKET_CALENDAR',JSON.stringify({year:'2026',checked:'2026-10-20',closed:[]}));
props.delete('SUCCESS_SNAPSHOT_AT');c.failureWatchdog();
assert.equal(events.length,1);assert(props.has('ACTIVE_ALERT'));
props.set('SUCCESS_SNAPSHOT_AT',new Date(now+60000).toISOString());now+=2*60000;c.failureWatchdog();
assert.equal(events[0].title,'Resolved · Volatility Lab collector');assert(!props.has('ACTIVE_ALERT'));

events.length=0;props.delete('SUCCESS_SNAPSHOT_AT');
props.set('MARKET_CALENDAR',JSON.stringify({year:'2026',checked:'2026-10-20',closed:['2026-10-20']}));
c.failureWatchdog();assert.equal(events.length,0);

props.set('MARKET_CALENDAR',JSON.stringify({year:'2026',checked:'2026-10-20',closed:[]}));
now=Date.parse('2026-10-20T22:31:00+05:30');props.delete('SUCCESS_EOD');c.failureWatchdog();
assert.equal(events.length,1);assert(events[0].description.includes('End-of-day candle capture'));

events.length=0;props.set('SCHEDULE_MODE','paused');c.failureWatchdog();assert.equal(events.length,0);
c.resolveCollectorAlert_('Test cleanup.',new Date(now));events.length=0;props.set('SCHEDULE_MODE','intraday');
now=Date.parse('2026-10-20T10:00:00+05:30');
props.set('MARKET_CALENDAR',JSON.stringify({year:'2026',checked:'2026-10-20',closed:[]}));
props.set('RUNTIME_BUDGET',JSON.stringify({day:'2026-10-20',ms:0}));
c.capture_=()=>({status:'partial',errors:[{item:'quotes',reason:'Broker HTTP 500'}],pending:[]});
assert.throws(()=>c.scheduledCapture(),/Capture incomplete/);
assert.equal(events.length,1);assert(events[0].description.includes('Broker HTTP 500'));
console.log('PASS: idempotent watchdog trigger, one all-day alert per day, repeated-failure dedup, recovery resolution, market holiday, EOD-missing, paused-mode and immediate capture-failure guards');
