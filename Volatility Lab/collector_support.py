"""Shared atomic I/O, cross-platform file locking, and gzip helpers for Volatility Lab."""
from contextlib import contextmanager
from datetime import timezone, timedelta
from functools import wraps
from pathlib import Path
import ctypes
import gzip
import io
import json
import os
import sys
import tempfile
import time

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import fcntl
except ImportError:
    fcntl = None

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
STATE = DATA / 'collector_state'
CONFIG = ROOT / 'collector_config.json'
IST = timezone(timedelta(hours=5, minutes=30))


def read_json(path, default=None):
    fallback = {} if default is None else default
    p = Path(path)
    if not p.exists():
        return fallback
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return fallback


def atomic_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def atomic_csv(path, df):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            df.to_csv(f, index=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


@contextmanager
def data_lock(timeout=60.0):
    lock_path = DATA / 'data.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o666)
    f = os.fdopen(fd, 'r+b')
    if lock_path.stat().st_size == 0:
        f.write(b'0')
        f.flush()
    deadline = time.monotonic() + float(timeout)
    locked = False
    try:
        while True:
            try:
                f.seek(0)
                if msvcrt is not None:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                elif fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Timed out acquiring data.lock')
                time.sleep(0.1)
        yield
    finally:
        if locked:
            try:
                f.seek(0)
                if msvcrt is not None:
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                elif fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        f.close()


def serialized(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with data_lock():
            return fn(*args, **kwargs)
    return call


def config():
    return {'rate': 0.065, 'dividend': 0.0, 'max_spread': 3.0, 'phone_origin': '', 'allowed_login': '', **read_json(CONFIG)}


def protect(raw, decrypt=False):
    """DPAPI CurrentUser on Windows; passthrough on platforms without WinDLL/DPAPI."""
    if sys.platform != 'win32' or not hasattr(ctypes, 'WinDLL'):
        return raw
    try:
        from ctypes import wintypes
        class Blob(ctypes.Structure):
            _fields_ = [('length', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
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
            return raw
        try:
            return ctypes.string_at(dest.data, dest.length)
        finally:
            kernel.LocalFree(dest.data)
    except Exception:
        return raw
