"""One background data worker; all Streamlit UI updates stay on the UI thread."""
from concurrent.futures import ThreadPoolExecutor
from copy import copy
import streamlit as st

_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='lab-data')

def submit(state,key,fn,*args,**kwargs):
    slot='_background_'+key
    if slot in state:return False
    state[slot]=_executor.submit(fn,*args,**kwargs)
    return True

def pending(state,key):
    return '_background_'+key in state

def take(state,key):
    ready=state.pop('_background_ready_'+key,None)
    if ready is not None:return ready
    return _collect(state,key)


def _collect(state,key):
    slot='_background_'+key
    future=state.get(slot)
    if future is None or not future.done():return None
    del state[slot]
    try:return (True,future.result())
    except Exception:
        # Provider error bodies / URLs must never expose authentication details.
        return (False,'Refresh failed. Previous saved data retained; check Upstox connection and retry.')

def frozen_client(client):
    return copy(client)

def status(key,on_tick=None):
    """Poll only this small control; never interrupt the visible workspace."""
    @st.fragment(run_every=2)
    def poll():
        completed=_collect(st.session_state,key)
        if completed is not None:st.session_state['_background_ready_'+key]=completed
        if on_tick is not None:on_tick()
        ready=st.session_state.get('_background_ready_'+key)
        if ready is not None:
            message,action=st.columns([4,1])
            message.caption('New snapshot available' if ready[0] else ready[1])
            if action.button('Show updates',key='_apply_'+key,width='stretch'):
                st.rerun()
        elif pending(st.session_state,key):
            st.caption('Updating quotes in background...')
    poll()
