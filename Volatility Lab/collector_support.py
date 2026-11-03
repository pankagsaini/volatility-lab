"""Private daily-collector state, Windows token protection and shared writer lock."""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import ctypes
from ctypes import wintypes
import json
import os
import threading
import time as clock
import uuid

from market_data import IST, MarketDataError

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / '.auth'
STATE = ROOT / 'data' / 'daily_collector'
CONFIG = ROOT / 'collector_config.json'
_mutex = threading.RLock()
_local = threading.local()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, allow_nan=False)
    for attempt in range(8):
        temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temp.write_text(text, encoding='utf-8')
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == 7: raise
            clock.sleep(min(.05 * 2**attempt, 1.))
        finally:
            temp.unlink(missing_ok=True)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {} if default is None else default


def atomic_csv(frame, path, **kwargs):
    path = Path(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        frame.to_csv(temp, **kwargs)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def config():
    return { 'rate': .065, 'dividend': 0., 'max_spread': 3.,
             'phone_origin': '', 'allowed_login': '', **read_json(CONFIG)}


@contextmanager
def data_lock(timeout=120):
    """Serialize UI and collector writers; lock releases after process failure."""
    import msvcrt
    with _mutex:
        if getattr(_local, 'held', False):
            yield
            return
        PRIVATE.mkdir(exist_ok=True)
        with (PRIVATE / 'writer.lock').open('a+b') as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            end = clock.monotonic() + timeout
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if clock.monotonic() >= end:
                        raise MarketDataError('Another data update is running. Please retry shortly.') from None
                    clock.sleep(.2)
            _local.held = True
            try:
                yield
            finally:
                _local.held = False
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def serialized(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with data_lock():
            return fn(*args, **kwargs)
    return call


class Blob(ctypes.Structure):
    _fields_ = [('length', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(raw, decrypt=False):
    """DPAPI CurrentUser: encrypted token works only for this Windows account."""
    buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, dest = Blob(len(raw), buf), Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(source), 'Volatility Lab daily session', None, None, None, 1, ctypes.byref(dest))
    if not ok:
        raise MarketDataError('Windows could not unlock the saved Upstox token. Please reconnect Upstox.')
    try:
        return ctypes.string_at(dest.data, dest.length)
    finally:
        kernel.LocalFree(dest.data)


