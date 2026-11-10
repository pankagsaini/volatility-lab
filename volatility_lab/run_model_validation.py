"""One-year out-of-sample origins, independently refitted rolling-year models."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json
import pandas as pd
from forecast_validation import ValidationConfig,walk_forward,score,forecast_origin
from vol_lib import rv_snapshot

MODELS=('GARCH','EWMA','Yang–Zhang baseline')

def predict(training,config):
    result=forecast_origin(training,config)
    for h in config.horizons:
        yz=rv_snapshot(training,h)['yang_zhang_rv']
        result['Yang–Zhang baseline',h]=(yz**2/config.annualization,'Trailing-H-session YZ held constant; historical baseline, not fitted forward model')
    return result

def run(symbol,years=None):
    from monitor_ui import full_price_history
    from collector_support import atomic_json
    root=Path(__file__).resolve().parent
    frame=full_price_history(symbol)
    end=pd.Timestamp(frame.Date.max())
    start=end-pd.DateOffset(years=years) if years else pd.Timestamp(frame.Date.min())+pd.DateOffset(years=1)
    config=ValidationConfig(horizons=(5,10,20,30,60))
    folder=root/'data'/'validation'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'_'+symbol)
    folder.mkdir(parents=True)
    raw=frame.to_csv(index=False).encode();(folder/'input.csv').write_bytes(raw)
    meta={'status':'running','symbol':symbol,'evaluation_start':str(start.date()),'data_through':str(end.date()),
          'training':'Rolling calendar year, minimum 200 returns; no future observations',
          'target':'Mean future squared daily log returns; annualized at 252',
          'models':MODELS,'horizons':config.horizons,'input_sha256':hashlib.sha256(raw).hexdigest(),
          'code_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in
                         ['forecast.py','forecast_validation.py','vol_lib.py','run_model_validation.py']}}
    atomic_json(folder/'metadata.json',meta)
    try:
        rows=walk_forward(frame,symbol,config,predictor=predict,models=MODELS,origin_start=start)
        if rows.empty:raise ValueError('No eligible forecast origins')
        rows.to_csv(folder/'forecasts.csv',index=False)
        metrics=score(rows,models=MODELS);metrics.to_csv(folder/'metrics.csv',index=False)
        meta.update(status='complete',origins=int(rows.origin.nunique()),pending_rows=int((~rows.matured).sum()),
                    failed_rows=int(rows.status.ne('ok').sum()))
        atomic_json(folder/'metadata.json',meta)
        return str(folder),metrics[['horizon','model','sample','scored','mae_pp']].to_dict('records')
    except Exception as error:
        meta.update(status='failed',error=type(error).__name__);atomic_json(folder/'metadata.json',meta);raise

if __name__=='__main__':
    for symbol in ('NIFTY','BANKNIFTY'):
        folder,metrics=run(symbol);print(symbol,folder,flush=True)
        print(json.dumps([m for m in metrics if m['sample']=='daily_overlapping'],indent=2),flush=True)
