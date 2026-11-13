"""Matched EOD comparison selection; never substitute today's IV into an old forecast."""
import json,sqlite3
from contextlib import closing
import numpy as np
import pandas as pd
from expiry_forecast import forecast_restriction

def comparison_snapshot(path,symbol,selected):
    def accepted(row):
        try:
            return (np.isfinite(float(row.get('IV'))) and float(row['IV'])>0
                    and not forecast_restriction(row['Expiry'],row['As of']))
        except (TypeError,ValueError,KeyError):return False
    if accepted(selected):return selected,False
    if not selected.get('Expiry') or not selected.get('As of'):raise ValueError('No dated expiry snapshot selected')
    limit=pd.Timestamp(selected['As of'])
    with closing(sqlite3.connect(path)) as con:
        rows=con.execute('SELECT payload FROM intraday_observations WHERE symbol=? AND expiry=? ORDER BY as_of DESC',
                         (symbol,selected['Expiry'])).fetchall()
    for payload, in rows:
        row=json.loads(payload)
        try:stamp=pd.Timestamp(row['As of'])
        except (ValueError,KeyError):continue
        if stamp>limit or (limit-stamp).total_seconds()>7*86400:continue
        if row.get('Source')!=selected.get('Source'):continue
        if selected.get('Config') and row.get('Config')!=selected.get('Config'):continue
        if accepted(row):return row,True
    raise ValueError('No accepted end-of-day IV for this expiry in the past seven days. Live comparison stays withheld.')
