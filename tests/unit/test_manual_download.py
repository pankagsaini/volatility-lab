"""Historical index candles remain distinct from captured option snapshots."""
from types import SimpleNamespace

import pytest
from volatility_lab.ingestion import google_archive_sync, manual_download_session as download


@pytest.mark.parametrize('available', [False, True])
def test_index_download_and_archive_sync(tmp_path, monkeypatch, available):
    rows = [['2026-11-13T10:00:00+05:30', 100, 102, 99, 101, 0]] if available else []
    broker = SimpleNamespace(minute_candles=lambda key, day: {'candles': rows})
    monkeypatch.setattr(download, 'saved_client', lambda: broker)
    monkeypatch.setattr(download, 'DATA', tmp_path)
    monkeypatch.setattr(google_archive_sync, 'sync_once', lambda *args: 0)
    result = download.download_session('2026-11-13', verbose=False)
    assert result['saved_snapshots'] == 0
    assert result['minute_bars'] == (3 if available else 0)
    assert result['status'] == ('partial' if available else 'empty')
    assert result['archive_sync'] == {'status': 'completed', 'imported': 0}
