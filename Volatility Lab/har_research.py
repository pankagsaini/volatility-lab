"""HAR evaluation requires genuine daily realized variance and availability times."""
import io
import numpy as np
import pandas as pd
from experimental_models import har_forecast


def read_rv(raw):
    frame=pd.read_csv(io.BytesIO(raw))
    if not {'Date','RV','AvailableAt','Source'}.issubset(frame):
        raise ValueError('Requires Date, RV, AvailableAt, Source. RV is decimal daily variance from intraday returns.')
    frame['Date']=pd.to_datetime(frame.Date).dt.normalize()
    if frame.Date.isna().any() or frame.Date.duplicated().any():raise ValueError('Unique daily dates required')
    for value in frame.AvailableAt:
        stamp=pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None:raise ValueError('AvailableAt requires a timezone')
    frame['AvailableAt']=pd.to_datetime(frame.AvailableAt,utc=True)
    frame['RV']=pd.to_numeric(frame.RV)
    if not np.isfinite(frame.RV).all() or (frame.RV<0).any() or frame.Source.isna().any():raise ValueError('Invalid RV or source')
    earliest=frame.Date.dt.tz_localize('Asia/Kolkata')+pd.Timedelta(hours=15,minutes=40)
    if (frame.AvailableAt<earliest).any():raise ValueError('Full-session RV cannot be available before the session closes')
    return frame.sort_values('Date').reset_index(drop=True)


def evaluate(frame,horizons=(5,10,20,30)):
    rows=[]
    for i in range(len(frame)):
        origin=frame.Date.iloc[i]
        if origin<frame.Date.iloc[0]+pd.DateOffset(years=1):continue
        decision=frame.AvailableAt.iloc[i]
        # Do not skip unavailable observations then pretend they are consecutive.
        train=frame[(frame.Date>origin-pd.DateOffset(years=1))&(frame.Date<=origin)]
        if len(train)<200 or (train.AvailableAt>decision).any():continue
        for h in horizons:
            if i+h>=len(frame):continue
            actual=float(frame.RV.iloc[i+1:i+h+1].mean())
            if decision>=frame.AvailableAt.iloc[i+1:i+h+1].min():continue
            try:prediction=har_forecast(train.RV.to_numpy(),h);status='ok'
            except ValueError as error:prediction=np.nan;status=str(error)
            for model,value in [('HAR-RV',prediction),('RV30 mean',float(train.RV.tail(30).mean()))]:
                rows.append({'Origin':str(origin.date()),'Horizon':h,'Model':model,'Forecast variance':value,
                    'Realized variance':actual,'Status':status if model=='HAR-RV' else 'ok'})
    return pd.DataFrame(rows)


def har_components_summary(frame):
    from vol_lib import validate_ohlc
    clean=validate_ohlc(frame)
    log_ret=np.log(clean.Close/clean.Close.shift(1)).dropna()
    daily_var=(log_ret**2)
    rv_day=np.sqrt(daily_var*252)*100
    rv_week=np.sqrt(daily_var.rolling(5).mean()*252)*100
    rv_month=np.sqrt(daily_var.rolling(22).mean()*252)*100
    dates=clean.index[1:]
    date_str=dates.strftime('%Y-%m-%d') if hasattr(dates,'strftime') else dates.astype(str)
    df=pd.DataFrame({
        'Date':date_str,
        'Daily RV %':rv_day,
        'Weekly (5d) RV %':rv_week,
        'Monthly (22d) RV %':rv_month
    }).dropna()
    return df
