"""Research candidates only. No automatic promotion to live forecasts."""
import warnings
import numpy as np
import pandas as pd
from forecast_validation import forecast_origin,MODELS
from vol_lib import validate_ohlc

VERSION='candidates-v1'
CANDIDATES=('GJR-GARCH','EGARCH','GARCH-t','Ensemble')
ALL_MODELS=(*MODELS,*CANDIDATES)


def candidate_forecasts(training,config):
    from arch import arch_model
    result=forecast_origin(training,config)
    returns=100*np.log(validate_ohlc(training).Close).diff().dropna()
    for name,vol,o,dist in [('GJR-GARCH','GARCH',1,'normal'),('EGARCH','EGARCH',1,'normal'),('GARCH-t','GARCH',0,'t')]:
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                fit=arch_model(returns,mean='Zero',vol=vol,p=1,o=o,q=1,dist=dist,rescale=False).fit(disp='off',options={'maxiter':1000})
                if fit.convergence_flag:raise ValueError('Optimizer did not converge')
                kwargs={} if vol!='EGARCH' else dict(method='simulation',simulations=2048,rng=np.random.default_rng(417).standard_normal)
                variance=fit.forecast(horizon=max(config.horizons),reindex=False,**kwargs).variance.iloc[-1].to_numpy()/10000
            if not np.isfinite(variance).all() or (variance<=0).any():raise ValueError('Invalid forecast variance')
            note='; '.join(sorted(set(type(w.message).__name__ for w in caught)))
            for h in config.horizons:result[name,h]=(float(variance[:h].mean()),note)
        except (ValueError,RuntimeError,FloatingPointError,OverflowError) as error:
            for h in config.horizons:result[name,h]=(np.nan,str(error))
    for h in config.horizons:
        values=[result[m,h][0] for m in MODELS]
        result['Ensemble',h]=(float(np.mean(values)) if np.isfinite(values).all() else np.nan,
                              'Equal daily variance weights: baseline GARCH, EWMA, HV30')
    return result


def har_forecast(realized,horizon):
    """Direct HAR-RV regression; input is completed observed daily RV, not OHLC.

    Regress future mean RV on current RV and trailing 5/22-observation means.
    Training outcomes must all finish inside this supplied training sample.
    """
    values=pd.Series(realized,dtype=float)
    if type(horizon) is not int or not 1<=horizon<=252:raise ValueError('Invalid HAR horizon')
    if len(values)<200 or not np.isfinite(values).all() or (values<0).any():
        raise ValueError('HAR requires at least 200 finite nonnegative daily realized variances.')
    features=pd.DataFrame({'rv':values,'week':values.rolling(5).mean(),'month':values.rolling(22).mean()})
    target=pd.concat([values.shift(-j) for j in range(1,horizon+1)],axis=1).mean(axis=1,skipna=False)
    usable=features.notna().all(axis=1)&target.notna()
    x=np.column_stack([np.ones(usable.sum()),features[usable].to_numpy()])
    if len(x)<100:raise ValueError('Insufficient matured HAR training outcomes')
    coefficients,_,rank,_=np.linalg.lstsq(x,target[usable].to_numpy(),rcond=None)
    if rank<4:raise ValueError('Rank-deficient HAR training data')
    predicted=float(np.r_[1.,features.iloc[-1].to_numpy()]@coefficients)
    if not np.isfinite(predicted) or predicted<=0:raise ValueError('Nonpositive HAR forecast withheld')
    return predicted
