const fs=require('fs'),vm=require('vm'),assert=require('assert');
const triggers=[{handler:'failureWatchdog',getHandlerFunction(){return this.handler;}},{handler:'scheduledCapture',getHandlerFunction(){return this.handler;}}];
const c={
  console,
  ScriptApp:{
    getProjectTriggers:()=>triggers,
    deleteTrigger:t=>{const i=triggers.indexOf(t);if(i!==-1)triggers.splice(i,1);}
  }
};
vm.createContext(c);
vm.runInContext(fs.readFileSync(__dirname+'/Code.gs','utf8'),c);

assert.equal(typeof c.removeFailureWatchdogTrigger,'function');
assert.equal(typeof c.failureWatchdog,'undefined','failureWatchdog must be removed');
assert.equal(typeof c.installFailureWatchdog,'undefined','installFailureWatchdog must be removed');

c.removeFailureWatchdogTrigger();
assert.equal(triggers.length,1);
assert.equal(triggers[0].handler,'scheduledCapture');
console.log('PASS: failureWatchdog removed cleanly, trigger deleted, scheduledCapture preserved');
