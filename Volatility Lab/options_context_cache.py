"""Nonblocking, bounded event-calendar refresh for the options workspace."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Lock
from time import monotonic


def saved_events(root,symbol,start,end):
    """Read already published dates without contacting any calendar provider."""
    from corporate_event_calendar import _read_cache,_filter,_bank_results,_index_macro_events
    root=Path(root)
    macro=_read_cache(root/'macro_event_calendar_cache.json')
    rows=[]
    for source in ('rbi','mospi','fed','bls'):
        rows.extend(_index_macro_events(macro.get('sources',{}).get(source,{}).get('events',[])))
    if symbol=='BANKNIFTY':
        corporate=_read_cache(root/'nse_event_calendar_cache.json')
        rows.extend(_bank_results(corporate.get('indices',{}).get(symbol,{}).get('events',[])))
    rows=_filter(rows,start,end)
    unique={(row['Date'],row['Event'],row['Category'],row['Source']):row for row in rows}
    return sorted(unique.values(),key=lambda row:(row['Date'],row['Category'],row['Event']))


class CalendarCache:
    """One calendar worker; expired provider caches never block chart rendering."""
    def __init__(self,fetcher=None,clock=monotonic):
        if fetcher is None:
            from corporate_event_calendar import automatic_events
            fetcher=automatic_events
        self._fetch=fetcher
        self._clock=clock
        self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='lab-calendar')
        self._lock=Lock()
        self._entries={}

    def get(self,root,symbol,start,end):
        key=(str(Path(root)),symbol,start,end)
        with self._lock:
            now=self._clock()
            entry=self._entries.get(key)
            if entry is None:
                for old in list(self._entries):
                    if len(self._entries)<8:break
                    pending=self._entries[old]['future']
                    if pending is None or pending.done():del self._entries[old]
                if len(self._entries)>=8:
                    return saved_events(root,symbol,start,end),'Saved calendar · checking updates in background'
                entry={'value':(saved_events(root,symbol,start,end),'Saved calendar'),
                       'attempt':float('-inf'),'future':None}
                self._entries[key]=entry
            future=entry['future']
            if future is not None and future.done():
                try:entry['value']=future.result()
                except Exception:
                    entry['value']=(entry['value'][0],'Partly available · calendar refresh failed; saved dates retained')
                entry['future']=None
            if entry['future'] is None and now-entry['attempt']>=300:
                # Provider caches enforce their own 12-hour refresh and failure cooldown.
                entry['attempt']=now
                entry['future']=self._executor.submit(self._fetch,Path(root),symbol,start,end)
            rows,status=deepcopy(entry['value'])
            if entry['future'] is not None:status+=' · checking updates in background'
            return rows,status

    def close(self):
        self._executor.shutdown(wait=True,cancel_futures=True)
