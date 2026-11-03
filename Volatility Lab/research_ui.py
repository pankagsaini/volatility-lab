"""Optional research tools, separate from live pricing and production models."""
from datetime import datetime,timezone
import hashlib
import json
import numpy as np
import pandas as pd
import streamlit as st
from research_extensions import vol_of_vol,cross_index
from volatility_regime import regime_input


def table(frame):
    display=frame.copy();numeric=display.select_dtypes(include='number').columns
    display[numeric]=display[numeric].round(2)
    st.dataframe(display,hide_index=True,width='stretch',column_config={
        c:st.column_config.NumberColumn(c,format='%.2e' if display[c].abs().max()>1e6 else '%.2f') for c in numeric})


def render_research(root,symbol,selected):
    data=root/'data';as_of=selected.get('As of')
    if not as_of:
        st.info('Refresh quotes to establish the research valuation time.');return
    with st.expander('Volatility-of-volatility'):
        try:
            frame=pd.read_csv(regime_input(data,symbol))
            result=vol_of_vol(frame,as_of).dropna()
            table(result.tail(1))
            if not result.empty:
                st.line_chart(result.tail(126).set_index('Date')[['VoV20 · pp per session','VoV60 · pp per session']])
            st.caption('HV30 variability · 20 / 60 sessions · volatility pp per session')
        except (ValueError,OSError) as error:st.info(str(error))
    with st.expander('Cross-index correlation & return dispersion'):
        window=st.selectbox('Aligned return observations',[20,60,120,252],index=1)
        try:
            frames={name:pd.read_csv(regime_input(data,name)) for name in ['NIFTY','BANKNIFTY']}
            corr,dispersion=cross_index(frames,as_of,window)
            st.dataframe(corr.round(2),width='stretch')
            st.line_chart(dispersion.set_index('Date'))
            st.caption(f'{len(dispersion)} common return intervals · {dispersion.Date.min():%d %b %Y}–{dispersion.Date.max():%d %b %Y} · NIFTY / BANKNIFTY')
            st.download_button('Download aligned dispersion',dispersion.to_csv(index=False).encode(),file_name='cross_index_dispersion.csv')
        except (ValueError,OSError) as error:st.info(str(error))
    with st.expander('Experimental forecast validation'):
        st.caption('Research models · live forecasts unchanged')
        found=False
        for manifest in sorted((data/'experiments').glob('*/metadata.json'),reverse=True):
            meta=json.loads(manifest.read_text())
            if meta.get('status')!='complete' or meta['symbol']!=symbol:continue
            metrics=pd.read_csv(manifest.parent/'metrics.csv')
            sample=st.radio('Experimental sample',['nonoverlapping','daily_overlapping'],horizontal=True)
            subset=metrics[metrics['sample']==sample]
            table(subset[['horizon','model','scored','failed','qlike','rmse_pp','mae_pp','bias_pp']])
            if (subset.rmse_pp>1000).any():st.warning('A candidate produced numerically extreme forecasts. Scores retain these outcomes; do not use this candidate for live decisions.')
            st.caption('Lower scores are better · RMSE / MAE: volatility pp · exploratory results')
            st.download_button('Download pairwise model scores',(manifest.parent/'pairwise.csv').read_bytes(),file_name=f'{symbol}_model_comparisons.csv')
            found=True;break
        if not found:st.info('No completed candidate evaluation for this index yet.')
        if st.button('Run candidate validation for this index'):
            from run_experiments import run
            with st.spinner('Running rolling one-year forecasts; this may take a few minutes.'):
                run(root,symbol)
            st.success('Run saved. Reload saved data to view results.')
