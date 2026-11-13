"""The only Streamlit bootstrap; importing this module does not start the app."""
import gc
import os
from pathlib import Path
import sys

from volatility_lab import PACKAGE_ROOT

DASHBOARD_FILE = PACKAGE_ROOT / 'ui' / 'dashboard.py'
_CACHE_KEY = '_vol_lab_dashboard_bytecode'
_REFERENCE_REVISION = None


def run():
    from .settings import load_settings
    from .events import configure
    settings = load_settings()
    configure(settings.log_level)
    from volatility_lab.storage.references import initialize_references
    global _REFERENCE_REVISION
    revision = (settings.data_dir, (PACKAGE_ROOT / 'data/reference-manifest.json').stat().st_mtime_ns)
    if revision != _REFERENCE_REVISION:
        initialize_references(settings.data_dir)
        _REFERENCE_REVISION = revision
    for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ.setdefault(key, '1')
    os.environ.setdefault('MALLOC_TRIM_THRESHOLD_', '65536')
    path = Path(DASHBOARD_FILE)
    revision = (str(path), path.stat().st_mtime_ns)
    cached = getattr(sys, _CACHE_KEY, None)
    if cached is None or cached[0] != revision:
        code = compile(path.read_text(encoding='utf-8'), str(path), 'exec')
        setattr(sys, _CACHE_KEY, (revision, code))
        gc.collect()
    else:
        code = cached[1]
    # Compiled code is process-safe. Dashboard globals can contain a session's data client.
    scope = {}
    scope.update(__file__=str(path), __name__='__main__', __doc__=None)
    exec(code, scope)
