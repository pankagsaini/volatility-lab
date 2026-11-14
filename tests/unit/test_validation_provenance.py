"""Validation receipts identify the models and conventions that actually ran."""
import hashlib
import json

import pandas as pd

from volatility_lab import PACKAGE_ROOT
from volatility_lab.research import run_model_validation as validation


def test_validation_receipt_hashes_implementation_and_actual_config(tmp_path, monkeypatch):
    from volatility_lab.ui import monitor_ui
    monkeypatch.setattr(validation, 'runtime_data_dir', lambda: tmp_path)
    monkeypatch.setattr(monitor_ui, 'full_price_history', lambda symbol: pd.DataFrame({
        'Date': pd.date_range('2025-11-11', periods=2), 'Close': [100., 101.]}))
    monkeypatch.setattr(validation, 'walk_forward', lambda *args, **kwargs: pd.DataFrame({
        'origin': ['2025-11-12'], 'matured': [False], 'status': ['ok']}))
    monkeypatch.setattr(validation, 'score', lambda *args, **kwargs: pd.DataFrame({
        'horizon': [5], 'model': ['EWMA'], 'sample': ['daily_overlapping'],
        'scored': [0], 'mae_pp': [None]}))
    folder, _ = validation.run('NIFTY')
    from pathlib import Path
    metadata = json.loads((Path(folder) / 'metadata.json').read_text())
    assert metadata['target'].endswith('245')
    assert metadata['config']['annualization'] == 245
    assert metadata['status'] == 'complete'
    for name, digest in metadata['code_sha256'].items():
        assert '/' in name
        assert hashlib.sha256((PACKAGE_ROOT / name).read_bytes()).hexdigest() == digest


def test_yang_zhang_baseline_uses_configured_annualization(monkeypatch):
    config = validation.ValidationConfig(horizons=(5,), annualization=252)
    monkeypatch.setattr(validation, 'forecast_origin', lambda *args: {})
    received = []
    def snapshot(training, horizon, ann_factor):
        received.append(ann_factor)
        return {'yang_zhang_rv': .2}
    monkeypatch.setattr(validation, 'rv_snapshot', snapshot)
    result = validation.predict(None, config)
    assert received == [252]
    assert result['Yang–Zhang baseline', 5][0] == .2 ** 2 / 252
