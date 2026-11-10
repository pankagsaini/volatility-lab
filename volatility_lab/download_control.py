"""Manual Windows launcher for the existing resumable minute downloader."""
from pathlib import Path
import argparse
import json
try:
    import msvcrt
except ImportError:
    msvcrt = None
import subprocess
import time
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parent


def running(root=ROOT):
    """The downloader's OS lock, not a stale JSON status, determines liveness."""
    path=Path(root)/'data'/'backtest_1minute'/'download.lock'
    if not path.exists() or msvcrt is None:return False
    with path.open('r+b') as handle:
        handle.seek(0)
        try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError as error:
            if error.errno in (13,36) or getattr(error,'winerror',None) in (32,33):return True
            raise
        else:
            handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            return False


def read_status(root=ROOT):
    try:return json.loads((Path(root)/'data'/'backtest_1minute'/'status.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}


def status_lines(root=ROOT):
    active=running(root);saved=read_status(root)
    lines=['Download is RUNNING in the background.' if active else 'Download is NOT running.']
    counts=saved.get('jobs') or {}
    if counts:
        lines.append(f"Completed requests: {counts.get('done',0):,} | Pending: {counts.get('pending',0):,} | Retries: {counts.get('retry',0):,} | Failed: {counts.get('failed',0):,}")
    if saved.get('updated'):lines.append('Last progress: '+str(saved['updated']))
    if not active and saved.get('status')=='finished_available_requests':lines.append('Download complete: all available queued requests have finished.')
    if not active and saved.get('status')=='finished_with_errors':lines.append('Download queue finished; failed requests need review.')
    if not active and saved.get('status')=='running':lines.append('The old running status was stale; the saved queue is safe to resume.')
    if saved.get('status')=='paused_low_disk':lines.append('At least 10 GB free disk space is required.')
    return lines


def start(root=ROOT,timeout=15):
    root=Path(root).resolve()
    if running(root):return 'Already running; no duplicate download was started.'
    python=root/'.runtime'/'pythonw.exe';script=root/'backtest_download.py'
    if not python.is_file() or not script.is_file():
        raise RuntimeError('Bundled Python or backtest_download.py is missing. Keep this launcher inside the Volatility Lab folder.')
    previous_updated=read_status(root).get('updated')
    logs=root/'logs';logs.mkdir(exist_ok=True)
    with (logs/'historical_download.log').open('a',encoding='utf-8') as log:
        log.write('\nManual start '+datetime.now(timezone.utc).isoformat()+'\n');log.flush()
        process=subprocess.Popen([str(python),str(script)],cwd=root,stdin=subprocess.DEVNULL,
            stdout=log,stderr=log,creationflags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP)
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if running(root):return 'Download started. You may close this window; the download will continue.'
        exit_code=process.poll()
        if exit_code is not None:
            saved=read_status(root);state=saved.get('status')
            fresh=bool(saved.get('updated')) and saved.get('updated')!=previous_updated
            if exit_code==0 and fresh and state in ('finished_available_requests','finished_with_errors'):
                return 'No queued work remains. '+('Some requests need review.' if state=='finished_with_errors' else 'Available requests are complete.')
            if exit_code==0 and fresh and state=='paused_low_disk':raise RuntimeError('Download paused: free at least 10 GB on the data drive, then run this launcher again.')
            raise RuntimeError('The downloader exited during startup. See logs/historical_download.log; the saved queue is retained.')
        time.sleep(.25)
    raise RuntimeError('Startup has not been confirmed yet. Run this launcher again to check; it will not duplicate an active download.')


def main():
    parser=argparse.ArgumentParser(description='Start or check the resumable historical download.')
    parser.add_argument('--status',action='store_true',help='Only check, without starting a download.')
    args=parser.parse_args()
    try:
        if not args.status:print(start())
        print('\n'.join(status_lines()))
        print('\nKeep the laptop awake and connected to the internet while downloading.')
        print('After a shutdown, double-click RESUME BACKTEST DOWNLOAD.cmd to continue pending work.')
        print('Log: '+str(ROOT/'logs'/'historical_download.log'))
        return 0
    except (OSError,RuntimeError) as error:
        print('Could not start or confirm the download: '+str(error));return 1


if __name__=='__main__':raise SystemExit(main())
