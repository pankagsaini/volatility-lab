"""Shared atomic file I/O, cross-process locking, DPAPI token protection, and validation helpers."""
from volatility_lab import PACKAGE_ROOT
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import ctypes
from ctypes import wintypes
import gzip
import io
import json
import os
import time
import uuid

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import fcntl
except ImportError:
    fcntl = None

ROOT = PACKAGE_ROOT
DATA = ROOT / 'data'
PRIVATE = DATA / 'private'
STATE = DATA / 'state'

IST = timezone(timedelta(hours=5, minutes=30))


def atomic_json(path, value):
    """Write JSON atomically using a unique temporary file and retry loop for Windows file locks."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.parent / f'.{target.name}.{uuid.uuid4().hex}.tmp'
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False).encode('utf-8')
    try:
        temp.write_bytes(payload)
        for attempt in range(8):
            try:
                os.replace(temp, target)
                return target
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(0.1 * 2**attempt, 2.0))
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return target


def read_json(path, default=None):
    """Safely read JSON file, returning {} (or explicit default) on missing or corrupt files."""
    fallback = {} if default is None else default
    target = Path(path)
    if not target.exists():
        return fallback
    for attempt in range(4):
        try:
            return json.loads(target.read_text(encoding='utf-8'))
        except PermissionError:
            if attempt == 3:
                return fallback
            time.sleep(0.03 * (attempt + 1))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return fallback
    return fallback


def atomic_csv(first, second, **kwargs):
    """Atomically write a pandas DataFrame to CSV (supports both (frame, path, **kw) and (path, frame, **kw))."""
    if hasattr(first, 'to_csv'):
        frame, path = first, second
    else:
        path, frame = first, second
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.parent / f'.{target.name}.{uuid.uuid4().hex}.tmp'
    if 'index' not in kwargs:
        kwargs['index'] = False
    try:
        frame.to_csv(temp, **kwargs)
        for attempt in range(8):
            try:
                os.replace(temp, target)
                return target
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(0.1 * 2**attempt, 2.0))
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return target


@contextmanager
def data_lock(timeout=120, lock_path=None):
    """Cross-process file lock using msvcrt on Windows or fcntl on POSIX with safe r+b descriptor opening."""
    path = Path(lock_path) if lock_path is not None else (PRIVATE / 'writer.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o666)
    handle = os.fdopen(fd, 'r+b')
    try:
        if path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
    except OSError:
        pass

    deadline = time.monotonic() + max(0.1, float(timeout))
    acquired = False
    try:
        while True:
            try:
                handle.seek(0)
                if msvcrt is not None:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                elif fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'Timed out acquiring lock: {path}') from error
                time.sleep(0.1)
        yield True
    finally:
        if acquired:
            try:
                handle.seek(0)
                if msvcrt is not None:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                elif fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            handle.close()
        except OSError:
            pass


@contextmanager
def serialized(name='writer', timeout=120):
    """Named cross-process lock context manager used by expiry_data.py and collector modules."""
    safe_name = str(name or "writer").replace("/", "_").replace(chr(92), "_")
    lock_file = PRIVATE / f'{safe_name}.lock' if not str(name).endswith('.lock') else Path(name)
    with data_lock(timeout=timeout, lock_path=lock_file):
        yield True


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ('cbData', wintypes.DWORD),
        ('pbData', ctypes.POINTER(ctypes.c_char)),
    ]


def protect(raw, decrypt=False):
    """Encrypt or decrypt bytes using Windows DPAPI (CryptProtectData / CryptUnprotectData)."""
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    if os.name != 'nt' or not hasattr(ctypes, 'windll'):
        return raw

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(raw, len(raw))
    blob_in = _DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()

    func = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    ok = func(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def unprotect(raw):
    """Decrypt bytes protected with Windows DPAPI."""
    return protect(raw, decrypt=True)


def decompress_gzip(raw_bytes, max_bytes=40 * 1024 * 1024):
    """Safely decompress gzip bytes with an explicit decompressed size cap."""
    with gzip.GzipFile(fileobj=io.BytesIO(raw_bytes), mode='rb') as gz:
        data = gz.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError('Decompressed gzip payload exceeds safety limit')
    return data


def validate_schema(obj):
    """Validate top-level Upstox cloud archive schema."""
    if not isinstance(obj, dict):
        raise ValueError('Archive payload must be a JSON object')
    if obj.get('schema') != 1 or obj.get('provider') != 'Upstox':
        raise ValueError('Unsupported archive schema or provider')
    if not isinstance(obj.get('records'), list):
        raise ValueError('Archive records must be a list')
    return obj


def parse_ist_timestamp(value):
    """Parse an ISO-8601 timestamp string into an IST-aware datetime object."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)
