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


def fit_candidates(frame,horizons=(5,10,20,30,60)):
    """Fits candidate research models (GJR-GARCH asymmetric leverage and GARCH-t)."""
    from arch import arch_model
    from vol_lib import validate_ohlc
    clean=validate_ohlc(frame)
    returns=100*np.log(clean.Close).diff().dropna()
    results={}
    for name,o,dist in [('GJR-GARCH',1,'normal'),('GARCH-t',0,'t')]:
        try:
            fit=arch_model(returns,mean='Zero',vol='GARCH',p=1,o=o,q=1,dist=dist,rescale=False).fit(disp='off',options={'maxiter':500})
            if not fit.convergence_flag:
                variance=fit.forecast(horizon=max(horizons),reindex=False).variance.iloc[-1].to_numpy()/10000
                for h in horizons:
                    ann=float(np.sqrt(max(0.,variance[:h].mean())*252)*100)
                    results[(name,h)]=ann
        except Exception:
            pass
    return results


def horizon_grid(forecasts,realized,annual_ewma,candidates=None):
    """One annualized scale; YZ describes the preceding H sessions."""
    table=forecasts[['Forward sessions','GARCH annualized %']].rename(columns={
        'Forward sessions':'Days (trading)','GARCH annualized %':'GARCH projection %'})
    if candidates:
        for name in ['GJR-GARCH','GARCH-t']:
            col=[candidates.get((name,int(h))) for h in table['Days (trading)']]
            if any(v is not None for v in col):
                table[f'{name} projection %']=col
    table['EWMA projection %']=annual_ewma
    yz=realized.set_index('Trailing sessions')['Yang–Zhang RV %']
    table['Yang–Zhang historical %']=table['Days (trading)'].map(yz)
    return table
