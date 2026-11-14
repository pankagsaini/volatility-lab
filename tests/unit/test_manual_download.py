"""Empty downloads and archive sync failures cannot masquerade as complete captures."""
import pytest
from volatility_lab.ingestion import google_archive_sync, manual_download_session as download


@pytest.mark.parametrize('response,status', [
    ({'status': 'empty', 'minutes': 0}, 'empty'),
    ({'error': 'Unauthorized'}, 'error'),
    ({'status': 'complete', 'saved_snapshots': 100, 'minutes': 100}, 'error'),
    ([], 'error'),
])
def test_empty_or_unknown_response_is_not_success(tmp_path, monkeypatch, response, status):
    monkeypatch.setattr(download, 'load_sync_config', lambda: {'source_folder': str(tmp_path), 'web_app_url': 'https://test'})
    monkeypatch.setattr(download, 'query_cloud_endpoint', lambda *args, **kwargs: response)
    calls = []
    monkeypatch.setattr(google_archive_sync, 'sync_once', lambda *args: calls.append(args) or 0)
    result = download.download_session('2026-11-13', verbose=False)
    assert result['status'] == status
    assert result['saved_snapshots'] == 0
    assert calls == [(None, None, 3.0)]


def test_fallback_unwrapped_candles_are_partial_and_sync_failure_is_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(download, 'load_sync_config', lambda: {'source_folder': str(tmp_path), 'web_app_url': 'https://test'})
    monkeypatch.setattr(download, 'DATA', tmp_path)
    def response(url, params, **kwargs):
        if params['action'] == 'manual_download':
            return {'error': 'Unavailable'}
        return {'candles': [['2026-11-13T10:00:00+05:30', 100, 102, 99, 101, 0]]}
    monkeypatch.setattr(download, 'query_cloud_endpoint', response)
    def fail(*args):
        raise OSError('private detail')
    monkeypatch.setattr(google_archive_sync, 'sync_once', fail)
    result = download.download_session('2026-11-13', verbose=False)
    assert result['status'] == 'partial' and result['minute_bars'] == 3
    assert result['archive_sync'] == {'status': 'error', 'imported': 0}
    assert result['saved_snapshots'] == 0
