"""Standalone Windows launcher. No Codex service or system Python required."""
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser

ROOT=Path(__file__).resolve().parent
URL='http://127.0.0.1:8501/'
EVENT='Local\\VolatilityLabStop_'+hashlib.sha256(str(ROOT).lower().encode()).hexdigest()[:20]
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.OpenEventW.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.LPCWSTR]
kernel.OpenEventW.restype=wintypes.HANDLE
kernel.CreateEventW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.BOOL,wintypes.LPCWSTR]
kernel.CreateEventW.restype=wintypes.HANDLE
kernel.CloseHandle.argtypes=[wintypes.HANDLE]
kernel.SetEvent.argtypes=[wintypes.HANDLE]
kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]


def running():
    handle=kernel.OpenEventW(0x00100000,False,EVENT)
    if handle:
        kernel.CloseHandle(handle)
        return True
    return False


def healthy():
    try:
        with urlopen(URL+'_stcore/health',timeout=2) as response:
            return response.status==200 and response.read().strip()==b'ok'
    except Exception:
        return False


def stop():
    handle=kernel.OpenEventW(0x0002,False,EVENT)
    if handle:
        kernel.SetEvent(handle)
        kernel.CloseHandle(handle)
        return True
    return False


def install_stop_listener():
    import threading
    handle=kernel.CreateEventW(None,True,False,EVENT)
    if not handle:
        raise OSError('Cannot create local application control event.')
    if ctypes.get_last_error()==183:
        kernel.CloseHandle(handle)
        raise RuntimeError('Dashboard is already starting or running.')
    def wait():
        kernel.WaitForSingleObject(handle,0xFFFFFFFF)
        os._exit(0)
    threading.Thread(target=wait,daemon=True).start()


def start(open_browser=True):
    if not running():
        if healthy():
            raise RuntimeError('Port 8501 is occupied by another server. Close that server and try again.')
        logs=ROOT/'logs'; logs.mkdir(exist_ok=True)
        with (logs/'dashboard.log').open('w',encoding='utf-8') as log:
            subprocess.Popen([str(ROOT/'.runtime/python.exe'),str(ROOT/'launch_dashboard.py')],
                             cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                             creationflags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP)
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        if healthy():
            if open_browser: webbrowser.open(URL)
            return
        time.sleep(.5)
    raise RuntimeError('Dashboard startup is taking too long. Check logs/dashboard.log in the app folder.')


if __name__=='__main__':
    try:
        if '--stop' in sys.argv:
            stop()
        else:
            start(open_browser='--no-browser' not in sys.argv)
    except Exception as exc:
        message=str(exc)
        if sys.stdout: print(message)
        else: ctypes.windll.user32.MessageBoxW(None,message,'Volatility Lab',0x10)
