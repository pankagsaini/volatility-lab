"""Explicit reproducible research run, never triggered by periodic refresh."""
from volatility_lab import PACKAGE_ROOT
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
os.environ.setdefault('OMP_NUM_THREADS','1')
from pathlib import Path
from datetime import datetime,timezone
import hashlib
import json
import uuid
import importlib.metadata
import pandas as pd
from forecast_validation import ValidationConfig,walk_forward,score
from experimental_models import candidate_forecasts,ALL_MODELS,VERSION
from volatility_regime import regime_input


def run(root,symbol,progress=None):
    source=regime_input(root/'data',symbol);raw=source.read_bytes()
    folder=root/'data'/'experiments'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+symbol+'_'+uuid.uuid4().hex[:6])
    folder.mkdir(parents=True);(folder/'input.csv').write_bytes(raw)
    metadata={'status':'running','symbol':symbol,'version':VERSION,'seed':417,'simulations':2048,
        'training':'Rolling calendar year, minimum 200 returns','horizons':[5,10,20,30],
        'input_sha256':hashlib.sha256(raw).hexdigest(),'models':ALL_MODELS,
        'packages':{name:importlib.metadata.version(name) for name in ['numpy','pandas','arch']},
        'code_sha256':{n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in
            ['experimental_models.py','forecast_validation.py','forecast.py','vol_lib.py']},
        'promotion':'None: research comparisons only; no independent untouched holdout yet.'}
    def save():(folder/'metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    save()
    try:
        frame=pd.read_csv(folder/'input.csv')
        rows=walk_forward(frame,symbol,ValidationConfig(),progress,predictor=candidate_forecasts,models=ALL_MODELS)
        if rows.empty:raise ValueError('Insufficient source history')
        rows.to_csv(folder/'forecasts.csv',index=False)
        score(rows,models=ALL_MODELS).to_csv(folder/'metrics.csv',index=False)
        # Pairwise baseline comparisons avoid one failing candidate suppressing all evidence.
        pairs=[]
        for model in ALL_MODELS[1:]:
            pair=score(rows[rows.model.isin(['GARCH',model])],models=('GARCH',model))
            pair['Comparison']=model;pairs.append(pair)
        pd.concat(pairs).to_csv(folder/'pairwise.csv',index=False)
        metadata['status']='complete';save()
        return folder
    except Exception as error:
        metadata.update(status='failed',error=str(error));save();raise


if __name__=='__main__':
    root=PACKAGE_ROOT
    for symbol in ['NIFTY','BANKNIFTY']:
        print(run(root,symbol),flush=True)
