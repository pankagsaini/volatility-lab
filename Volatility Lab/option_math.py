"""European spot Black-Scholes with a configurable continuous dividend yield."""
import numpy as np
import pandas as pd
from scipy.special import ndtr


def price_delta(spot, strike, years, rate, vol, kind, dividend=0.):
    if kind not in ('CE','PE') or min(spot,strike,years,vol)<=0:
        raise ValueError('Invalid option inputs.')
    d1=(np.log(spot/strike)+(rate-dividend+.5*vol*vol)*years)/(vol*np.sqrt(years))
    d2=d1-vol*np.sqrt(years)
    s=spot*np.exp(-dividend*years); k=strike*np.exp(-rate*years)
    if kind=='CE':
        return s*ndtr(d1)-k*ndtr(d2), np.exp(-dividend*years)*ndtr(d1)
    return k*ndtr(-d2)-s*ndtr(-d1), np.exp(-dividend*years)*(ndtr(d1)-1)


def delta_iv(chain, kind, target):
    side=chain[chain.option_type==kind].copy()
    for column in ('iv','delta'):
        side[column]=pd.to_numeric(side[column],errors='coerce').astype(float)
    side=side[np.isfinite(side.iv)&np.isfinite(side.delta)&(side.iv>0)]
    side=side[side.delta.between(0,1) if kind=='CE' else side.delta.between(-1,0)]
    if side.empty:return np.nan
    side['abs_delta']=side.delta.abs()
    side=side.dropna(subset=['abs_delta','iv']).groupby('abs_delta',as_index=False).iv.mean().sort_values('abs_delta')
    exact=side[np.isclose(side.abs_delta,target,rtol=0,atol=1e-12)]
    if len(exact):return float(exact.iv.iloc[0])
    if len(side)<2 or not side.abs_delta.min()<=target<=side.abs_delta.max():
        return np.nan
    return float(np.interp(target,side.abs_delta,side.iv))


def skew_metrics(chain):
    put=delta_iv(chain,'PE',.25); call=delta_iv(chain,'CE',.25)
    proxy=(delta_iv(chain,'PE',.5)+delta_iv(chain,'CE',.5))/2
    return {'50-delta IV proxy %':proxy*100,'25D put IV %':put*100,'25D call IV %':call*100,
            'Put-minus-call RR (pp)':(put-call)*100,'Butterfly (pp)':((put+call)/2-proxy)*100}


def reversal_values(chain, symbol):
    """Calculate put-minus-call reversal from accepted 25-delta IVs."""
    chain=chain.copy()
    for column in ('iv','delta','strike'):
        chain[column]=pd.to_numeric(chain[column],errors='coerce').astype(float)
    chain=chain[np.isfinite(chain.iv)&np.isfinite(chain.delta)&np.isfinite(chain.strike)&(chain.iv>0)]
    legs={}; used={}
    for kind in ('PE','CE'):
        value=delta_iv(chain,kind,.25); actual=.25
        legs[kind]=value;used[kind]=actual if np.isfinite(value) else np.nan
    complete=all(np.isfinite(v) for v in legs.values())
    status='25-delta interpolation' if complete else 'Unavailable: accepted quotes do not cover 25D'
    return {'25 Delta reversal':100*(legs['PE']-legs['CE']), 'Reversal method':status,
            'Reversal put delta':100*used['PE'],'Reversal call delta':100*used['CE']}
