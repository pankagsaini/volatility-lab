"""Background data worker pool; thread-safe client cloning and monotonic TTL staleness tracking."""
from concurrent.futures import ThreadPoolExecutor
from copy import copy
import logging
import time
import streamlit as st

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='lab-data')


def is_stale(state, key, ttl=60.0):
    last_ts = state.get('_background_ts_' + key)
    if last_ts is None:
        return True
    return (time.monotonic() - float(last_ts)) >= float(ttl)


def submit(state, key, fn, *args, **kwargs):
    slot = '_background_' + key
    if slot in state:
        return False
    state[slot] = _executor.submit(fn, *args, **kwargs)
    return True


def pending(state, key):
    return '_background_' + key in state


def take(state, key):
    ready = state.pop('_background_ready_' + key, None)
    if ready is not None:
        return ready
    return _collect(state, key)


def _collect(state, key):
    slot = '_background_' + key
    future = state.get(slot)
    if future is None or not future.done():
        return None
    state.pop(slot, None)
    try:
        res = future.result()
        state['_background_ts_' + key] = time.monotonic()
        return (True, res)
    except Exception as exc:
        logging.warning('Background refresh task %s failed with %s', key, type(exc).__name__)
        return (False, 'Refresh failed. Previous saved data retained; check Upstox connection and retry.')


def frozen_client(client):
    cloned = copy(client)
    if hasattr(client, 'headers') and isinstance(client.headers, dict):
        cloned.headers = dict(client.headers)
    if hasattr(client, 'session') and client.session is not None:
        try:
            import requests
            new_sess = requests.Session()
            new_sess.headers.update(client.session.headers)
            cloned.session = new_sess
        except Exception:
            pass
    return cloned


def status(key, on_tick=None):
    """Poll only this small control; never interrupt the visible workspace."""
    @st.fragment(run_every=2)
    def poll():
        completed = _collect(st.session_state, key)
        if completed is not None:
            st.session_state['_background_ready_' + key] = completed
        if on_tick is not None:
            on_tick()
        ready = st.session_state.get('_background_ready_' + key)
        with st.container(height=48, border=False):
            message, action = st.columns([4, 1])
            if ready is not None:
                message.caption('New snapshot available' if ready[0] else ready[1])
                if action.button('Show updates', key='_apply_' + key, width='stretch'):
                    st.rerun()
            elif pending(st.session_state, key):
                message.caption('Updating quotes in background')
            else:
                message.caption('Saved snapshot · updates appear when ready')
    poll()
