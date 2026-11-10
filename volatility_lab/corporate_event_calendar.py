"""Official macro and constituent events used as volatility context."""
from __future__ import annotations

import csv
import html
import io
import json
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from market_data import IST

NSE_PAGE = 'https://www.nseindia.com/companies-listing/corporate-filings-event-calendar'
NSE_EVENT_API = 'https://www.nseindia.com/api/event-calendar'
INDEX_COMPONENTS = {
    'NIFTY': 'https://archives.nseindia.com/content/indices/ind_nifty50list.csv',
    'BANKNIFTY': 'https://archives.nseindia.com/content/indices/ind_niftybanklist.csv',
}
RBI_URL = 'https://www.rbi.org.in/Scripts/Annualpolicy.aspx'
MOSPI_API = 'https://www.mospi.gov.in/api/documents/get-latest-release-calender'
MOSPI_PAGE = 'https://www.mospi.gov.in/advance-release-calendar'
ECI_API = 'https://www.eci.gov.in/eci-backend/public/api'
ECI_PAGE = 'https://www.eci.gov.in/assembly-elections-new'
FED_URL = 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'
BLS_URL = 'https://www.bls.gov/schedule/news_release/cpi.htm'
SCHEMA = 3
CACHE_HOURS = 12
FIELDS = ('Event', 'Date', 'Category', 'Details', 'Timezone', 'Instrument', 'Source',
          'Company', 'Symbol', 'Purpose')
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36'
MONTHS = {name.lower(): number for number, name in enumerate(
    ('', 'January', 'February', 'March', 'April', 'May', 'June', 'July',
     'August', 'September', 'October', 'November', 'December')) if name}
MONTHS.update({name[:3].lower(): number for name, number in tuple(MONTHS.items())})
MONTHS['sept'] = 9
MONTH_RE = r'(January|February|March|April|May|June|July|August|September|October|November|December)'
DATE_RE = re.compile(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b', re.I)
EXPECTED_NETWORK_ERRORS = (HTTPError, URLError, TimeoutError, OSError, ValueError,
                           TypeError, UnicodeError, json.JSONDecodeError)


def _request(url, opener=None, headers=None, max_bytes=5_000_000):
    request_headers = {'User-Agent': USER_AGENT, 'Accept': '*/*'}
    if headers:
        request_headers.update(headers)
    request = Request(url, headers=request_headers)
    client = opener or build_opener()
    with client.open(request, timeout=15) as response:
        body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError('Calendar response exceeded the size limit.')
    return body


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        text = ' '.join(data.split())
        if text:
            self.parts.append(text)


def _page_text(payload):
    parser = _TextExtractor()
    parser.feed(payload.decode('utf-8', errors='replace'))
    return ' '.join(parser.parts)


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or '').strip()
    if not text:
        return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y',
                '%d %B %Y', '%d %b %Y', '%B %d, %Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).date()
    except ValueError:
        return None


def _event(day, name, category, details, timezone_name, source, symbol=''):
    return {'Event': name, 'Date': day.isoformat(), 'Category': category,
            'Details': str(details or '').strip(), 'Timezone': timezone_name,
            'Instrument': symbol, 'Source': source, 'Company': '', 'Symbol': '', 'Purpose': ''}


def _components(symbol):
    payload = _request(INDEX_COMPONENTS[symbol], headers={
        'Referer': 'https://www.nseindia.com/static/products-services/indices-nifty50-index'})
    reader = csv.DictReader(io.StringIO(payload.decode('utf-8-sig')))
    values = {str(row.get('Symbol', '')).strip().upper() for row in reader}
    values.discard('')
    if len(values) < (10 if symbol == 'NIFTY' else 5):
        raise ValueError('NSE constituent list was incomplete.')
    return values


def _nse_payload(start, end):
    client = build_opener(HTTPCookieProcessor())
    _request(NSE_PAGE + '?days=1month', opener=client,
             headers={'Accept': 'text/html,application/xhtml+xml'}, max_bytes=1_000_000)
    query = urlencode({'index': 'equities', 'from_date': start.strftime('%d-%m-%Y'),
                       'to_date': end.strftime('%d-%m-%Y')})
    body = _request(NSE_EVENT_API + '?' + query, opener=client, headers={
        'Accept': 'application/json, text/plain, */*', 'Referer': NSE_PAGE,
        'X-Requested-With': 'XMLHttpRequest'})
    return json.loads(body.decode('utf-8-sig'))


def _nse_rows(payload, symbol, components):
    rows = []
    for item in _walk(payload):
        ticker = str(item.get('symbol') or item.get('Symbol') or '').strip().upper()
        raw_day = item.get('date') or item.get('Date')
        day = None
        for fmt in ('%d-%b-%Y', '%d-%B-%Y', '%Y-%m-%d'):
            try:
                day = datetime.strptime(str(raw_day).strip(), fmt).date()
                break
            except (ValueError, TypeError):
                pass
        if not ticker or ticker not in components or day is None:
            continue
        company = str(item.get('company') or item.get('companyName') or item.get('Company') or ticker).strip()
        purpose = str(item.get('purpose') or item.get('Purpose') or item.get('event') or 'Corporate event').strip()
        details = str(item.get('bm_desc') or item.get('details') or item.get('Details') or '').strip()
        rows.append({'Event': f'{company} · {purpose}', 'Date': day.isoformat(),
            'Category': 'Company', 'Details': details, 'Timezone': 'Asia/Kolkata',
            'Instrument': symbol, 'Source': NSE_PAGE + '?symbol=' + ticker,
            'Company': company, 'Symbol': ticker, 'Purpose': purpose})
    unique = {(row['Date'], row['Symbol'], row['Purpose']): row for row in rows}
    return sorted(unique.values(), key=lambda row: (row['Date'], row['Company'], row['Purpose']))


def _rbi_events():
    text = _page_text(_request(RBI_URL))
    heading = re.search(r'Meeting Schedule of the Monetary Policy Committee', text, re.I)
    if not heading:
        raise ValueError('RBI MPC schedule heading was not found.')
    section = text[heading.end():heading.end() + 2400]
    events = []
    pattern = re.compile(rf'\b{MONTH_RE}\s+([0-9]{{1,2}}(?:\s*,\s*[0-9]{{1,2}})*(?:\s+and\s+[0-9]{{1,2}})?),?\s*(20[0-9]{{2}})', re.I)
    for match in pattern.finditer(section):
        days = [int(value) for value in re.findall(r'\d{1,2}', match.group(2))]
        try:
            day = date(int(match.group(3)), MONTHS[match.group(1).lower()], max(days))
        except (ValueError, KeyError):
            continue
        events.append(_event(day, 'RBI MPC decision', 'India policy',
            'Final scheduled day of the RBI Monetary Policy Committee meeting.',
            'Asia/Kolkata', RBI_URL))
    if not events:
        raise ValueError('No RBI MPC dates could be read.')
    return events


def _mospi_latest_pdf():
    payload = json.loads(_request(MOSPI_API).decode('utf-8-sig'))
    metadata = payload.get('data') if isinstance(payload, dict) else None
    candidates = list(_walk(metadata if metadata is not None else payload))
    pdf_url = next((str(row.get('url') or row.get('file_url') or row.get('document_url'))
                    for row in candidates if row.get('url') or row.get('file_url') or row.get('document_url')), '')
    if not pdf_url:
        raise ValueError('MoSPI did not publish a release-calendar PDF link.')
    if pdf_url.startswith('/'):
        pdf_url = 'https://www.mospi.gov.in' + pdf_url
    elif not pdf_url.startswith('http'):
        pdf_url = 'https://www.mospi.gov.in/' + pdf_url.lstrip('/')
    if '/uploads/' in pdf_url and pdf_url.startswith('https://www.mospi.gov.in/https://'):
        pdf_url = pdf_url.split('https://www.mospi.gov.in/', 1)[1]
    return pdf_url, _request(pdf_url, max_bytes=12_000_000)


def _mospi_events():
    from pypdf import PdfReader
    pdf_url, payload = _mospi_latest_pdf()
    reader = PdfReader(io.BytesIO(payload))
    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    fiscal = re.search(r'ADVANCE\s+RELEASE\s+CALENDAR\s*\(?\s*(20\d{2})\s*[-–/]\s*(\d{2,4})', text, re.I)
    if not fiscal:
        fiscal = re.search(r'ARC[_ -](20\d{2})[_ -](\d{2,4})', pdf_url, re.I)
    if not fiscal:
        raise ValueError('MoSPI calendar fiscal year was not found.')
    fiscal_start = int(fiscal.group(1))
    date_matches = list(DATE_RE.finditer(text))
    events = []
    keywords = (
        (('consumer price index', '(cpi)', ' cpi'), 'India CPI release'),
        (('gross domestic product', ' gdp'), 'India GDP release'),
        (('index of industrial production', ' iip'), 'India IIP release'),
    )
    for index, match in enumerate(date_matches):
        month = MONTHS.get(match.group(2).lower()[:4]) or MONTHS.get(match.group(2).lower()[:3])
        if not month:
            continue
        tail = text[match.end():date_matches[index + 1].start() if index + 1 < len(date_matches) else len(text)]
        normalized = ' '.join(tail.lower().split())
        match_event = next(((terms, label) for terms, label in keywords
                            if any(term in normalized for term in terms)), None)
        if not match_event:
            continue
        year = fiscal_start if month >= 4 else fiscal_start + 1
        try:
            day = date(year, month, int(match.group(1)))
        except ValueError:
            continue
        terms, label = match_event
        detail = next((line.strip() for line in tail.splitlines() if line.strip()), label)
        events.append(_event(day, label, 'India macro', detail[:240], 'Asia/Kolkata', pdf_url))
    unique = {(row['Date'], row['Event']): row for row in events}
    if not unique:
        raise ValueError('No MoSPI CPI, GDP, or IIP release dates could be read.')
    return sorted(unique.values(), key=lambda row: (row['Date'], row['Event']))


def _fed_events():
    text = _page_text(_request(FED_URL))
    section_re = re.compile(r'\b(20\d{2})\s+FOMC Meetings\b(.*?)(?=\b20\d{2}\s+FOMC Meetings\b|$)', re.I)
    meeting_re = re.compile(rf'\b{MONTH_RE}\s+(\d{{1,2}})\s*[-–]\s*(\d{{1,2}})\s*\*?', re.I)
    events = []
    for section in section_re.finditer(text):
        year = int(section.group(1))
        # The official page appends a note about the following year's tentative
        # dates to the last schedule; it must not inherit this section's year.
        schedule = re.split(r'\bNotes?:', section.group(2), maxsplit=1, flags=re.I)[0]
        for match in meeting_re.finditer(schedule):
            try:
                day = date(year, MONTHS[match.group(1).lower()], int(match.group(3)))
            except (ValueError, KeyError):
                continue
            events.append(_event(day, 'US Federal Reserve (FOMC) decision', 'Global policy',
                'Final scheduled day of the Federal Open Market Committee meeting.',
                'America/New_York', FED_URL))
    if not events:
        raise ValueError('No Fed meeting dates could be read.')
    return events


def _eci_date_values(value):
    if isinstance(value, list):
        for item in value:
            yield from _eci_date_values(item)
    elif value:
        text = str(value).strip()
        parsed = _date(text)
        if parsed:
            yield parsed
        else:
            for candidate in re.findall(r'\d{1,2}[/-]\d{1,2}[/-]20\d{2}|20\d{2}-\d{2}-\d{2}', text):
                parsed = _date(candidate)
                if parsed:
                    yield parsed


def _eci_events(start, end):
    events = []
    seen = set()
    years = range(start.year, end.year + 1)
    state_payload = json.loads(_request(ECI_API + '/get-ac-election-state'))
    state_map = state_payload.get('message', {}) if isinstance(state_payload, dict) else {}
    for year in years:
        year_states = state_map.get(str(year), {}) if isinstance(state_map, dict) else {}
        if isinstance(year_states, dict):
            year_rows = [(code, name) for code, name in year_states.items()]
        elif isinstance(year_states, list):
            year_rows = [(row.get('st_code') or row.get('state_code'),
                          row.get('state_name') or row.get('stateName') or 'Assembly election')
                         for row in year_states if isinstance(row, dict)]
        else:
            year_rows = []
        for code, state_name in year_rows:
            if not code:
                continue
            url = ECI_API + '/get-ac-election-details?' + urlencode({
                'iYear': year, 'st_code': code, 'election_id': 3})
            detail_payload = json.loads(_request(url))
            for detail in _walk(detail_payload):
                title = str(detail.get('election_name') or detail.get('electionName') or
                             detail.get('state_name') or detail.get('stateName') or state_name or 'Assembly election')
                phase = str(detail.get('phase') or detail.get('phase_name') or '').strip()
                suffix = f' · {phase}' if phase and phase.lower() not in title.lower() else ''
                for key, label in (('date_of_poll', 'Election polling'), ('date_of_counting', 'Election counting')):
                    for day in _eci_date_values(detail.get(key)):
                        if start <= day <= end and (day.isoformat(), label, title, phase) not in seen:
                            seen.add((day.isoformat(), label, title, phase))
                            events.append(_event(day, f'{label}: {title}{suffix}', 'Election',
                                f'Officially announced by the Election Commission of India. {phase}'.strip(),
                                'Asia/Kolkata', ECI_PAGE))
        bye_url = ECI_API + '/get-bye-election-details?' + urlencode({'iyear': year})
        bye_payload = json.loads(_request(bye_url))
        for detail in _walk(bye_payload):
            title = str(detail.get('election_name') or detail.get('electionName') or detail.get('state_name') or 'By-election')
            phase = str(detail.get('phase') or detail.get('phase_name') or '').strip()
            for key, label in (('date_of_poll', 'By-election polling'), ('date_of_counting', 'By-election counting')):
                for day in _eci_date_values(detail.get(key)):
                    if start <= day <= end and (day.isoformat(), label, title, phase) not in seen:
                        seen.add((day.isoformat(), label, title, phase))
                        events.append(_event(day, f'{label}: {title}', 'Election',
                            'Officially announced by the Election Commission of India.',
                            'Asia/Kolkata', ECI_PAGE))
    return events


def _bls_events():
    # Read the release-date cell, not the reference month or nearby page headings.
    class ReleaseRows(HTMLParser):
        def __init__(self):
            super().__init__(); self.rows=[]; self.cells=None; self.cell=None
        def handle_starttag(self,tag,attrs):
            if tag=='tr':self.cells=[]
            if tag in ('td','th') and self.cells is not None:self.cell=[]
        def handle_data(self,data):
            if self.cell is not None:self.cell.append(data)
        def handle_endtag(self,tag):
            if tag in ('td','th') and self.cell is not None:
                self.cells.append(' '.join(' '.join(self.cell).split()));self.cell=None
            if tag=='tr' and self.cells is not None:
                self.rows.append(self.cells);self.cells=None
    parser=ReleaseRows()
    parser.feed(_request(BLS_URL).decode('utf-8',errors='replace'))
    events = []
    for cells in parser.rows:
        if len(cells)<3:continue
        date_match = re.fullmatch(r'([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(20\d{2})',cells[1])
        if not date_match:
            continue
        try:
            day = date(int(date_match.group(3)), MONTHS[date_match.group(1).lower()], int(date_match.group(2)))
        except (ValueError, KeyError):
            continue
        events.append(_event(day, 'US CPI release', 'Global inflation',
            'US Consumer Price Index release date published by the Bureau of Labor Statistics.',
            'America/New_York', BLS_URL))
    unique = {(row['Date'], row['Event']): row for row in events}
    if not unique:
        raise ValueError('No US CPI release dates could be read.')
    return sorted(unique.values(), key=lambda row: row['Date'])


def _bls_published_fallback():
    # These dates were already published by BLS for the current schedule, but the
    # BLS host may reject automated requests. Do not extrapolate future release days.
    return [_event(date(2026, 11, 10), 'US CPI release', 'Global inflation',
                   'Published BLS release date; the live calendar could not be refreshed.',
                   'America/New_York', BLS_URL),
            _event(date(2026, 12, 10), 'US CPI release', 'Global inflation',
                   'Published BLS release date; the live calendar could not be refreshed.',
                   'America/New_York', BLS_URL)]


def _read_cache(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _write_cache(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    temporary.replace(path)


def _cached_source(cache, source, fetcher, now):
    saved = cache.get('sources', {}).get(source, {})
    stamp = None
    try:
        stamp = datetime.fromisoformat(str(saved.get('updated_at', '')).replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, AttributeError):
        stamp = None
    age = (now - stamp.astimezone(timezone.utc)).total_seconds() if stamp else None
    if age is not None and 0 <= age < CACHE_HOURS * 3600 and isinstance(saved.get('events'), list):
        return saved['events'], 'cached', None
    try:
        attempt=datetime.fromisoformat(saved.get('attempted_at',''))
        if saved.get('error') and 0<=(now-attempt).total_seconds()<3600:
            return saved.get('events',[]), 'stale', saved['error']
    except (ValueError,TypeError):pass
    try:
        events = fetcher()
        if not isinstance(events, list):
            raise ValueError('Calendar source returned invalid events.')
        cache.setdefault('sources', {})[source] = {
            'updated_at': now.isoformat(), 'events': events}
        return events, 'updated', None
    except EXPECTED_NETWORK_ERRORS as error:
        if isinstance(saved.get('events'), list) and saved['events']:
            events,state,message=saved['events'],'stale',f'{source} refresh failed; using saved dates'
        elif source == 'bls':
            events,state,message=_bls_published_fallback(),'fallback','BLS refresh unavailable; showing published dates through Dec 2026'
        else:
            events,state,message=[],'unavailable',f'{source} unavailable ({type(error).__name__})'
        cache.setdefault('sources',{})[source]={**saved,'attempted_at':now.isoformat(),
                                              'events':events,'error':message}
        return events,state,message


def _filter(events, start, end):
    filtered = []
    for item in events:
        if not isinstance(item, dict):
            continue
        day = _date(item.get('Date'))
        if day is None or not start <= day <= end:
            continue
        normalized = {key: item.get(key, '') for key in FIELDS}
        if not normalized['Category'] and (normalized['Company'] or normalized['Symbol']):
            normalized['Category'] = 'Company'
        filtered.append(normalized)
    return filtered


def _legacy_nse_events(root, symbol, start, end, now):
    path = Path(root) / 'nse_event_calendar_cache.json'
    cache = _read_cache(path)
    entry = cache.get('indices', {}).get(symbol, {})
    try:
        attempt=datetime.fromisoformat(entry.get('attempted_at',''))
        if entry.get('error') and 0<=(now-attempt).total_seconds()<3600:
            return entry.get('events',[]),'stale',entry['error']
    except (ValueError,TypeError):pass
    try:
        cached_start = date.fromisoformat(entry.get('start', ''))
        cached_end = date.fromisoformat(entry.get('end', ''))
        stamp = datetime.fromisoformat(entry.get('updated_at', '').replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        fresh = 0 <= (now - stamp.astimezone(timezone.utc)).total_seconds() < CACHE_HOURS * 3600
        if fresh and cached_start <= start and cached_end >= end:
            return entry.get('events', []), 'cached', None
    except (ValueError, TypeError, AttributeError):
        pass
    try:
        constituents = _components(symbol)
        raw = _nse_payload(start, end)
        events = _nse_rows(raw, symbol, constituents)
        cache.setdefault('indices', {})[symbol] = {
            'start': start.isoformat(), 'end': end.isoformat(), 'updated_at': now.isoformat(),
            'constituents': len(constituents), 'events': events}
        _write_cache(path, cache)
        return events, 'updated', None
    except EXPECTED_NETWORK_ERRORS as error:
        message=('Bank results could not refresh; using saved dates' if entry.get('events')
                 else f'Bank results unavailable ({type(error).__name__})')
        cache.setdefault('indices',{})[symbol]={**entry,'attempted_at':now.isoformat(),'error':message}
        try:_write_cache(path,cache)
        except OSError:pass
        return entry.get('events',[]),'stale' if entry.get('events') else 'unavailable',message


def _bank_results(events):
    """NSE-verified BANKNIFTY constituent results, including scheduled results meetings."""
    result=[]
    for row in events:
        if row.get('Instrument')!='BANKNIFTY' or not row.get('Symbol'):continue
        text=' '.join(str(row.get(k,'')).lower() for k in ('Purpose','Details'))
        if not re.search(r'\b(?:financial|quarterly|annual|audited|unaudited|half.yearly)\s+results\b',text):continue
        result.append({**row,'Category':'Bank results',
                       'Event':f"{row.get('Company') or row['Symbol']} · results meeting"})
    return result


def _index_macro_events(events):
    """Small scheduled-event set with plausible index-wide rates/growth exposure."""
    categories={'India policy','Global policy','Global inflation'}
    return [row for row in events if row.get('Category') in categories or
            (row.get('Category')=='India macro' and row.get('Event') in
             ('India CPI release','India GDP release','India IIP release'))]


def automatic_events(root, symbol, start, end):
    """Return index macro events and, for BANKNIFTY only, constituent results.

    Network failures are isolated by provider; last successful values are reused from
    disk when available. Returns ``(events, status)`` for the dashboard.
    """
    symbol = str(symbol or '').upper()
    if symbol not in INDEX_COMPONENTS:
        raise ValueError('Unsupported index for event calendar.')
    start, end = _date(start), _date(end)
    if start is None or end is None:
        raise ValueError('Event calendar dates are invalid.')
    if end < start:
        raise ValueError('Event calendar end must be on or after its start.')
    now = datetime.now(timezone.utc)
    root = Path(root)
    errors = []
    all_events = []
    company_state='not requested'
    if symbol=='BANKNIFTY':
        company_events, company_state, error = _legacy_nse_events(root, symbol, start, end, now)
        all_events.extend(_bank_results(company_events))
        if error:errors.append(error)

    cache_path = root / 'macro_event_calendar_cache.json'
    cache = _read_cache(cache_path)
    if cache.get('schema') != SCHEMA:
        cache = {'schema': SCHEMA, 'sources': {}}
    providers = (
        ('rbi', _rbi_events), ('mospi', _mospi_events),
        ('fed', _fed_events), ('bls', _bls_events),
    )
    provider_results = []
    for source, fetcher in providers:
        events, state, error = _cached_source(cache, source, fetcher, now)
        provider_results.append((source, state, error))
        all_events.extend(_index_macro_events(events))
        if error:
            errors.append(error)
    try:
        _write_cache(cache_path, cache)
    except OSError:
        errors.append('Calendar cache could not be saved')

    all_events = _filter(all_events, start, end)
    unique = {}
    for item in all_events:
        key = (item['Date'], item['Event'], item['Category'], item['Source'])
        unique[key] = item
    events = sorted(unique.values(), key=lambda item: (item['Date'], item['Category'], item['Event']))
    states = [company_state]
    states.extend(state for source, state, _error in provider_results)
    if errors:
        status = 'Partly available · ' + '; '.join(dict.fromkeys(errors))
    else:
        status = 'Official calendars refreshed' if 'updated' in states else 'Official calendars up to date'
    return events, status
