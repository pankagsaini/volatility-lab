const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const codeContent = fs.readFileSync(__dirname + '/Code.gs', 'utf8');

// 1. Strict verification: ZERO watchdog references in the entire codebase
assert.equal(
  /watchdog/i.test(codeContent),
  false,
  'Code.gs must not contain any watchdog references or comments'
);

// 2. VM execution context
const triggers = [
  { handler: 'scheduledCapture', getHandlerFunction() { return this.handler; } }
];

const c = {
  console,
  ScriptApp: {
    getProjectTriggers: () => triggers,
    deleteTrigger: (t) => {
      const idx = triggers.indexOf(t);
      if (idx !== -1) triggers.splice(idx, 1);
    }
  }
};

vm.createContext(c);
vm.runInContext(codeContent, c);

// 3. Confirm watchdog functions are completely absent
assert.equal(typeof c.failureWatchdog, 'undefined', 'failureWatchdog must be removed');
assert.equal(typeof c.installFailureWatchdog, 'undefined', 'installFailureWatchdog must be removed');
assert.equal(typeof c.removeFailureWatchdogTrigger, 'undefined', 'removeFailureWatchdogTrigger must be removed');

// 4. Confirm core production capture functions exist
assert.equal(typeof c.scheduledCapture, 'function', 'scheduledCapture must be present');
assert.equal(typeof c.reconcilePriorSession, 'function', 'reconcilePriorSession must be present');
assert.equal(typeof c.weeklyCleanup, 'function', 'weeklyCleanup must be present');
assert.equal(typeof c.installIntradaySchedule, 'function', 'installIntradaySchedule must be present');
assert.equal(typeof c.installWeeklyCleanup, 'function', 'installWeeklyCleanup must be present');
assert.equal(typeof c.testCapture, 'function', 'testCapture must be present');
assert.equal(typeof c.verifyArchive, 'function', 'verifyArchive must be present');
assert.equal(typeof c.auditStatus, 'function', 'auditStatus must be present');

// 5. Preserved scheduledCapture trigger
assert.equal(triggers.length, 1);
assert.equal(triggers[0].handler, 'scheduledCapture');

console.log('PASS: Code.gs is 100% clean — zero watchdog code, triggers verified, core engine intact.');
