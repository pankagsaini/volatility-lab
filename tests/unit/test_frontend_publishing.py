"""Calendar badges and public archive provenance must match canonical records."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from volatility_lab.ui.ui_theme import market_session_badge_html


def test_session_badge_uses_exchange_calendar():
    def badge(stamp):
        return market_session_badge_html(datetime.fromisoformat(stamp).replace(tzinfo=ZoneInfo('Asia/Kolkata')))
    assert 'CLOSED' in badge('2026-11-10T10:00:00')
    assert 'CLOSED' in badge('2026-11-08T10:00:00')
    assert 'LIVE · SPECIAL' in badge('2026-11-08T14:30:00')
    assert 'LIVE · REGULAR' in badge('2026-11-13T15:35:00')
    assert 'CLOSED' in badge('2026-11-13T15:40:00')
    assert 'PRE-OPEN' in badge('2026-11-13T09:05:00')
    assert 'calendar unavailable' in badge('2030-01-01T10:00:00')


def test_dated_public_archive_excludes_unverified_performance():
    from volatility_lab.storage.references import text_digest
    root = Path(__file__).resolve().parents[2]
    state = json.loads((root / 'docs/live_state.json').read_bytes())
    for name, digest in state['sources'].items():
        assert text_digest((root / 'volatility_lab/data' / name).read_bytes()) == digest
    assert state['ledger']['source_authority'] == 'dated_archive'
    assert state['ledger']['updated_at'].startswith('2026-11-11')
    assert state['ledger']['intraday_mtm_series'] == []
    for row in state['ledger']['closed_trades']:
        if row.get('integrity_status') == 'UNVERIFIED_RECORD':
            assert not {'gross', 'net', 'charges', 'id', 'legs'} & row.keys()
    for symbol in ['NIFTY', 'BANKNIFTY']:
        assert state['forecasts'][symbol]['training_through'] == '2026-11-11'
        assert len(state['forecasts'][symbol]['expiries']) > 1
        for row in state['forecasts'][symbol]['expiries']:
            assert row['as_of'].startswith(row['training_through'])
            assert 'iv' not in row


def test_ui_palette_has_no_gold():
    root = Path(__file__).resolve().parents[2]
    for path in [root / 'index.html', root / 'docs/index.html', *list((root / 'volatility_lab/ui').glob('*.py'))]:
        source = path.read_text(encoding='utf-8').lower()
        assert '#d4af37' not in source and '#eab308' not in source, path
