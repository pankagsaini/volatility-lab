"""Small saved-status caches; revisions always follow the files on disk."""
from datetime import datetime
from pathlib import Path
import gzip
import json
import streamlit as st
from market_data import IST


def _latest_capture(folder):
    return max(Path(folder).glob('*.json.gz'),key=lambda item:item.name,default=None)


@st.cache_data(show_spinner=False,max_entries=4)
def _capture_summary(path,revision):
    with gzip.open(path,'rt',encoding='utf-8') as handle:payload=json.load(handle)
    target=str(payload.get('target_day') or payload.get('day') or 'date unavailable')
    try:target=datetime.strptime(target,'%Y-%m-%d').strftime('%d %b')
    except ValueError:pass
    mode=str(payload.get('mode','capture')).upper()
    status=str(payload.get('status','unknown'))
    retrieved=datetime.fromisoformat(str(payload.get('retrieved_at','')).replace('Z','+00:00'))
    time_text=retrieved.astimezone(IST).strftime('%d %b %H:%M IST') if retrieved.tzinfo else 'time unavailable'
    return f'Latest saved Cloud capture · {mode} {status} for {target} · received {time_text}'


def latest_cloud_capture(root):
    folder=Path(root)/'data'/'google_archive'
    try:
        # Windows can delay directory mtime changes; enumerate filenames so a
        # just-synced capture cannot be hidden by a cached directory revision.
        path=_latest_capture(str(folder))
        if path is None:return None
        info=path.stat()
        return _capture_summary(str(path),(info.st_mtime_ns,info.st_size,info.st_ino))
    except FileNotFoundError:return None
    except (OSError,ValueError,TypeError):return 'Latest saved Cloud archive could not be read.'
