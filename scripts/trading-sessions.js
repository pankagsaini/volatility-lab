// NSE equity derivatives calendar. The collector refreshes the current year's holidays from Upstox.
const TradingSessions = (() => {
  const holidays = new Set(["2025-02-26", "2025-03-14", "2025-03-31", "2025-04-10", "2025-04-14", "2025-04-18", "2025-05-01", "2025-08-15", "2025-08-27", "2025-10-02", "2025-10-21", "2025-10-22", "2025-11-05", "2025-12-25", "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25"]);
  const special = new Map([
    ['2026-02-01', {startMins:555, endMins:930, label:'BUDGET'}],
    ['2026-11-08', {startMins:860, endMins:940, label:'MUHURAT'}],
    ['2025-10-21', {startMins:825, endMins:885, label:'MUHURAT'}]
  ]);
  function regularClose(day) {
    return day < '2026-08-03' ? 930 : day < '2026-12-07' ? 940 : 945;
  }
  function expiryMillis(day) {
    const close = day >= '2026-12-07' ? 930 : regularClose(day);
    return Date.parse(day + 'T00:00:00+05:30') + close * 60000;
  }
  function apply(calendar) {
    if (!calendar || !/^\d{4}$/.test(calendar.year) || !Array.isArray(calendar.closed)) return;
    const prefix = String(calendar.year) + '-';
    const valid = day => /^\d{4}-\d{2}-\d{2}$/.test(day) && day.startsWith(prefix);
    if (!calendar.closed.every(valid)) return;
    // Retain exchange-confirmed closures when a partial provider list omits a holiday.
    calendar.closed.forEach(day => holidays.add(day));
    for (const [day, window] of Object.entries(calendar.special || {})) {
      if (!valid(day) || !window) continue;
      const midnight = Date.parse(day + 'T00:00:00+05:30');
      const startMins = (Number(window.start_time) - midnight) / 60000;
      const endMins = (Number(window.end_time) - midnight) / 60000;
      if (startMins >= 0 && endMins > startMins && endMins < 1440)
        special.set(day, {startMins, endMins, label:'SPECIAL SESSION'});
    }
  }
  function session(day) {
    if (special.has(day)) return special.get(day);
    const weekday = new Date(day + 'T00:00:00Z').getUTCDay();
    return weekday > 0 && weekday < 6 && !holidays.has(day)
      ? {startMins:555, endMins:regularClose(day)} : null;
  }
  function history(rows) {
    if (!rows.length) return [];
    const values = new Map(rows.map(row => [row.d,row]));
    const result = [];
    for (let ts = Date.parse(rows[0].d); ts <= Date.parse(rows.at(-1).d); ts += 86400000) {
      const date = new Date(ts), day = date.toISOString().slice(0,10);
      if (session(day)) result.push(values.get(day) || {d:day, l:date.toLocaleDateString('en-GB',
        {day:'2-digit',month:'short',timeZone:'UTC'})});
    }
    return result;
  }
  return {apply, session, history, expiryMillis};
})();
