"""The only Streamlit bootstrap; importing this module does not start the app."""
import gc
import os
from pathlib import Path
import sys

from volatility_lab import PACKAGE_ROOT

DASHBOARD_FILE = PACKAGE_ROOT / 'ui' / 'dashboard.py'
_CACHE_KEY = '_vol_lab_dashboard_bytecode'
_SCOPE_KEY = '_vol_lab_dashboard_scope'


def run():
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
    scope = getattr(sys, _SCOPE_KEY, None)
    if scope is None:
        scope = {}
        setattr(sys, _SCOPE_KEY, scope)
    scope.update(__file__=str(path), __name__='__main__', __doc__=None)
    exec(code, scope)
