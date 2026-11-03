"""Honest horizon comparison: past-window YZ versus forward variance models."""
import numpy as np
import pandas as pd
from forecast import fit_garch
from vol_lib import rv_snapshot


def model_tables(frame,horizons=(5,10,20,30,60)):
    forecasts,diag,_,_=fit_garch(frame,horizons=horizons,min_observations=200)
    annual_ewma=rv_snapshot(frame,20)['ewma_next_session_vol']*100
    forecasts['GARCH period %']=forecasts['GARCH forecast %']*np.sqrt(forecasts.Sessions/252)
    forecasts['EWMA period %']=annual_ewma*np.sqrt(forecasts.Sessions/252)
    forecasts=forecasts.rename(columns={'Sessions':'Forward sessions','GARCH forecast %':'GARCH annualized %'})
    realized=pd.DataFrame([{'Trailing sessions':h,'Yang–Zhang RV %':rv_snapshot(frame,h)['yang_zhang_rv']*100} for h in horizons])
    return forecasts,realized,annual_ewma,diag


def horizon_grid(forecasts,realized,annual_ewma):
    """One annualized scale; YZ describes the preceding H sessions."""
    table=forecasts[['Forward sessions','GARCH annualized %']].rename(columns={
        'Forward sessions':'Days (trading)','GARCH annualized %':'GARCH projection %'})
    table['EWMA projection %']=annual_ewma
    yz=realized.set_index('Trailing sessions')['Yang–Zhang RV %']
    table['Yang–Zhang historical %']=table['Days (trading)'].map(yz)
    return table
