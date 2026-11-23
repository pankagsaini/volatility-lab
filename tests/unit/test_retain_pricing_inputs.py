from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from scripts import refresh_pricing_inputs as pricing
from volatility_lab.pricing.pricing_policy import recorded_inputs


def bundle(checked):
    data = json.loads((Path(__file__).resolve().parents[2] / 'docs/pricing-inputs.json').read_bytes())
    data['checked_at'] = checked
    for row in (data['rate'], *data['dividends'].values()):
        row['checked_at'] = checked
    for row in data['dividends'].values():
        row['as_of'] = '2026-10-30'
    return data


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 23, 10, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(pricing, 'datetime', Clock)


def test_source_outage_retains_live_bundle_with_original_checks_and_expiry(tmp_path, monkeypatch):
    output = tmp_path / 'pricing-inputs.json'
    output.write_text(json.dumps(bundle('2026-11-13T06:34:25+00:00')))
    previous = bundle('2026-11-23T09:05:17+00:00')

    def deployed(name, limit):
        assert name == 'pricing-inputs.json' and 0 < limit <= 65536
        return deepcopy(previous)

    monkeypatch.setattr(pricing, 'fetch_json', deployed)
    pricing.retain(output)
    saved = json.loads(output.read_bytes())
    assert saved == previous
    fields = {'rate_source': saved['rate'], 'dividend_source': saved['dividends']['NIFTY']}
    assert all(v is not None for v in recorded_inputs(fields, 'NIFTY', '2026-11-23T10:00:00Z'))
    assert recorded_inputs(fields, 'NIFTY', '2026-12-01T10:00:00Z')[0] is None


def test_prolonged_outage_preserves_an_expired_record_without_updating_its_age(tmp_path, monkeypatch):
    output = tmp_path / 'pricing-inputs.json'
    previous = bundle('2026-11-13T06:34:25+00:00')
    monkeypatch.setattr(pricing, 'fetch_json', lambda *a: previous)
    pricing.retain(output)
    saved = json.loads(output.read_bytes())
    assert saved == previous
    fields = {'rate_source': saved['rate'], 'dividend_source': saved['dividends']['NIFTY']}
    assert recorded_inputs(fields, 'NIFTY', '2026-11-23T10:00:00Z') == (None, .011399999999999999 if False else saved['dividends']['NIFTY']['model_yield'])


@pytest.mark.parametrize('regresses_one_factsheet', [True, False])
def test_keep_newer_complete_checkout_only_if_no_fact_date_regresses(tmp_path, monkeypatch, regresses_one_factsheet):
    previous = bundle('2026-11-13T06:34:25+00:00')
    checkout = bundle('2026-11-23T09:05:17+00:00')
    if regresses_one_factsheet:
        checkout['dividends']['BANKNIFTY']['as_of'] = '2026-10-29'
    monkeypatch.setattr(pricing, 'fetch_json', lambda *a: previous)
    output = tmp_path / 'pricing-inputs.json'
    output.write_text(json.dumps(checkout))
    pricing.retain(output)
    assert json.loads(output.read_bytes()) == (previous if regresses_one_factsheet else checkout)


@pytest.mark.parametrize(('path', 'bad'), [
    (('rate', 'source'), 'https://example.com/'),
    (('rate', 'sha256'), 'unverified'),
    (('rate', 'model_rate'), .12),
    (('dividends', 'NIFTY', 'yield_percent'), 12),
    (('dividends', 'BANKNIFTY', 'source'), 'https://www.niftyindices.com/Factsheet/ind_nifty50.pdf'),
    (('dividends', 'BANKNIFTY', 'checked_at'), '2026-11-24T10:00:00Z'),
    (('dividends', 'BANKNIFTY', 'as_of'), '2026-11-24'),
    (('checked_at',), '2027-01-01T12:00:00Z'),
])
def test_invalid_live_evidence_leaves_output_intact(path, bad, tmp_path, monkeypatch):
    output = tmp_path / 'pricing-inputs.json'
    output.write_bytes(b'previous checkout bytes')
    live = bundle('2026-11-23T09:05:17+00:00')
    item = live
    for part in path[:-1]:
        item = item[part]
    item[path[-1]] = bad
    monkeypatch.setattr(pricing, 'fetch_json', lambda *a: live)
    with pytest.raises(ValueError):
        pricing.retain(output)
    assert output.read_bytes() == b'previous checkout bytes'


def test_failed_recovery_leaves_existing_release_protected(tmp_path, monkeypatch):
    output = tmp_path / 'pricing-inputs.json'
    original = json.dumps(bundle('2026-11-13T06:34:25+00:00')).encode()
    output.write_bytes(original)

    def outage(*args):
        raise OSError('published artifact unavailable')

    monkeypatch.setattr(pricing, 'fetch_json', outage)
    with pytest.raises(OSError):
        pricing.retain(output)
    assert output.read_bytes() == original
