"""Reusable closed-trade reporting with explicit data exclusions."""
from volatility_lab.runtime.settings import data_dir as runtime_data_dir
from volatility_lab import PACKAGE_ROOT
import json,uuid
from datetime import datetime
from pathlib import Path
import numpy as np,pandas as pd,plotly.graph_objects as go
from plotly.subplots import make_subplots
STORE=runtime_data_dir()/'backtest_runs'

def save_run(settings,trades,skips,coverage):
 STORE.mkdir(parents=True,exist_ok=True)
 name=datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
 record={'provenance_schema':'captured-minute-v1','settings':settings,'coverage':coverage,'trades':json.loads(trades.to_json(orient='records')),'skips':json.loads(skips.to_json(orient='records'))}
 path=STORE/(name+'.json');temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
 try:
  temporary.write_text(json.dumps(record,indent=2),encoding='utf-8')
  temporary.replace(path)
 finally:temporary.unlink(missing_ok=True)
 return name
